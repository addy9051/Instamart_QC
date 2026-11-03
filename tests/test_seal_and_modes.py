import pytest

from insta_audit.bag_audit import audit_bag
from insta_audit.catalog import load_catalog
from insta_audit.metrics import bootstrap_ci, exposure, miss_rate_at_false_hold_budget, order_report
from insta_audit.modes import Mode, ModePolicy, apply_mode
from insta_audit.orders import Order, OrderLine
from insta_audit.scan_check import reconcile_scan_vs_bag
from insta_audit.seal import ScaleSpec, check_seal_weight

SCALE = ScaleSpec(resolution_g=1.0, sigma_g=2.0)


def sku(mean, std=1.0, verified=True, **extra):
    return {"gross_mean_g": mean, "gross_std_g": std, "gross_verified": verified, "unit_weight_g": None, **extra}


CATALOG = {
    "chips": sku(60, 1.5),
    "salt": sku(1050, 6.0),
    "sachet": sku(2.5, 0.2),
    "net_only": {"unit_weight_g": 100, "gross_verified": False},
    "unknown_weight": {"unit_weight_g": None},
    "loose": {"min_weight_g": 900, "max_weight_g": 1100, "gross_verified": False},
}


def order(lines, scans=None):
    return Order(
        order_id="o1", store_id="s1",
        lines=[OrderLine(sku=s, quantity=q) for s, q in lines.items()], scans=scans,
    )


def test_seal_ok_and_short_and_over():
    expected = {"chips": 2, "salt": 1}
    ok = check_seal_weight(expected, CATALOG, 1170, SCALE)
    assert ok["status"] == "ok" and ok["confidence"] == "high"
    short = check_seal_weight(expected, CATALOG, 1170 - 60, SCALE)
    assert short["status"] == "short" and short["suspected"][0]["sku"] == "chips"
    over = check_seal_weight(expected, CATALOG, 1170 + 60, SCALE)
    assert over["status"] == "over"


def test_missing_item_is_not_hidden_in_a_heavy_order():
    """A missing 60 g pack must be caught even next to a 1 kg pack (no single 18% tolerance)."""
    result = check_seal_weight({"chips": 1, "salt": 1}, CATALOG, 1050 + 0, SCALE)
    assert result["status"] == "short"


def test_light_sku_is_uninformative_not_always_held():
    result = check_seal_weight({"sachet": 4}, CATALOG, 10, SCALE)
    assert result["status"] == "uninformative"
    mixed = check_seal_weight({"sachet": 4, "chips": 1}, CATALOG, 70, SCALE)
    assert mixed["status"] == "ok" and mixed["uninformative_skus"] == ["sachet"]


def test_net_weight_only_is_low_confidence_with_wide_band():
    result = check_seal_weight({"net_only": 1}, CATALOG, 112, SCALE)
    assert result["status"] == "ok" and result["confidence"] == "low" and result["basis"] == "nominal"
    assert check_seal_weight({"net_only": 1}, CATALOG, 60, SCALE)["confidence"] == "low"


def test_unknown_weight_and_unsettled_scale_give_no_verdict():
    assert check_seal_weight({"unknown_weight": 1}, CATALOG, 50, SCALE)["status"] == "uninformative"
    assert check_seal_weight({"chips": 1}, CATALOG, 50, SCALE, settled=False)["status"] == "unsettled"


def test_variable_weight_uses_range():
    assert check_seal_weight({"loose": 1}, CATALOG, 1000, SCALE)["status"] == "ok"
    assert check_seal_weight({"loose": 1}, CATALOG, 600, SCALE)["status"] == "short"


def test_inferred_count_for_single_sku_bag():
    assert check_seal_weight({"chips": 3}, CATALOG, 120, SCALE)["inferred_unit_count"] == 2


def test_scan_vs_bag_findings():
    ordered = {"a": 2, "b": 1}
    result = reconcile_scan_vs_bag(ordered, {"a": 2, "b": 1}, {"a": 1, "b": 1})
    assert result["issues"] == {"a": ["quantity_short", "scanned_not_bagged"]}
    twice = reconcile_scan_vs_bag(ordered, {"a": 2, "b": 1}, {"a": 2, "b": 2})
    assert "bagged_twice" in twice["issues"]["b"]
    assert reconcile_scan_vs_bag(ordered, None, None)["status"] == "unavailable"
    assert reconcile_scan_vs_bag(ordered, {"a": 2, "b": 1}, {"a": 2, "b": 1})["status"] == "ok"


def test_scan_vs_bag_skipped_across_bags():
    result = reconcile_scan_vs_bag({"a": 1}, {"a": 1, "b": 1}, {"a": 1}, single_bag=False)
    assert result["status"] == "ok"


