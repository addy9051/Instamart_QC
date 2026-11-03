from datetime import date
from pathlib import Path

import numpy as np
import pytest
import yaml

from insta_audit.catalog import nominal_weight_g
from insta_audit.decision import audit_drop, reconcile_order
from insta_audit.quality.expiry import (
    evaluate_expiry,
    normalize_category,
    parse_expiry,
    parse_expiry_details,
)
from insta_audit.seal import ScaleSpec
from insta_audit.versioning import build_versions, file_digest
from insta_audit.vision.matching import MatchPolicy, classify_match
from insta_audit.vision.sku_bank import SkuBank

SCALE = ScaleSpec(resolution_g=1.0, sigma_g=1.0)
POLICY = MatchPolicy(min_similarity=0.78, margin=0.03, lookalike_margin=0.06)


def _entry(mean, brand, verified=True):
    return {"brand": brand, "gross_mean_g": mean, "gross_std_g": 1.0, "gross_verified": verified,
            "unit_weight_g": None}


CATALOG = {
    "A": _entry(52, "Acme"),
    "B": _entry(52, "Other"),
    "A2": _entry(90, "Acme"),  # same-brand lookalike of A, but a clearly different weight
    "A3": _entry(52, "Acme"),  # same-brand lookalike with the SAME weight
    "NET": {"brand": "Net", "unit_weight_g": 50, "gross_verified": False},
}


@pytest.fixture
def bank(tmp_path):
    path = tmp_path / "bank.npz"
    np.savez(path, skus=np.array(["A", "B", "A2"]),
             embeddings=np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0.95, 0.31, 0, 0]], dtype=np.float32))
    return SkuBank(path)


@pytest.fixture
def plain_bank(tmp_path):
    path = tmp_path / "plain.npz"
    np.savez(path, skus=np.array(["A", "B"]), embeddings=np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float32))
    return SkuBank(path)


@pytest.fixture
def twin_bank(tmp_path):
    """A and A3 look alike AND weigh the same, so weight cannot break the tie."""
    path = tmp_path / "twin.npz"
    np.savez(path, skus=np.array(["A", "A3"]), embeddings=np.array([[1, 0, 0, 0], [0.96, 0.28, 0, 0]], dtype=np.float32))
    return SkuBank(path)


def payload(**kw):
    base = {"event_id": "e", "order_id": "o", "expected_sku": "A", "expected_quantity": 1,
            "measured_weight_delta_g": 52, "visual_embedding": [1, 0, 0, 0]}
    return {**base, **kw}


def run(bank, **kw):
    return audit_drop(payload(**kw), bank, CATALOG, SCALE, POLICY)


def test_pass(plain_bank):
    r = run(plain_bank)
    assert r["decision"] == "pass" and r["match_status"] == "confident"


def test_same_weight_variant_swap_caught_by_vision(bank):
    r = run(bank, visual_embedding=[0, 1, 0, 0])
    assert r["decision"] == "hold" and "sku_mismatch" in r["reason_codes"]


def test_missing_unit_inferred_from_weight_not_client(bank):
    r = run(bank, expected_quantity=2)
    assert r["decision"] == "hold" and "quantity_short" in r["reason_codes"] and r["inferred_quantity"] == 1


def test_extra_unit_inferred_from_weight(bank):
    r = run(bank, measured_weight_delta_g=104)
    assert "quantity_over" in r["reason_codes"] and r["inferred_quantity"] == 2


def test_weight_anomaly(bank):
    assert "weight_out_of_tolerance" in run(bank, measured_weight_delta_g=20)["reason_codes"]


def test_no_embedding_requires_review(bank):
    assert run(bank, visual_embedding=None)["decision"] == "review_required"


def test_dimension_mismatch_is_a_review_not_a_hold(bank):
    r = run(bank, visual_embedding=[1.0, 0.0])
    assert "embedding_dimension_mismatch" in r["reason_codes"] and r["decision"] == "review_required"


def test_unknown_item_is_never_forced_onto_nearest_sku(bank):
    r = run(bank, visual_embedding=[0, 0, 1, 0])
    assert r["match_status"] == "unknown" and r["decision"] == "review_required"
    assert "unknown_item" in r["reason_codes"]


def test_ambiguous_lookalike_resolved_by_weight(bank):
    r = run(bank, visual_embedding=[1, 0, 0, 0])
    assert r["match_status"] == "ambiguous" and r["resolved_by_weight"] is True and r["decision"] == "pass"


def test_ambiguous_lookalike_with_same_weight_is_review(twin_bank):
    r = run(twin_bank, visual_embedding=[1, 0, 0, 0])
    assert r["match_status"] == "ambiguous" and r["decision"] == "review_required"
    assert "visual_ambiguous_lookalike" in r["reason_codes"]


def test_net_only_weight_cannot_hold(bank):
    r = audit_drop(payload(expected_sku="NET", measured_weight_delta_g=20, visual_embedding=None),
                   bank, CATALOG, SCALE, POLICY)
    assert r["decision"] == "review_required" and "weight_out_of_tolerance_unverified" in r["reason_codes"]


def test_unknown_catalog_sku_and_unsettled_scale_are_reviews(bank):
    assert "catalog_weight_unavailable" in run(bank, expected_sku="MISSING")["reason_codes"]
    assert "scale_unsettled" in run(bank, settled=False)["reason_codes"]


