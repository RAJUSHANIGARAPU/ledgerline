import pytest

from ledgerline.config import get_settings
from ledgerline.ops import routes


@pytest.fixture
def ops_dir(tmp_path, monkeypatch, database_url):
    monkeypatch.setenv("LEDGERLINE_OPS_DIR", str(tmp_path))
    get_settings.cache_clear()
    routes._limiter = None
    yield tmp_path
    get_settings.cache_clear()
    routes._limiter = None


@pytest.fixture
def deep_client(ops_dir):
    from fastapi.testclient import TestClient

    from ledgerline.main import create_app

    return TestClient(create_app())
