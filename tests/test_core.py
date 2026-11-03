from datetime import date
from pathlib import Path

import numpy as np
import pytest
import yaml

from insta_audit.catalog import load_catalog, nominal_weight_g
from insta_audit.decision import audit_drop, reconcile_order
from insta_audit.quality.expiry import (
    evaluate_expiry,
    normalize_category,
    parse_expiry,
    parse_expiry_details,
)
from insta_audit.versioning import build_versions, file_digest
from insta_audit.vision.sku_bank import SkuBank


@pytest.fixture
def bank(tmp_path):
    path = tmp_path / "bank.npz"
    np.savez(path, skus=np.array(["A", "B"]), embeddings=np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float32))
    return SkuBank(path)


def payload(**kw):
    base = {"event_id": "e", "order_id": "o", "expected_sku": "A", "expected_unit_weight_g": 52,
            "expected_quantity": 1, "observed_quantity": 1, "measured_weight_delta_g": 52,
            "visual_embedding": [1, 0, 0, 0]}
    return {**base, **kw}


def test_pass(bank):
    assert audit_drop(payload(), bank, 0.78, 18)["decision"] == "pass"


def test_same_weight_variant_swap_caught_by_vision(bank):
    r = audit_drop(payload(visual_embedding=[0, 1, 0, 0]), bank, 0.78, 18)
    assert r["decision"] == "hold" and "sku_mismatch" in r["reason_codes"]


def test_missing_unit_caught(bank):
    r = audit_drop(payload(expected_quantity=2), bank, 0.78, 18)
    assert "quantity_short" in r["reason_codes"]


def test_weight_anomaly(bank):
    assert "weight_out_of_tolerance" in audit_drop(payload(measured_weight_delta_g=20), bank, 0.78, 18)["reason_codes"]


def test_no_embedding_requires_review(bank):
    assert audit_drop(payload(visual_embedding=None), bank, 0.78, 18)["decision"] == "review_required"


def test_dimension_mismatch_caught(bank):
    with pytest.raises(ValueError, match="Embedding dimension mismatch"):
        bank.match([1.0, 0.0])
    r = audit_drop(payload(visual_embedding=[1.0, 0.0]), bank, 0.78, 18)
    assert "embedding_dimension_mismatch" in r["reason_codes"]
    assert r["decision"] == "hold"


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
