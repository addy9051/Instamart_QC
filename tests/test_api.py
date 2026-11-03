import os

os.environ.setdefault("INSTA_AUDIT_ENVIRONMENT", "development")

from fastapi.testclient import TestClient  # noqa: E402

from insta_audit.api.main import app  # noqa: E402

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


def test_packing_audit_without_model_is_review_not_pass():
    r = client.post(
        "/v1/packing/audit",
        json={
            "event_id": "e",
            "order_id": "o",
            "expected_sku": "X",
            "measured_weight_delta_g": 50,
            "expected_unit_weight_g": 50,
        },
    )
    assert r.status_code == 200
    res = r.json()
    assert res["decision"] == "review_required"
    assert "versions" in res
    assert "latency_ms" in res
    assert res["latency_ms"] >= 0


def _audit(**kw):
    body = {
        "event_id": "e",
        "order_id": "o",
        "expected_sku": "8901491501054",
        "expected_unit_weight_g": 52,
        "measured_weight_delta_g": 52,
        **kw,
    }
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
