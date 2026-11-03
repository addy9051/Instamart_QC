import base64
import importlib.util
import io
import time

import numpy as np
import pytest
from fastapi import HTTPException
from PIL import Image

from insta_audit.api.guards import InferenceRunner
from insta_audit.quality.freshness_eval import (
    checkpoint_score,
    commodity_of,
    expected_calibration_error,
    fbeta,
    fit_temperature,
    group_key,
    softmax,
)
from insta_audit.quality.freshness_policy import (
    CommodityPrototypes,
    FreshnessPolicy,
    decide_freshness,
)
from insta_audit.sensors.load_cell import (
    BagScale,
    LoadCellCalibration,
    StabilityDetector,
    drift_check,
)
from insta_audit.vision.compat import check_compatibility
from insta_audit.vision.onnx_embedder import OnnxEmbedder, preprocess
from insta_audit.vision.tracking import (
    BagMouthTracker,
    Detection,
    DropCrossing,
    EventFuser,
    PolygonROI,
)

# ---- C20: checkpoint selection ----------------------------------------------------------------


def test_recall_alone_prefers_always_rotten_but_f2_does_not():
    always_rotten = (0.34, 1.0)  # precision, recall
    balanced = (0.92, 0.9)
    assert checkpoint_score("recall", *always_rotten) > checkpoint_score("recall", *balanced)
    assert checkpoint_score("f2", *balanced) > checkpoint_score("f2", *always_rotten)
    assert checkpoint_score("recall_at_precision", *balanced, min_precision=0.9) > checkpoint_score(
        "recall_at_precision", *always_rotten, min_precision=0.9)
    assert fbeta(0, 0) == 0.0
    with pytest.raises(ValueError):
        checkpoint_score("accuracy", 1, 1)


# ---- C17: item-grouped splits ----------------------------------------------------------------


def test_group_key_uses_item_regex_else_filename_fallback():
    pattern = r"(day\d+_item\d+)"
    a = group_key("Fresh Apple/day1_item3/img_001.jpg", pattern, "fallback-a")
    b = group_key("Fresh Apple/day1_item3/img_002.jpg", pattern, "fallback-b")
    c = group_key("Fresh Apple/day2_item3/img_001.jpg", pattern, "fallback-c")
    assert a == b == "day1_item3" and c != a
    assert group_key("no/match/here.jpg", pattern, "fallback") == "fallback"
    assert group_key("x.jpg", None, "fallback") == "fallback"


def test_commodity_of_depth_and_regex():
    assert commodity_of("Fresh Apple/Apple/img.jpg", depth=1) == "apple"
    assert commodity_of("img.jpg", depth=1) == "unknown"
    assert commodity_of("a/b/c.jpg", 1, r"/(b)/") == "b"


# ---- C18/C19: calibration, policy, OOD gate --------------------------------------------------


def test_temperature_scaling_reduces_overconfidence():
    rng = np.random.default_rng(0)
    labels = rng.integers(0, 2, 600)
    signal = np.where(labels == 1, 1.0, -1.0)
    logits = np.stack([-(signal * 3 + rng.normal(0, 3, 600)), signal * 3 + rng.normal(0, 3, 600)], axis=1)
    temperature = fit_temperature(logits, labels)
    assert temperature > 1.0  # overconfident logits need softening
    before = expected_calibration_error(softmax(logits), labels)
    after = expected_calibration_error(softmax(logits, temperature), labels)
    assert after < before
    assert fit_temperature(np.zeros((0, 2)), np.zeros(0)) == 1.0


def test_decide_freshness_maps_classes_to_actions_with_cost_threshold():
    policy = FreshnessPolicy(cost_missed_rotten=5, cost_false_reject=1)
    assert policy.review_rotten_probability == pytest.approx(1 / 6)
    assert decide_freshness({"fresh": 0.95, "semi_fresh": 0.03, "rotten": 0.02}, policy)["action"] == "accept"
    assert decide_freshness({"fresh": 0.1, "semi_fresh": 0.1, "rotten": 0.8}, policy)["action"] == "reject"
    # 25% rotten is below the reject bar but above the cost-based review bar: not silently accepted.
    maybe = decide_freshness({"fresh": 0.7, "semi_fresh": 0.05, "rotten": 0.25}, policy)
    assert maybe["decision"] == "review_required" and maybe["reason"] == "freshness_possible_spoilage"
    discount = decide_freshness({"fresh": 0.2, "semi_fresh": 0.7, "rotten": 0.1}, policy)
    assert discount["decision"] == "hold" and discount["action"] == "discount"
    assert decide_freshness({"fresh": 0.45, "semi_fresh": 0.4, "rotten": 0.05}, policy)["action"] == "review"
    assert decide_freshness({"fresh": 0.1, "spoiled": 0.9}, policy)["action"] == "reject"


