import os

os.environ.setdefault("INSTA_AUDIT_ENVIRONMENT", "development")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from insta_audit.api import main  # noqa: E402
from insta_audit.api.main import app  # noqa: E402
from insta_audit.vision.sku_bank import SkuBank  # noqa: E402

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "versions" in data
    assert "service" in data["versions"]
    assert "quality_policy" in data["versions"]


def test_catalog_items_have_weights():
    r = client.get("/v1/catalog/items").json()
    if r["count"]:
        assert any(i["unit_weight_g"] for i in r["items"])


def test_reconcile_endpoint():
    r = client.post(
        "/v1/packing/reconcile",
        json={"order_id": "o1", "expected_quantities": {"A": 1}, "observed_quantities": {}},
    )
    assert r.status_code == 200 and r.json()["decision"] == "hold"


def test_packing_audit_rejects_client_controlled_fields():
    base = {"event_id": "e", "order_id": "o", "expected_sku": "X", "measured_weight_delta_g": 50}
    for field, value in [("expected_unit_weight_g", 50), ("max_weight_error_pct", 100),
                         ("observed_sku", "X"), ("observed_quantity", 1)]:
        assert client.post("/v1/packing/audit", json={**base, field: value}).status_code == 422


def test_packing_audit_without_model_is_review_not_pass():
    r = client.post(
        "/v1/packing/audit",
        json={"event_id": "e", "order_id": "o", "expected_sku": "X", "measured_weight_delta_g": 50},
    )
    assert r.status_code == 200
    res = r.json()
    assert res["decision"] == "review_required"
    assert "versions" in res
    assert "latency_ms" in res
    assert res["latency_ms"] >= 0


@pytest.fixture
def audit_env(monkeypatch, tmp_path):
    path = tmp_path / "bank.npz"
    np.savez(path, skus=np.array(["A", "B"]),
             embeddings=np.array([[1, 0, 0, 0, 0, 0, 0, 0], [0, 1, 0, 0, 0, 0, 0, 0]], dtype=np.float32))
    monkeypatch.setattr(main, "bank", SkuBank(path))
    monkeypatch.setattr(main, "catalog", {
        "A": {"brand": "a", "gross_mean_g": 52.0, "gross_std_g": 1.0, "gross_verified": True, "unit_weight_g": None},
        "B": {"brand": "b", "gross_mean_g": 52.0, "gross_std_g": 1.0, "gross_verified": True, "unit_weight_g": None},
    })


def _audit(**kw):
    body = {"event_id": "e", "order_id": "o", "expected_sku": "A", "measured_weight_delta_g": 52,
            "visual_embedding": [1, 0, 0, 0, 0, 0, 0, 0], **kw}
    return client.post("/v1/packing/audit", json=body).json()


def test_audit_correct_drop_passes(audit_env):
    r = _audit()
    assert r["decision"] == "pass" and r["match_status"] == "confident" and r["weight_status"] == "ok"


def test_audit_wrong_variant_is_hold(audit_env):
    r = _audit(visual_embedding=[0, 1, 0, 0, 0, 0, 0, 0])
    assert r["decision"] == "hold" and "sku_mismatch" in r["reason_codes"]


def test_audit_extra_unit_is_inferred_from_weight(audit_env):
    r = _audit(measured_weight_delta_g=104)
    assert r["decision"] == "hold" and "quantity_over" in r["reason_codes"] and r["inferred_quantity"] == 2


def test_audit_without_photo_is_review_not_pass(audit_env):
    r = _audit(visual_embedding=None)
    assert r["decision"] == "review_required" and r["visual_similarity"] is None


def test_catalog_match_reports_margin_and_unknown(audit_env, monkeypatch):
    class FakeEmbedder:
        ready = True
        dimension = 8
        model_path = None
        failures = 0

        def __init__(self, vector):
            self.vector = vector

        def from_base64(self, encoded):
            return self.vector

    body = {"image_base64": "A" * 40}
    monkeypatch.setattr(main, "embedder", FakeEmbedder([1, 0, 0, 0, 0, 0, 0, 0]))
    res = client.post("/v1/catalog/match", json=body).json()
    assert res["decision"] == "match" and res["match_status"] == "confident" and res["top_sku"] == "A"
    monkeypatch.setattr(main, "embedder", FakeEmbedder([0, 0, 1, 0, 0, 0, 0, 0]))
    res = client.post("/v1/catalog/match", json=body).json()
    assert res["decision"] == "review_required" and res["reason_codes"] == ["unknown_item"]
    monkeypatch.setattr(main, "embedder", FakeEmbedder([1, 1, 0, 0, 0, 0, 0, 0]))
    res = client.post("/v1/catalog/match", json=body).json()
    assert res["match_status"] == "ambiguous"


def test_inward_expiry_with_ocr_disambiguation():
    # Tests that OCR with MFG and USE BY extracts the right date and calculates shelf life properly
    r = client.post(
        "/v1/inward/expiry",
        json={
            "category": "snacks_and_namkeen",
            "received_date": "2026-06-01",
            "ocr_text": "PKD ON 10/05/2026 USE BY 10/11/2026 BATCH 101",
        },
    )
    assert r.status_code == 200
    res = r.json()
    assert res["expiry_date"] == "2026-11-10"
    assert res["manufactured_date"] == "2026-05-10"
    assert res["life_basis"] == "pack_label"
    assert res["decision"] == "accept"
    assert "versions" in res
    assert "latency_ms" in res


def test_inward_expiry_handles_all_catalog_categories():
    # Every catalog category should be supported and not return unknown_category
    categories = [
        "biscuits_and_bakery",
        "beverages",
        "snacks_and_namkeen",
        "pantry_staples",
        "condiments_and_spreads",
        "tea_coffee_and_mixes",
        "dairy",
    ]
    for cat in categories:
        r = client.post(
            "/v1/inward/expiry",
            json={
                "category": cat,
                "received_date": "2026-06-01",
                "expiry_date": "2026-10-01",
            },
        )
        assert r.status_code == 200
        res = r.json()
        assert "unknown_category" not in res["reason_codes"]
