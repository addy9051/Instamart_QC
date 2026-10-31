from datetime import date

import numpy as np
import pytest

from insta_audit.catalog import nominal_weight_g
from insta_audit.decision import audit_drop, reconcile_order
from insta_audit.quality.expiry import evaluate_expiry, parse_expiry
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


def test_reconcile():
    assert reconcile_order({"A": 2}, {"A": 2})["decision"] == "pass"
    r = reconcile_order({"A": 2}, {"A": 1, "B": 1})
    assert r["decision"] == "hold" and set(r["differences"]) == {"A", "B"}


@pytest.mark.parametrize("text,expected", [("52 g", 52), ("500ml", 500), ("1 L", 1000), ("1.2 g", None), ("", None), ("4", None)])
def test_nominal_weight(text, expected):
    assert nominal_weight_g(text) == expected


def test_expiry_policy():
    policy = {"categories": {"dairy": {"min_remaining_life_pct": 70, "reference_shelf_life_days": 7}}}
    assert parse_expiry("USE BY 02/11/26", today=date(2026, 10, 31)) == date(2026, 11, 2)
    r = evaluate_expiry(date(2026, 10, 31), date(2026, 11, 2), "dairy", policy)
    assert r["decision"] != "accept"

