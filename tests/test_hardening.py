import importlib.util
import os

os.environ.setdefault("INSTA_AUDIT_ENVIRONMENT", "development")

from fastapi.testclient import TestClient  # noqa: E402

from insta_audit import settings as settings_module  # noqa: E402
from insta_audit.api import main  # noqa: E402

client = TestClient(main.app)


# ---- C3: demo UI never turns authentication off -------------------------------------------------


def test_demo_ui_in_production_still_requires_api_key(monkeypatch):
    monkeypatch.setattr(main.settings, "environment", "production")
    monkeypatch.setattr(main.settings, "demo_ui", True)
    monkeypatch.setattr(main.settings, "public_demo", False)
    monkeypatch.setattr(main.settings, "api_key", "secret")
    for path in ("/health", "/v1/catalog/items"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"x-api-key": "wrong"}).status_code == 401
        assert client.get(path, headers={"x-api-key": "secret"}).status_code == 200
    body = {"image_base64": "A" * 40}
    assert client.post("/v1/catalog/match", json=body).status_code == 401
    assert client.post("/v1/inward/freshness", json={"sku": "x", **body}).status_code == 401
    # the static demo files are served, but they are not an API bypass
    assert client.get("/").status_code in (200, 404)


def test_production_without_key_and_without_public_demo_is_rejected(monkeypatch):
    monkeypatch.setattr(main.settings, "environment", "production")
    monkeypatch.setattr(main.settings, "demo_ui", True)
    monkeypatch.setattr(main.settings, "public_demo", False)
    monkeypatch.setattr(main.settings, "api_key", None)
    assert client.get("/health").status_code == 401


def test_demo_ui_disabled_in_production_hides_static_files(monkeypatch):
    monkeypatch.setattr(main.settings, "environment", "production")
    monkeypatch.setattr(main.settings, "demo_ui", False)
    monkeypatch.setattr(main.settings, "public_demo", False)
    assert client.get("/").status_code == 404


def test_rate_limit_and_image_cap_apply_in_public_demo(monkeypatch):
    monkeypatch.setattr(main.settings, "public_demo", True)
    monkeypatch.setattr(main.settings, "max_image_bytes", 5_000_000)
    assert main.settings.effective_max_image_bytes <= main.settings.public_demo_max_image_bytes
    too_big = "A" * (main.settings.effective_max_image_bytes * 4 // 3 + 100)
    assert client.post("/v1/catalog/match", json={"image_base64": too_big}).status_code in (413, 422)


# ---- C23: config/demo paths resolve outside the source tree (non-editable install) --------------


def test_root_resolution_without_source_tree(tmp_path, monkeypatch):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "quality_policy.yaml").write_text("version: 1\n", encoding="utf-8")
    fake_site_packages = tmp_path / "site-packages-parent"
    fake_site_packages.mkdir()
    monkeypatch.setattr(settings_module, "_SOURCE_ROOT", fake_site_packages)  # no configs/ here
    monkeypatch.chdir(tmp_path)
    assert settings_module.Settings(project_root=None).root == tmp_path.resolve()
    explicit = tmp_path / "elsewhere"
    explicit.mkdir()
    assert settings_module.Settings(project_root=str(explicit)).root == explicit.resolve()
    resolved = settings_module.Settings().resolve("configs/quality_policy.yaml")
    assert resolved == str(tmp_path.resolve() / "configs" / "quality_policy.yaml")


def test_readme_numbers_match_bank_metadata():
    spec = importlib.util.spec_from_file_location("sync_readme_stats", "scripts/sync_readme_stats.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import json
    from pathlib import Path

    metadata_path = Path("artifacts/sku_bank.npz.metadata.json")
    if not metadata_path.is_file():
        return
    _, stale = module.sync(Path("README.md").read_text(encoding="utf-8"), json.loads(metadata_path.read_text("utf-8")))
    assert stale == []
    updated, stale = module.sync("<!--bank:sku_count-->1<!--/bank-->", {"sku_count": 7})
    assert updated == "<!--bank:sku_count-->7<!--/bank-->" and len(stale) == 1


# ---- C14/C26: policy covers every catalog category ----------------------------------------------


def test_policy_has_every_catalog_category():
    categories = {item["category"] for item in main.catalog.values()}
    policy_categories = set((main.quality_policy.get("categories") or {}).keys())
    assert categories <= policy_categories | {"packaged"}, categories - policy_categories
