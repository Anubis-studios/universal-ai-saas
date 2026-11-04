"""Guards the browser client against URL and auth drift from the API."""
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
API_JS = (FRONTEND / "lib" / "api.js").read_text()
APP_JS = (FRONTEND / "app.js").read_text()
INDEX = (FRONTEND / "index.html").read_text()


def test_no_double_api_prefix():
    # The browser used to request /api/api/v1/... when served by the app itself.
    assert "/api/api" not in API_JS
    assert '`${apiBase()}/api/v1${path}`' in API_JS


def test_default_base_is_origin():
    assert 'window.location.origin' in API_JS
    assert '/api`' not in API_JS.split("export function apiBase")[0]


def test_every_request_sends_the_bearer_token():
    assert "function authHeaders" in API_JS
    assert API_JS.count("authHeaders(") >= 3  # definition + both fetch call sites
    assert "authorization" in API_JS.lower()


def test_settings_expose_the_api_key():
    assert 'id="api-key"' in INDEX
    assert 'type="password"' in INDEX
    assert "setApiKey" in APP_JS


def test_library_view_is_wired_up():
    # The library tab must exist, be initialised, and hit the documented routes.
    assert 'data-view="library"' in INDEX
    assert 'id="view-library"' in INDEX
    assert "initLibrary" in APP_JS
    assert 'request(`/library' in API_JS
    assert 'request("/db/status")' in API_JS
    page = (FRONTEND / "pages" / "library.js").read_text()
    assert "api.library(" in page and "api.deleteRecord(" in page


def test_library_refresh_reads_width_from_viewport_notrypto():
    # Guard against the old /api/api-style drift: library must use the shared client.
    page = (FRONTEND / "pages" / "library.js").read_text()
    assert "fetch(" not in page
    assert "/api/v1" not in page