def test_classify_match_margin_and_per_sku_threshold():
    topk = [("A", 0.9), ("B", 0.88)]
    assert classify_match(topk, POLICY)["status"] == "ambiguous"
    assert classify_match([("A", 0.9), ("B", 0.5)], POLICY)["status"] == "confident"
    strict = {"A": {"match_threshold": 0.95}}
    assert classify_match([("A", 0.9)], POLICY, strict, "A")["status"] == "unknown"
    assert classify_match([], POLICY)["status"] == "unavailable"


def test_bank_topk_is_deduplicated_per_sku(tmp_path):
    path = tmp_path / "multi.npz"
    np.savez(path, skus=np.array(["A", "A", "B"]),
             embeddings=np.array([[1, 0], [0.9, 0.4], [0, 1]], dtype=np.float32))
    top = SkuBank(path).match_topk([1, 0], k=3)
    assert [sku for sku, _ in top] == ["A", "B"]
    with pytest.raises(ValueError, match="Embedding dimension mismatch"):
        SkuBank(path).match([1.0, 0.0, 0.0])


def test_reconcile():
    assert reconcile_order({"A": 2}, {"A": 2})["decision"] == "pass"
    r = reconcile_order({"A": 2}, {"A": 1, "B": 1})
    assert r["decision"] == "hold" and set(r["differences"]) == {"A", "B"}


@pytest.mark.parametrize("text,expected", [("52 g", 52), ("500ml", 500), ("1 L", 1000), ("1.2 g", None), ("", None), ("4", None)])
def test_nominal_weight(text, expected):
    assert nominal_weight_g(text) == expected


def test_parse_expiry_mfg_and_exp_disambiguation():
    # MFG date appears first in text, followed by USE BY
    raw = "PKD ON 15/01/2026 USE BY 20/07/2026 BATCH 49B"
    res = parse_expiry_details(raw, today=date(2026, 2, 1))
    assert res["manufactured"] == date(2026, 1, 15)
    assert res["expiry"] == date(2026, 7, 20)
    assert parse_expiry(raw, today=date(2026, 2, 1)) == date(2026, 7, 20)


def test_parse_expiry_relative_shelf_life():
    raw = "MFG 10/01/2026 BEST BEFORE 6 MONTHS FROM MANUFACTURE"
    res = parse_expiry_details(raw, today=date(2026, 2, 1))
    assert res["manufactured"] == date(2026, 1, 10)
    assert res["expiry"] == date(2026, 7, 10)
    assert res["method"] == "relative_from_manufacture"


def test_parse_expiry_month_year_formats():
    raw = "EXP 05/27"
    assert parse_expiry(raw, today=date(2026, 1, 1)) == date(2027, 5, 31)

    raw_word = "BEST BEFORE DEC 2026"
    assert parse_expiry(raw_word, today=date(2026, 1, 1)) == date(2026, 12, 31)


def test_expiry_policy_life_basis():
    policy = {
        "categories": {"dairy": {"min_remaining_life_pct": 50, "reference_shelf_life_days": 10}},
        "skus": {"12345": {"reference_shelf_life_days": 20}},
    }
    # 1. Pack label basis (both mfg and exp provided)
    r = evaluate_expiry(
        received=date(2026, 6, 1),
        expiry=date(2026, 6, 11),
        category="dairy",
        policy=policy,
        manufactured=date(2026, 5, 22),
    )
    assert r["decision"] == "accept"
    assert r["life_basis"] == "pack_label"
    assert r["remaining_days"] == 10
    assert r["remaining_life_pct"] == 50.0

    # 2. SKU reference override
    r2 = evaluate_expiry(
        received=date(2026, 6, 1),
        expiry=date(2026, 6, 11),
        category="dairy",
        policy=policy,
        sku="12345",
    )
    assert r2["life_basis"] == "sku_reference"
    assert r2["remaining_life_pct"] == 50.0  # 10 days / 20 days

    # 3. Category reference
    r3 = evaluate_expiry(
        received=date(2026, 6, 1),
        expiry=date(2026, 6, 11),
        category="dairy",
        policy=policy,
    )
    assert r3["life_basis"] == "category_reference"
    assert r3["remaining_life_pct"] == 100.0  # 10 days / 10 days


def test_policy_covers_catalog_categories():
    policy_path = Path("configs/quality_policy.yaml")
    assert policy_path.is_file(), "quality_policy.yaml missing"
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy_categories = {normalize_category(c) for c in policy.get("categories", {})}

    for cat_path in [Path("data/catalog/skus.yaml"), Path("configs/skus.yaml")]:
        if cat_path.is_file():
            data = yaml.safe_load(cat_path.read_text(encoding="utf-8")) or {}
            for item in data.get("skus", []):
                cat = item.get("category")
                if cat:
                    normalized = normalize_category(cat)
                    assert normalized in policy_categories, (
                        f"Category '{cat}' (normalized: '{normalized}') for SKU '{item.get('sku')}' "
                        f"in {cat_path} is missing from quality_policy.yaml!"
                    )


def test_versioning_and_fingerprints(tmp_path):
    f1 = tmp_path / "model.ts"
    f1.write_bytes(b"model-weights-v1")
    digest1 = file_digest(f1)
    assert digest1 is not None and len(digest1) == 12

    f1.write_bytes(b"model-weights-v2")
    digest2 = file_digest(f1)
    assert digest2 != digest1

    versions = build_versions(
        sku_bank_path=f1,
        embedding_model_path=None,
        embedder_ready=False,
        freshness_model_path=f1,
        freshness_ready=True,
        policy_path=f1,
        policy={"version": 2},
        catalog_path=None,
        ocr_ready=False,
    )
    assert versions["sku_bank"] == digest2
    assert versions["freshness_model"] == digest2
    assert versions["quality_policy"].startswith("v2@")
