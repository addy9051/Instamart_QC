import base64
import io
import json
import os
import time

import pytest

os.environ.setdefault("INSTA_AUDIT_ENVIRONMENT", "development")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from insta_audit.api import main  # noqa: E402
from insta_audit.modes import Mode, ModePolicy  # noqa: E402
from insta_audit.orders import MockOrderSource, Order, OrderLine, sign_payload  # noqa: E402
from insta_audit.storage import LocalStore  # noqa: E402

client = TestClient(main.app)

CATALOG = {
    "chips": {"gross_mean_g": 60.0, "gross_std_g": 1.5, "gross_verified": True, "unit_weight_g": 55.0},
    "salt": {"gross_mean_g": 1050.0, "gross_std_g": 6.0, "gross_verified": True, "unit_weight_g": 1000.0},
}


def b64_jpeg() -> str:
    out = io.BytesIO()
    Image.new("RGB", (320, 240), (10, 120, 200)).save(out, format="JPEG")
    return base64.b64encode(out.getvalue()).decode()


@pytest.fixture
def seal_env(monkeypatch, tmp_path):
    order = Order(
        order_id="ORD-1", store_id="S1",
        lines=[OrderLine(sku="chips", quantity=2), OrderLine(sku="salt", quantity=1)],
        scans={"chips": 2, "salt": 1},
    )
    source = MockOrderSource([order])
    monkeypatch.setattr(main, "order_source", source)
    monkeypatch.setattr(main, "catalog", CATALOG)
    monkeypatch.setattr(main, "proof_store", LocalStore(tmp_path / "proof"))
    policy = ModePolicy(default_mode=Mode.ADVISORY, allow_enforce=True, stores={"ENF": Mode.ENFORCE})
    monkeypatch.setattr(main, "mode_policy", policy)
    return {"source": source, "policy": policy, "order": order, "tmp": tmp_path}


def seal(**overrides):
    body = {"order_id": "ORD-1", "measured_weight_g": 1170, "bagged_quantities": {"chips": 2, "salt": 1}}
    return client.post("/v1/packing/seal", json={**body, **overrides})


def test_seal_pass_with_versions_and_latency(seal_env):
    response = seal()
    assert response.status_code == 200
    data = response.json()
    assert data["decision"] == "pass" and data["action"] == "none" and data["mode"] == "advisory"
    assert data["versions"]["mode_policy"] and data["latency_ms"] >= 0
    assert data["weight"]["status"] == "ok"


def test_client_cannot_supply_tolerances_or_expected_weights(seal_env):
    # Unknown fields are ignored; the verdict comes from the server-side catalog and order.
    data = seal(measured_weight_g=900, max_weight_error_pct=100, expected_unit_weight_g=900).json()
    assert data["weight"]["status"] == "short"


def test_seal_hold_when_count_and_weight_agree(seal_env):
    data = seal(measured_weight_g=1110, bagged_quantities={"chips": 1, "salt": 1}).json()
    assert data["decision"] == "hold" and data["confident"] is True
    assert data["action"] == "notify"  # advisory never blocks


def test_enforce_blocks_only_confident_holds(seal_env):
    order = seal_env["order"].model_copy(update={"order_id": "ORD-ENF", "store_id": "ENF"})
    seal_env["source"].add(order)
    blocked = seal(order_id="ORD-ENF", measured_weight_g=1110, bagged_quantities={"chips": 1, "salt": 1}).json()
    assert blocked["action"] == "block"
    review = seal(order_id="ORD-ENF", measured_weight_g=1110).json()
    assert review["decision"] == "review_required" and review["action"] == "notify"
    seal_env["policy"].allow_enforce = False
    assert seal(order_id="ORD-ENF", measured_weight_g=1110,
                bagged_quantities={"chips": 1, "salt": 1}).json()["action"] == "notify"


def test_shadow_and_kill_switch_never_act(seal_env):
    seal_env["policy"].default_mode = Mode.SHADOW
    shadow = seal(measured_weight_g=1110, bagged_quantities={"chips": 1, "salt": 1}).json()
    assert shadow["decision"] == "hold" and shadow["action"] == "none" and shadow["mode"] == "shadow"
    seal_env["policy"].set_kill_switch(True)
    off = seal(measured_weight_g=1110).json()
    assert off["mode"] == "off" and off["action"] == "none" and off["reason_codes"] == ["mode_off"]


def test_unknown_order_and_bad_bag_are_reviews_not_errors(seal_env):
    missing = seal(order_id="NOPE").json()
    assert missing["decision"] == "review_required" and missing["reason_codes"] == ["order_not_found"]
    assert seal(bag_id="frozen").json()["reason_codes"] == ["bag_not_in_order"]


def test_invalid_identifiers_and_counts_rejected(seal_env):
    assert seal(order_id="../etc").status_code == 422
    assert seal(bag_id="a/b").status_code == 422
    assert seal(bagged_quantities={"chips": -1}).status_code == 422
    assert seal(measured_weight_g=-5).status_code == 422


