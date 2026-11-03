# OMS/WMS webhook contract

Insta-Audit never trusts the packing client for what was ordered or scanned. Expected lines and
the picker's scans come from the OMS/WMS through this contract. The code is
`insta_audit/orders.py` (`WebhookOrderSource`); `MockOrderSource` implements the same interface for
tests and local pilots.

## 1. OMS → Insta-Audit: order ready to pack

`POST /v1/orders/webhook`

Headers:

| Header | Value |
|---|---|
| `X-Timestamp` | Unix seconds when the request was signed. Rejected if more than 300 s from server time (replay protection). |
| `X-Signature` | `sha256=` + hex HMAC-SHA256 of `"<X-Timestamp>.<raw body>"` using the shared secret (`INSTA_AUDIT_WEBHOOK_SECRET`). |

Body (JSON, max 256 KB, unknown fields rejected):

```json
{
  "order_id": "ORD-123",
  "store_id": "STORE-BLR-001",
  "lines": [
    {"sku": "8901491501054", "quantity": 2, "bag_id": "ambient"},
    {"sku": "8901719134845", "quantity": 1, "bag_id": "frozen"}
  ],
  "scans": {"8901491501054": 2, "8901719134845": 1}
}
```

- `order_id` and `bag_id` match `[A-Za-z0-9._-]{1,100}` / 1–40 characters. `bag_id` defaults to `bag-1`.
- `quantity` is 1–100; at most 200 lines. `scans` is optional (SKU → units scanned by the picker).
- `scans` is order-level. For multi-bag orders only bag-vs-order checks and the weight check run per bag.

Responses: `200 {"status":"accepted"}`, `401` bad/missing/stale signature, `413` too large, `422` invalid body, `503` webhook not configured. Orders are held in memory (TTL `INSTA_AUDIT_WEBHOOK_ORDER_TTL_SECONDS`, bounded); re-sending an order replaces it. Run a single worker per store, or replace `WebhookOrderSource` with a shared cache before scaling out.

## 2. Packer app → Insta-Audit: seal

`POST /v1/packing/seal` (API key). The client sends only `order_id`, `bag_id`, `measured_weight_g`, `settled`, optional `bagged_quantities`, and optional frames. An unknown order returns `review_required` / `order_not_found`; it never blocks packing.

## 3. Insta-Audit → OMS: result post-back

When `INSTA_AUDIT_WEBHOOK_RESULT_URL` is set, advisory/enforce results are POSTed with the same signing scheme and body `{"order_id": ..., "result": {"bag_id", "decision", "mode", "action", "reason_codes"}}`. Shadow results are never posted. Post-back is best-effort with a 3 s timeout; failures are logged and never affect packing.

## Open items for Instamart

Confirm the real field names, whether scans carry batch/expiry, whether lines are pre-assigned to bags, and the authentication the OMS can support. Only this adapter should need to change.
