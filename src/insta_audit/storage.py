"""Object storage for proof-of-pack bundles: S3-compatible with a local-filesystem fallback."""
from pathlib import Path
from typing import Protocol


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...

    def get(self, key: str) -> bytes | None: ...

    def list(self, prefix: str) -> list[str]: ...

    def delete(self, key: str) -> None: ...


def _safe_key(key: str) -> str:
    parts = key.replace("\\", "/").split("/")
    if not key or key.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"unsafe object key: {key!r}")
    return "/".join(parts)


class LocalStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / _safe_key(key)).resolve()
        if self.root not in path.parents:
            raise ValueError(f"unsafe object key: {key!r}")
        return path

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def list(self, prefix: str) -> list[str]:
        base = self.root / prefix.strip("/")
        if not base.exists():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file())

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            path.unlink()


class S3Store:
    """Any S3-compatible service (AWS S3, MinIO, ...). Pass `client` to inject a pre-built or fake
    client; otherwise boto3 is imported lazily (pip install insta-audit[s3])."""

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        endpoint_url: str | None = None,
        region: str | None = None,
        client=None,
    ) -> None:
        if client is None:
            import boto3

            client = boto3.client("s3", endpoint_url=endpoint_url, region_name=region)
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")

    def _full(self, key: str) -> str:
        key = _safe_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._full(key), Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._full(key))["Body"].read()
        except Exception as error:  # botocore ClientError NoSuchKey, without importing botocore
            code = getattr(error, "response", {}).get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404"):
                return None
            raise

    def list(self, prefix: str) -> list[str]:
        full_prefix = self._full(prefix.strip("/") + "/x")[:-1]
        keys, token = [], None
        while True:
            args = {"Bucket": self.bucket, "Prefix": full_prefix}
            if token:
                args["ContinuationToken"] = token
            page = self.client.list_objects_v2(**args)
            keys += [item["Key"] for item in page.get("Contents", [])]
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
        strip = len(self.prefix) + 1 if self.prefix else 0
        return sorted(key[strip:] for key in keys)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._full(key))