def test_proof_bundle_created_verified_and_tamper_detected(seal_env):
    data = seal(keyframes_base64=[b64_jpeg()], seal_frame_base64=b64_jpeg()).json()
    assert data["proof"]["status"] == "stored" and data["proof"]["images"] == 2
    lookup = client.get("/v1/proof/ORD-1/bag-1").json()
    assert lookup["verification"]["valid"] is True
    assert lookup["verification"]["head_hash"] == data["proof"]["head_hash"]

    main.proof_store.put("proof/ORD-1/bag-1/evidence.json", json.dumps({"decision": "pass"}).encode())
    assert client.get("/v1/proof/ORD-1/bag-1").json()["verification"]["valid"] is False


def test_proof_rejects_bad_images_without_failing_the_audit(seal_env):
    garbage = base64.b64encode(b"not an image").decode()
    data = seal(keyframes_base64=[garbage]).json()
    assert data["decision"] == "pass"
    assert data["proof"]["images"] == 0 and data["proof"]["images_rejected"] == 1
    assert seal(keyframes_base64=["***not-base64***"]).status_code == 422


def test_proof_lookup_errors(seal_env, monkeypatch):
    assert client.get("/v1/proof/NOPE/bag-1").status_code == 404
    assert client.get("/v1/proof/..%2Fx/bag-1").status_code in (404, 422)
    monkeypatch.setattr(main, "proof_store", None)
    assert client.get("/v1/proof/ORD-1/bag-1").status_code == 404
    assert seal().json()["proof"] is None


def test_results_posted_back_only_outside_shadow(seal_env):
    seal()
    assert "ORD-1" in seal_env["source"].results
    seal_env["source"].results.clear()
    seal_env["policy"].default_mode = Mode.SHADOW
    seal()
    assert seal_env["source"].results == {}


def test_webhook_accepts_signed_orders_only(monkeypatch):
    monkeypatch.setattr(main.webhook_orders, "secret", "whsec")
    body = json.dumps({"order_id": "W-1", "store_id": "S9", "lines": [{"sku": "chips", "quantity": 1}]}).encode()
    stamp = str(int(time.time()))
    good = {"X-Timestamp": stamp, "X-Signature": sign_payload("whsec", stamp, body)}

    assert client.post("/v1/orders/webhook", content=body, headers=good).json() == {"status": "accepted", "order_id": "W-1"}
    assert main.webhook_orders.get_order("W-1").store_id == "S9"

    bad = {"X-Timestamp": stamp, "X-Signature": "sha256=deadbeef"}
    assert client.post("/v1/orders/webhook", content=body, headers=bad).status_code == 401
    assert client.post("/v1/orders/webhook", content=body).status_code == 401
    invalid = b'{"order_id":"W-2","store_id":"S","lines":[]}'
    signed = {"X-Timestamp": stamp, "X-Signature": sign_payload("whsec", stamp, invalid)}
    assert client.post("/v1/orders/webhook", content=invalid, headers=signed).status_code == 422
    slash = b'{"order_id":"a/b","store_id":"S","lines":[{"sku":"x","quantity":1}]}'
    signed = {"X-Timestamp": stamp, "X-Signature": sign_payload("whsec", stamp, slash)}
    assert client.post("/v1/orders/webhook", content=slash, headers=signed).status_code == 422


def test_webhook_disabled_without_secret(monkeypatch):
    monkeypatch.setattr(main.webhook_orders, "secret", None)
    assert client.post("/v1/orders/webhook", content=b"{}").status_code == 503


def test_admin_kill_switch_and_purge(seal_env):
    assert client.post("/v1/admin/kill-switch", params={"active": "true"}).json() == {"kill_switch": True}
    assert seal().json()["mode"] == "off"
    assert client.post("/v1/admin/kill-switch", params={"active": "false"}).json() == {"kill_switch": False}
    assert client.post("/v1/admin/proof/purge").json() == {"removed": 0}


def test_admin_requires_api_key_outside_development(seal_env, monkeypatch):
    monkeypatch.setattr(main.settings, "environment", "staging")
    assert client.post("/v1/admin/kill-switch", params={"active": "true"}).status_code == 403
    monkeypatch.setattr(main.settings, "api_key", "k")
    assert client.post("/v1/admin/kill-switch", params={"active": "true"}).status_code == 401
    ok = client.post("/v1/admin/kill-switch", params={"active": "false"}, headers={"x-api-key": "k"})
    assert ok.status_code == 200


def test_reconcile_validates_counts():
    bad = client.post("/v1/packing/reconcile",
                      json={"order_id": "o", "expected_quantities": {"A": -1}, "observed_quantities": {}})
    assert bad.status_code == 422
    huge = {f"S{i}": 1 for i in range(201)}
    assert client.post("/v1/packing/reconcile",
                       json={"order_id": "o", "expected_quantities": huge, "observed_quantities": {}}).status_code == 422


def test_audit_client_tolerance_can_tighten_but_not_loosen():
    base = {"event_id": "e", "order_id": "o", "expected_sku": "X",
            "measured_weight_delta_g": 70, "expected_unit_weight_g": 50}
    loose = client.post("/v1/packing/audit", json={**base, "max_weight_error_pct": 100}).json()
    assert "weight_out_of_tolerance" in loose["reason_codes"]