def test_matrix_pass_review_and_hold():
    o = order({"chips": 2, "salt": 1}, scans={"chips": 2, "salt": 1})
    good = audit_bag(o, "bag-1", CATALOG, SCALE, 1170, True, {"chips": 2, "salt": 1})
    assert good["decision"] == "pass" and not good["confident"]

    weight_only = audit_bag(o, "bag-1", CATALOG, SCALE, 1110, True, {"chips": 2, "salt": 1})
    assert weight_only["decision"] == "review_required" and "weight_short" in weight_only["reason_codes"]

    count_only = audit_bag(o, "bag-1", CATALOG, SCALE, 1170, True, {"chips": 1, "salt": 1})
    assert count_only["decision"] == "review_required"

    agree = audit_bag(o, "bag-1", CATALOG, SCALE, 1110, True, {"chips": 1, "salt": 1})
    assert agree["decision"] == "hold" and agree["confident"]


def test_unverified_weight_never_gives_confident_hold():
    o = order({"net_only": 2}, scans={"net_only": 2})
    result = audit_bag(o, "bag-1", CATALOG, SCALE, 90, True, {"net_only": 1})
    assert result["decision"] == "review_required" and not result["confident"]
    assert "weight_basis_not_verified" in result["reason_codes"]


def test_no_evidence_is_review_not_pass():
    o = order({"chips": 1})
    result = audit_bag(o, "bag-1", CATALOG, SCALE, None, True, None)
    assert result["decision"] == "review_required"
    unsettled = audit_bag(o, "bag-1", CATALOG, SCALE, 60, False, None)
    assert unsettled["decision"] == "review_required"


def test_multi_bag_only_checks_its_own_lines():
    o = Order(order_id="o", store_id="s", lines=[
        OrderLine(sku="chips", quantity=1, bag_id="ambient"),
        OrderLine(sku="salt", quantity=1, bag_id="frozen"),
    ])
    assert audit_bag(o, "ambient", CATALOG, SCALE, 60, True, {"chips": 1})["decision"] == "pass"
    assert audit_bag(o, "frozen", CATALOG, SCALE, 1050, True, {"salt": 1})["decision"] == "pass"


def test_modes_never_block_unless_enforce_and_confident():
    assert apply_mode("hold", True, Mode.SHADOW) == "none"
    assert apply_mode("hold", True, Mode.OFF) == "none"
    assert apply_mode("hold", True, Mode.ADVISORY) == "notify"
    assert apply_mode("hold", False, Mode.ENFORCE) == "notify"
    assert apply_mode("review_required", True, Mode.ENFORCE) == "notify"
    assert apply_mode("hold", True, Mode.ENFORCE) == "block"
    assert apply_mode("pass", True, Mode.ENFORCE) == "none"


def test_mode_policy_overrides_kill_switch_and_enforce_gate():
    policy = ModePolicy(default_mode=Mode.SHADOW, stores={"s1": Mode.ENFORCE},
                        skus={"noisy": Mode.SHADOW}, allow_enforce=False)
    assert policy.resolve("s2") is Mode.SHADOW
    assert policy.resolve("s1") is Mode.ADVISORY  # enforce gated until allow_enforce
    policy.allow_enforce = True
    assert policy.resolve("s1") is Mode.ENFORCE
    assert policy.resolve("s1", ["noisy"]) is Mode.SHADOW  # SKU overrides only lower the mode
    policy.set_kill_switch(True)
    assert policy.resolve("s1") is Mode.OFF


def test_mode_parse_rejects_garbage():
    with pytest.raises(ValueError):
        Mode.parse("block")


def test_catalog_loads_gross_fields_and_rejects_bad_ranges(tmp_path):
    path = tmp_path / "skus.yaml"
    path.write_text(
        "skus:\n- {sku: a, gross_mean_g: 60, gross_std_g: 1.5, gross_verified: true}\n"
        "- {sku: b, expected_unit_weight_g: 100, weight_verified: true}\n",
        encoding="utf-8",
    )
    catalog = load_catalog(path)
    assert catalog["a"]["gross_verified"] is True and catalog["a"]["gross_std_g"] == 1.5
    # a verified NET weight must not be mistaken for a verified gross weight
    assert catalog["b"]["gross_verified"] is False
    path.write_text("skus:\n- {sku: c, min_weight_g: 10, max_weight_g: 5}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_catalog(path)


def test_order_report_gate_uses_upper_bound():
    clean_ok = [{"has_error": False, "decision": "pass"}] * 300
    report = order_report(clean_ok + [{"has_error": True, "decision": "hold"}] * 10)
    assert report["false_hold_rate"]["rate"] == 0.0
    assert report["gate"]["passed"] is True
    bad = order_report([{"has_error": False, "decision": "hold"}] * 10 + [{"has_error": False, "decision": "pass"}] * 90)
    assert bad["false_hold_rate"]["rate"] == 0.1 and bad["gate"]["passed"] is False


def test_per_order_exposure_matches_review_math():
    assert exposure(0.148, 8) == pytest.approx(0.722, abs=0.001)
    assert bootstrap_ci([]) is None


def test_miss_rate_at_budget():
    scored = [(False, s / 100) for s in range(100)] + [(True, 0.5), (True, 0.995)]
    assert miss_rate_at_false_hold_budget(scored, 0.01) == 0.5