def test_commodity_gate_rejects_other_objects_and_wrong_commodity():
    protos = CommodityPrototypes(["apple", "tomato"], np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float32))
    assert protos.check([1, 0.05, 0], "apple", 0.35)["status"] == "ok"
    assert protos.check([0, 0, 1], "apple", 0.35)["status"] == "out_of_distribution"  # e.g. a chips packet
    assert protos.check([0, 1, 0], "apple", 0.35)["status"] == "commodity_mismatch"
    assert protos.check([0, 1, 0], "unlisted-sku", 0.35)["status"] == "ok"  # no routing, just OOD gate
    assert CommodityPrototypes.load(None) is None and CommodityPrototypes.load("missing.npz") is None


# ---- C21: load cell --------------------------------------------------------------------------


def test_multipoint_calibration_signed_readings_and_drift():
    points = [(8000, 0.0), (10000, 200.0), (14000, 600.0), (18000, 1000.0)]
    cal = LoadCellCalibration.from_points(points)
    assert cal.grams_per_count == pytest.approx(0.1, rel=1e-6) and cal.max_residual_g < 1e-6
    assert cal.to_grams(7900) == pytest.approx(-10.0)  # signed: an item lifted out is visible
    assert drift_check(cal, 10005, 200, tolerance_g=1.0)["ok"] is True
    assert drift_check(cal, 10100, 200, tolerance_g=1.0)["ok"] is False
    with pytest.raises(ValueError):
        LoadCellCalibration.from_points([(1, 0.0)])
    with pytest.raises(ValueError):
        LoadCellCalibration.from_points([(5, 0.0), (5, 10.0)])
    with pytest.raises(ValueError):
        LoadCellCalibration.from_points([(5, 100.0), (10, 0.0)])


def test_stability_window_tare_and_signed_steps():
    scale = BagScale(StabilityDetector(window_s=0.3, sigma_g=1.0, min_samples=5))
    for i in range(10):  # empty bag settles at 20 g
        scale.push(i * 0.05, 20.0 + (0.2 if i % 2 else -0.2))
    assert scale.tare() == pytest.approx(20.0, abs=0.5)
    scale.detector.reset()
    scale.push(1.0, 20.0)
    scale.push(1.05, 95.0)  # item landing: still moving, not settled
    assert scale.detector.settled is False and scale.net_g is None and scale.step() is None
    for i in range(14):
        scale.push(1.1 + i * 0.05, 80.0)
    assert scale.net_g == pytest.approx(60.0, abs=0.5)
    assert scale.step() == pytest.approx(60.0, abs=0.5)
    for i in range(14):
        scale.push(2.0 + i * 0.05, 50.0)
    assert scale.step() == pytest.approx(-30.0, abs=0.5)  # item taken back out: negative delta preserved
    with pytest.raises(ValueError):
        BagScale().tare()


# ---- C22: polygon ROI, fusion -----------------------------------------------------------------


def test_polygon_roi_and_hand_suppression():
    roi = PolygonROI([(0, 50), (100, 50), (100, 100), (0, 100)])
    assert roi.contains(50, 75) and not roi.contains(50, 10)
    with pytest.raises(ValueError):
        PolygonROI([(0, 0), (1, 1)])
    tracker = BagMouthTracker(roi, cooldown_frames=2)
    assert tracker.update({1: Detection(50, 20), 2: Detection(10, 20, "hand")}) == []
    events = tracker.update({1: Detection(50, 70), 2: Detection(10, 70, "hand")})
    assert [(e.track_id, e.direction) for e in events] == [(1, "into_bag")]  # the hand never counts
    assert tracker.update({3: Detection(50, 70, confidence=0.1)}) == []  # low confidence ignored
    tracker.update({1: Detection(50, 70)})
    tracker.update({1: Detection(50, 70)})
    out = tracker.update({1: Detection(50, 10)})
    assert [e.direction for e in out] == ["out_of_bag"]
    assert tracker.update({9: (50, 70)}) == []  # tuple input is accepted; first sight is not a crossing


def test_ray_trigger_is_not_y_only():
    diagonal = PolygonROI([(0, 0), (100, 0), (0, 100)])
    assert diagonal.contains(10, 10) and not diagonal.contains(90, 90)


def test_fuser_requires_a_scale_step_and_subtracts_removals():
    fuser = EventFuser(window_s=1.0, min_step_g=3.0)
    crossing = DropCrossing(1, 0, 0, "into_bag")
    fuser.add_crossing(0.0, crossing)
    confirmed = fuser.add_scale_step(0.4, 60.0)
    assert len(confirmed) == 1 and confirmed[0].direction == "into_bag" and fuser.net_count == 1

    fuser.add_crossing(2.0, DropCrossing(7, 0, 0, "into_bag"))  # a hand/ID switch: no weight change
    assert fuser.expire(3.5)[0].track_id == 7 and fuser.net_count == 1 and fuser.unconfirmed == 1

    fuser.add_crossing(4.0, DropCrossing(1, 0, 0, "out_of_bag"))
    assert fuser.add_scale_step(4.2, -58.0)[0].direction == "out_of_bag" and fuser.net_count == 0
    assert fuser.add_scale_step(5.0, 1.0) == []  # below the minimum step
    assert fuser.add_scale_step(6.0, 40.0) == [] and fuser.unmatched_steps == 1
    fuser.add_crossing(7.0, DropCrossing(2, 0, 0, "into_bag"))
    assert fuser.add_scale_step(7.1, -40.0) == []  # wrong sign does not confirm an into_bag crossing


