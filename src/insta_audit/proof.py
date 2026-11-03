"""Tamper-evident proof-of-pack bundles (I2).

A bundle is keyed by order_id/bag_id and holds redacted keyframes, a sealed-bag frame and an
evidence record (scan list, seal weight, decision, versions). Every artifact is a link in a SHA-256
hash chain; the head can be HMAC-signed with a service key and logged elsewhere. This is
tamper-EVIDENT: someone who can rewrite the whole store AND holds the key could forge a bundle,
so keep the signing key out of the store and ship head hashes to the structured audit log.
"""
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta

from insta_audit.privacy import PrivacyPolicy
from insta_audit.storage import ObjectStore

SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
MANIFEST = "manifest.json"
EVIDENCE = "evidence.json"
ROOT = "proof"


def validate_id(value: str) -> str:
    if not SAFE_ID.match(value) or value in (".", ".."):
        raise ValueError(f"invalid identifier: {value!r}")
    return value


def _canonical(data) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _link(prev_hash: str, record: dict) -> str:
    return _sha(prev_hash.encode("utf-8") + _canonical(record))


def _genesis(order_id: str, bag_id: str) -> str:
    return _sha(f"{order_id}|{bag_id}".encode())


def _sign(key: str, head_hash: str) -> str:
    return hmac.new(key.encode("utf-8"), head_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def bundle_prefix(order_id: str, bag_id: str) -> str:
    return f"{ROOT}/{validate_id(order_id)}/{validate_id(bag_id)}"


def build_bundle(
    store: ObjectStore,
    order_id: str,
    bag_id: str,
    images: list[tuple[str, bytes]],
    evidence: dict,
    policy: PrivacyPolicy,
    signing_key: str | None = None,
    now: datetime | None = None,
) -> dict:
    """`images` are (kind, already-redacted JPEG bytes). Returns the manifest that was stored."""
    prefix = bundle_prefix(order_id, bag_id)
    now = now or datetime.now(UTC)
    artifacts: list[tuple[str, str, bytes, str]] = []
    for index, (kind, data) in enumerate(images):
        artifacts.append((kind, f"{kind}_{index:03d}.jpg", data, "image/jpeg"))
    artifacts.append(("evidence", EVIDENCE, _canonical(evidence), "application/json"))

    records, prev = [], _genesis(order_id, bag_id)
    for seq, (kind, name, data, content_type) in enumerate(artifacts):
        record = {"seq": seq, "kind": kind, "name": name, "sha256": _sha(data), "bytes": len(data)}
        record["prev_hash"] = prev
        record["hash"] = prev = _link(prev, {k: v for k, v in record.items() if k not in ("prev_hash", "hash")})
        records.append(record)
        store.put(f"{prefix}/{name}", data, content_type)

    manifest = {
        "schema": 1,
        "order_id": order_id,
        "bag_id": bag_id,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=policy.retention_days)).isoformat(),
        "purpose": policy.purpose,
        "records": records,
        "head_hash": prev,
        "head_signature": _sign(signing_key, prev) if signing_key else None,
    }
    store.put(f"{prefix}/{MANIFEST}", _canonical(manifest), "application/json")
    return manifest


def load_manifest(store: ObjectStore, order_id: str, bag_id: str) -> dict | None:
    raw = store.get(f"{bundle_prefix(order_id, bag_id)}/{MANIFEST}")
    return json.loads(raw) if raw else None


def verify_bundle(store: ObjectStore, order_id: str, bag_id: str, signing_key: str | None = None) -> dict:
    manifest = load_manifest(store, order_id, bag_id)
    if manifest is None:
        return {"valid": False, "reasons": ["bundle_not_found"], "head_hash": None}
    prefix = bundle_prefix(order_id, bag_id)
    reasons: list[str] = []
    prev = _genesis(order_id, bag_id)
    for record in manifest.get("records", []):
        body = {k: v for k, v in record.items() if k not in ("prev_hash", "hash")}
        if record.get("prev_hash") != prev or record.get("hash") != _link(prev, body):
            reasons.append(f"chain_broken_at_{record.get('seq')}")
        prev = record.get("hash", "")
        data = store.get(f"{prefix}/{record['name']}")
        if data is None:
            reasons.append(f"missing_{record['name']}")
        elif _sha(data) != record["sha256"]:
            reasons.append(f"modified_{record['name']}")
    if manifest.get("head_hash") != prev:
        reasons.append("head_hash_mismatch")
    signature = manifest.get("head_signature")
    if signing_key:
        if not signature:
            reasons.append("signature_missing")
        elif not hmac.compare_digest(signature, _sign(signing_key, manifest.get("head_hash", ""))):
            reasons.append("signature_invalid")
    return {"valid": not reasons, "reasons": reasons, "head_hash": manifest.get("head_hash"),
            "signed": bool(signature), "created_at": manifest.get("created_at"),
            "expires_at": manifest.get("expires_at"), "records": len(manifest.get("records", []))}


def purge_expired(store: ObjectStore, now: datetime | None = None) -> int:
    """Delete whole bundles past their retention window. Returns the number of bundles removed."""
    now = now or datetime.now(UTC)
    removed = 0
    for key in store.list(ROOT):
        if not key.endswith(f"/{MANIFEST}"):
            continue
        raw = store.get(key)
        try:
            expires = datetime.fromisoformat(json.loads(raw)["expires_at"]) if raw else None
        except (ValueError, KeyError):
            expires = None
        if expires is not None and expires <= now:
            directory = key.rsplit("/", 1)[0]
            for member in store.list(directory):
                store.delete(member)
            removed += 1
    return removed
