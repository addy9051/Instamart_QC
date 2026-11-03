import io
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from insta_audit.orders import (
    CompositeOrderSource,
    MockOrderSource,
    Order,
    OrderLine,
    WebhookOrderSource,
    sign_payload,
    verify_signature,
)
from insta_audit.privacy import PrivacyPolicy, parse_box, parse_boxes, pseudonymize, redact_image
from insta_audit.proof import build_bundle, purge_expired, validate_id, verify_bundle
from insta_audit.storage import LocalStore, S3Store


def jpeg(color=(200, 30, 30), size=(400, 300)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="JPEG")
    return out.getvalue()


class FakeS3:
    """Minimal stand-in for a boto3 S3 client."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, ContentType):  # noqa: N803
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            error = Exception("missing")
            error.response = {"Error": {"Code": "NoSuchKey"}}
            raise error
        import io as _io
        return {"Body": _io.BytesIO(self.objects[Key])}

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):  # noqa: N803
        return {"Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)], "IsTruncated": False}

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.objects.pop(Key, None)


@pytest.fixture(params=["local", "s3"])
def store(request, tmp_path):
    if request.param == "local":
        return LocalStore(tmp_path / "store")
    return S3Store("bucket", prefix="pilot", client=FakeS3())


def test_store_roundtrip_list_delete_and_key_safety(store):
    store.put("a/b/c.bin", b"x")
    assert store.get("a/b/c.bin") == b"x" and store.get("a/none") is None
    assert store.list("a/b") == ["a/b/c.bin"]
    store.delete("a/b/c.bin")
    assert store.list("a") == []
    for bad in ("../evil", "/abs", "a/../../b", ""):
        with pytest.raises(ValueError):
            store.put(bad, b"x")


def test_bundle_verifies_then_detects_tampering(store):
    policy = PrivacyPolicy()
    build_bundle(store, "o1", "bag-1", [("keyframe", jpeg()), ("seal", jpeg((0, 0, 0)))],
                 {"decision": "pass"}, policy, signing_key="k")
    assert verify_bundle(store, "o1", "bag-1", "k")["valid"] is True

    store.put("proof/o1/bag-1/seal_001.jpg", jpeg((1, 2, 3)))
    result = verify_bundle(store, "o1", "bag-1", "k")
    assert result["valid"] is False and "modified_seal_001.jpg" in result["reasons"]


def test_bundle_detects_evidence_edit_missing_file_and_bad_signature(store):
    policy = PrivacyPolicy()
    build_bundle(store, "o2", "bag-1", [("keyframe", jpeg())], {"decision": "hold"}, policy, signing_key="k")
    store.put("proof/o2/bag-1/evidence.json", b'{"decision":"pass"}')
    assert "modified_evidence.json" in verify_bundle(store, "o2", "bag-1", "k")["reasons"]
    store.delete("proof/o2/bag-1/keyframe_000.jpg")
    assert "missing_keyframe_000.jpg" in verify_bundle(store, "o2", "bag-1", "k")["reasons"]

    build_bundle(store, "o3", "bag-1", [], {"decision": "pass"}, policy, signing_key="k")
    assert "signature_invalid" in verify_bundle(store, "o3", "bag-1", "wrong-key")["reasons"]
    assert verify_bundle(store, "nope", "bag-1")["reasons"] == ["bundle_not_found"]


def test_forged_manifest_chain_is_detected(store):
    build_bundle(store, "o4", "bag-1", [("keyframe", jpeg())], {"decision": "hold"}, PrivacyPolicy())
    manifest = json.loads(store.get("proof/o4/bag-1/manifest.json"))
    manifest["records"][1]["sha256"] = "0" * 64
    store.put("proof/o4/bag-1/manifest.json", json.dumps(manifest).encode())
    assert not verify_bundle(store, "o4", "bag-1")["valid"]


def test_retention_purge_removes_only_expired_bundles(store):
    now = datetime.now(UTC)
    policy = PrivacyPolicy(retention_days=30)
    build_bundle(store, "old", "bag-1", [("keyframe", jpeg())], {}, policy, now=now - timedelta(days=31))
    build_bundle(store, "new", "bag-1", [("keyframe", jpeg())], {}, policy, now=now)
    assert purge_expired(store, now) == 1
    assert store.list("proof/old") == [] and store.list("proof/new")


def test_identifier_validation():
    for bad in ("../x", "a/b", "", "x" * 101, "..", "a b"):
        with pytest.raises(ValueError):
            validate_id(bad)
    assert validate_id("ORD-123_a.b") == "ORD-123_a.b"


def test_redaction_crops_blurs_downsizes_and_strips_metadata():
    image = Image.new("RGB", (2000, 1000), (255, 255, 255))
    for x in range(1000, 2000, 20):  # high-frequency stripes in the label zone
        image.paste((0, 0, 0), (x, 0, x + 10, 1000))
    out = io.BytesIO()
    exif = Image.Exif()
    exif[0x010E] = "worker name"
    image.save(out, format="JPEG", exif=exif)

    policy = PrivacyPolicy(roi=(0.0, 0.0, 1.0, 1.0), blur_boxes=((0.5, 0.0, 1.0, 1.0),), max_side_px=500)
    redacted = redact_image(out.getvalue(), policy)
    result = Image.open(io.BytesIO(redacted))
    assert max(result.size) <= 500
    assert not result.getexif()
    right = result.crop((int(result.width * 0.55), 0, result.width, result.height)).convert("L")
    low, high = right.getextrema()
    assert high - low < 120  # stripes were blurred away

    narrow = redact_image(out.getvalue(), PrivacyPolicy(roi=(0.0, 0.0, 0.5, 1.0), max_side_px=4000))
    assert Image.open(io.BytesIO(narrow)).size == (1000, 1000)


def test_redaction_uses_face_detector_and_fails_closed():
    calls = []

    def detector(image):
        calls.append(image.size)
        return [(0.0, 0.0, 0.5, 0.5)]

    redact_image(jpeg(), PrivacyPolicy(), face_detector=detector)
    assert calls
    with pytest.raises(ValueError):
        redact_image(b"not an image", PrivacyPolicy())


def test_privacy_policy_validation_and_helpers():
    with pytest.raises(ValueError):
        PrivacyPolicy(roi=(0.5, 0.0, 0.2, 1.0))
    with pytest.raises(ValueError):
        PrivacyPolicy(retention_days=0)
    assert parse_box("0.1,0.2,0.9,1") == (0.1, 0.2, 0.9, 1.0)
    assert parse_boxes("0,0,0.5,0.5; 0.5,0.5,1,1") == ((0, 0, 0.5, 0.5), (0.5, 0.5, 1, 1))
    assert parse_boxes("") == ()
    assert pseudonymize("worker-7", "salt") == pseudonymize("worker-7", "salt") != pseudonymize("worker-8", "salt")


def test_webhook_signature_and_replay_window():
    body = b'{"order_id":"o"}'
    now = time.time()
    signature = sign_payload("secret", str(int(now)), body)
    assert verify_signature("secret", str(int(now)), body, signature, now=now)
    assert not verify_signature("other", str(int(now)), body, signature, now=now)
    assert not verify_signature("secret", str(int(now)), body + b" ", signature, now=now)
    assert not verify_signature("secret", str(int(now)), body, signature, now=now + 3600)
    assert not verify_signature("secret", "not-a-number", body, signature, now=now)


def test_order_validation_and_sources():
    with pytest.raises(ValueError):
        Order(order_id="o", store_id="s", lines=[OrderLine(sku="a", quantity=0)])
    with pytest.raises(ValueError):
        Order(order_id="o", store_id="s", lines=[OrderLine(sku="a", quantity=1)], scans={"a": -1})
    order = Order(order_id="o", store_id="s", lines=[OrderLine(sku="a", quantity=2), OrderLine(sku="a", quantity=1)])
    assert order.expected_by_sku() == {"a": 3}

    mock = MockOrderSource([order])
    hook = WebhookOrderSource("secret", ttl_seconds=0)
    other = order.model_copy(update={"order_id": "w"})
    hook.ingest(other)
    composite = CompositeOrderSource(hook, mock)
    assert composite.get_order("o") == order
    assert hook.get_order("w") is None  # ttl=0 expires immediately
    assert composite.post_result("o", {"decision": "pass"}) is True


def test_webhook_source_is_bounded():
    hook = WebhookOrderSource("s", max_orders=2)
    for i in range(3):
        hook.ingest(Order(order_id=f"o{i}", store_id="s", lines=[OrderLine(sku="a", quantity=1)]))
    assert hook.get_order("o0") is None and hook.get_order("o2") is not None


def test_post_result_is_signed_and_failures_are_swallowed(monkeypatch):
    sent = {}

    class Resp:
        ok = True

    def fake_post(url, data, headers, timeout):
        sent.update(url=url, data=data, headers=headers)
        return Resp()

    monkeypatch.setattr("insta_audit.orders.requests.post", fake_post)
    hook = WebhookOrderSource("secret", "https://oms.example/result")
    assert hook.post_result("o", {"decision": "hold"}) is True
    assert verify_signature("secret", sent["headers"]["X-Timestamp"], sent["data"], sent["headers"]["X-Signature"])

    import requests

    def boom(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr("insta_audit.orders.requests.post", boom)
    assert hook.post_result("o", {}) is False
    assert WebhookOrderSource(None).post_result("o", {}) is False