# ---- C10: compatibility --------------------------------------------------------------------


def test_compatibility_reports_dimension_and_model_mismatch():
    base = dict(bank_ready=True, embedder_ready=True, bank_dim=384, embedder_dim=384,
                bank_model="facebook/dinov2-small", embedder_model="/opt/hf/dinov2-small")
    assert check_compatibility(**base)["status"] == "ok"
    assert check_compatibility(**{**base, "embedder_dim": 768})["status"] == "mismatch"
    other = check_compatibility(**{**base, "embedder_model": "/opt/hf/dinov2-base"})
    assert other["status"] == "mismatch" and "model_mismatch" in other["reasons"][0]
    assert check_compatibility(**{**base, "embedder_ready": False})["status"] == "unchecked"
    assert check_compatibility(**{**base, "bank_model": None})["status"] == "ok"


# ---- C24: bounded inference + ONNX ------------------------------------------------------------


def test_inference_runner_times_out_and_backpressures():
    runner = InferenceRunner(workers=1, max_queue=0, wait_seconds=0.05, timeout_seconds=0.1)
    assert runner.run(lambda x: x + 1, 1) == 2
    with pytest.raises(HTTPException) as slow:
        runner.run(time.sleep, 0.4)
    assert slow.value.status_code == 504
    with pytest.raises(HTTPException) as busy:  # the timed-out call still holds the only slot
        runner.run(lambda: 1)
    assert busy.value.status_code == 503
    time.sleep(0.5)
    assert runner.run(lambda: "free again") == "free again"


def test_inference_runner_propagates_errors_and_releases_slots():
    runner = InferenceRunner(workers=1, max_queue=0, wait_seconds=0.2, timeout_seconds=1)

    def boom():
        raise RuntimeError("model crashed")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            runner.run(boom)
    assert runner.run(lambda: 1) == 1


class FakeSession:
    def __init__(self, dim=8):
        self.dim = dim
        self.seen = None

    class _Out:
        def __init__(self, dim):
            self.shape = ["batch", dim]

    def get_outputs(self):
        return [self._Out(self.dim)]

    def run(self, _names, feeds):
        self.seen = feeds["pixel_values"]
        return [np.arange(1, self.dim + 1, dtype=np.float32)[None] * 5.0]


def _b64_image(size=(300, 200)):
    out = io.BytesIO()
    Image.new("RGB", size, (200, 40, 40)).save(out, format="JPEG")
    return base64.b64encode(out.getvalue()).decode()


def test_onnx_embedder_preprocessing_and_normalisation():
    session = FakeSession()
    embedder = OnnxEmbedder(session=session)
    assert embedder.ready and embedder.dimension == 8
    vector = embedder.from_base64(_b64_image())
    assert session.seen.shape == (1, 3, 224, 224) and session.seen.dtype == np.float32
    assert np.linalg.norm(vector) == pytest.approx(1.0, abs=1e-5)
    assert embedder.from_base64("not-base64!!") is None and embedder.failures == 1
    assert OnnxEmbedder(None).ready is False and OnnxEmbedder(None).from_base64("x") is None
    pixels = preprocess(Image.new("RGB", (640, 480), (255, 255, 255)))
    assert pixels.shape == (1, 3, 224, 224) and pixels.max() == pytest.approx((1 - 0.406) / 0.225, abs=1e-3)


# ---- scripts --------------------------------------------------------------------------------


def test_import_weighings_sets_verified_only_with_enough_units():
    spec = importlib.util.spec_from_file_location("import_weighings", "scripts/import_weighings.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = [{"sku": "A", "batch": f"b{i % 2}", "weight_g": str(58 + (i % 3) * 0.5)} for i in range(20)]
    rows += [{"sku": "B", "batch": "b1", "weight_g": "100"} for _ in range(5)]
    summary = module.summarise(rows, min_units=20, min_batches=2)
    assert summary["A"]["gross_verified"] is True and summary["B"]["gross_verified"] is False
    catalog = {"skus": [{"sku": "A", "expected_unit_weight_g": 55}, {"sku": "B"}]}
    updated, unknown = module.apply_to_catalog(catalog, {**summary, "Z": summary["A"]})
    assert updated == ["A", "B"] and unknown == ["Z"]
    assert catalog["skus"][0]["expected_unit_weight_g"] == 55  # net weight untouched
    assert catalog["skus"][0]["gross_mean_g"] == pytest.approx(summary["A"]["gross_mean_g"])
    with pytest.raises(ValueError):
        module.summarise([{"sku": "A", "weight_g": "0"}], 1, 1)


def test_multipoint_calibration_cli_parses_points():
    spec = importlib.util.spec_from_file_location("calibrate_scale", "scripts/calibrate_scale.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.parse_points("8120:0,10480:200") == [(8120.0, 0.0), (10480.0, 200.0)]
