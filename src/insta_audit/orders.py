"""Order data adapters. Expected lines and the picker's scan list come from the OMS/WMS, never
from the packing client.

Contract (see docs/oms_webhook_contract.md):
  * OMS -> Insta-Audit: signed "order ready to pack" webhook (`WebhookOrderSource.ingest`).
  * Insta-Audit -> OMS/packer app: signed result post-back (`WebhookOrderSource.post_result`).
"""
import hashlib
import hmac
import json
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Protocol

import requests
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger("insta_audit.orders")

MAX_LINES = 200
MAX_QTY = 100
SIGNATURE_TOLERANCE_SECONDS = 300
DEFAULT_BAG = "bag-1"


class OrderLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1, max_length=80)
    quantity: int = Field(ge=1, le=MAX_QTY)
    # Frozen/chilled goods ship in separate bags; lines without a bag go in the default bag.
    bag_id: str = Field(default=DEFAULT_BAG, min_length=1, max_length=40)


class Order(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(min_length=1, max_length=100)
    store_id: str = Field(min_length=1, max_length=40)
    lines: list[OrderLine] = Field(min_length=1, max_length=MAX_LINES)
    # SKU -> units the picker scanned on the handheld. None when the OMS does not send scans.
    scans: dict[str, int] | None = None

    @field_validator("scans")
    @classmethod
    def valid_scans(cls, scans: dict[str, int] | None) -> dict[str, int] | None:
        if scans is None:
            return None
        if len(scans) > MAX_LINES:
            raise ValueError("too many scan entries")
        if any(not sku or len(sku) > 80 or not 0 <= count <= MAX_QTY for sku, count in scans.items()):
            raise ValueError("scan counts must be between 0 and 100")
        return scans

    def expected_by_sku(self, bag_id: str | None = None) -> dict[str, int]:
        totals: dict[str, int] = {}
        for line in self.lines:
            if bag_id is None or line.bag_id == bag_id:
                totals[line.sku] = totals.get(line.sku, 0) + line.quantity
        return totals

    @property
    def bag_ids(self) -> list[str]:
        return sorted({line.bag_id for line in self.lines})


class OrderSource(Protocol):
    def get_order(self, order_id: str) -> Order | None: ...

    def post_result(self, order_id: str, result: dict) -> bool: ...


class MockOrderSource:
    """In-memory/JSON-file source for tests and local pilots (no OMS needed)."""

    def __init__(self, orders: list[Order] | None = None) -> None:
        self._orders = {order.order_id: order for order in orders or []}
        self.results: dict[str, dict] = {}

    @classmethod
    def from_file(cls, path: str | Path | None) -> "MockOrderSource":
        if not path or not Path(path).is_file():
            return cls()
        path = Path(path)
        rows = json.loads(path.read_text(encoding="utf-8"))
        return cls([Order.model_validate(row) for row in rows])

    def add(self, order: Order) -> None:
        self._orders[order.order_id] = order

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def post_result(self, order_id: str, result: dict) -> bool:
        self.results[order_id] = result
        return True


def sign_payload(secret: str, timestamp: str, body: bytes) -> str:
    message = timestamp.encode("utf-8") + b"." + body
    return "sha256=" + hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_signature(secret: str, timestamp: str, body: bytes, signature: str, now: float | None = None) -> bool:
    try:
        age = abs((now if now is not None else time.time()) - float(timestamp))
    except (TypeError, ValueError):
        return False
    if age > SIGNATURE_TOLERANCE_SECONDS:
        return False
    return hmac.compare_digest(sign_payload(secret, timestamp, body), signature or "")


class WebhookOrderSource:
    """Holds orders pushed by the OMS webhook (bounded, TTL) and posts results back, signed.

    State is per-process; run a single worker per store or back it with a shared cache before
    scaling out.
    """

    def __init__(
        self,
        secret: str | None,
        result_url: str | None = None,
        ttl_seconds: int = 6 * 3600,
        max_orders: int = 5_000,
        post_timeout: float = 3.0,
    ) -> None:
        self.secret = secret
        self.result_url = result_url
        self.ttl_seconds = ttl_seconds
        self.max_orders = max_orders
        self.post_timeout = post_timeout
        self._orders: OrderedDict[str, tuple[float, Order]] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.secret)

    def ingest(self, order: Order) -> None:
        with self._lock:
            self._orders.pop(order.order_id, None)
            self._orders[order.order_id] = (time.monotonic(), order)
            while len(self._orders) > self.max_orders:
                self._orders.popitem(last=False)

    def get_order(self, order_id: str) -> Order | None:
        with self._lock:
            entry = self._orders.get(order_id)
            if entry is None:
                return None
            stored_at, order = entry
            if time.monotonic() - stored_at >= self.ttl_seconds:
                del self._orders[order_id]
                return None
            return order

    def post_result(self, order_id: str, result: dict) -> bool:
        """Best-effort: a failed post-back must never block packing."""
        if not self.result_url or not self.secret:
            return False
        body = json.dumps({"order_id": order_id, "result": result}, default=str).encode("utf-8")
        timestamp = str(int(time.time()))
        try:
            response = requests.post(
                self.result_url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Timestamp": timestamp,
                    "X-Signature": sign_payload(self.secret, timestamp, body),
                },
                timeout=self.post_timeout,
            )
            return response.ok
        except requests.RequestException as error:
            logger.warning("result post-back failed for %s: %s", order_id, error)
            return False


class CompositeOrderSource:
    """Looks in each source in order; results go to every source that accepts them."""

    def __init__(self, *sources: OrderSource) -> None:
        self.sources = sources

    def get_order(self, order_id: str) -> Order | None:
        for source in self.sources:
            order = source.get_order(order_id)
            if order is not None:
                return order
        return None

    def post_result(self, order_id: str, result: dict) -> bool:
        return any([source.post_result(order_id, result) for source in self.sources])
