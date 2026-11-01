import os

os.environ.setdefault("INSTA_AUDIT_ENVIRONMENT", "development")

from fastapi.testclient import TestClient  # noqa: E402

from insta_audit.api.main import app  # noqa: E402

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_catalog_items_have_weights():
    r = client.get("/v1/catalog/items").json()
    if r["count"]:
        assert any(i["unit_weight_g"] for i in r["items"])


def test_reconcile_endpoint():
    r = client.post("/v1/packing/reconcile", json={"order_id": "o1", "expected_quantities": {"A": 1}, "observed_quantities": {}})
    assert r.status_code == 200 and r.json()["decision"] == "hold"


def test_packing_audit_without_model_is_review_not_pass():
    r = client.post("/v1/packing/audit", json={
        "event_id": "e", "order_id": "o", "expected_sku": "X", "measured_weight_delta_g": 50, "expected_unit_weight_g": 50})
    assert r.status_code == 200 and r.json()["decision"] == "review_required"



def _audit(**kw):
    body = {"event_id": "e", "order_id": "o", "expected_sku": "8901491501054",
            "expected_unit_weight_g": 52, "measured_weight_delta_g": 52, **kw}
    return client.post("/v1/packing/audit", json=body).json()


def test_ui_wrong_variant_payload_is_hold():
    r = _audit(observed_sku="8901491501061")
    assert r["decision"] == "hold" and "sku_mismatch" in r["reason_codes"]


def test_ui_extra_unit_payload_is_hold():
    r = _audit(observed_quantity=2, measured_weight_delta_g=104)
    assert r["decision"] == "hold" and "quantity_over" in r["reason_codes"]
    assert r["weight_error_pct"] == 0


def test_ui_correct_without_photo_is_review_not_pass():
    r = _audit(observed_sku="8901491501054")
    assert r["decision"] == "review_required" and r["visual_similarity"] is None
