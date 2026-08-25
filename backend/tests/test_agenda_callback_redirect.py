"""The consent round trip must land the rep back in the APP, not on the API.

The bug: `RedirectResponse("/?agenda=connected")` is relative, so in
development it resolved against uvicorn on :8000 — which serves no page — and a
rep who had just connected Gmail successfully was shown
`{"detail":"Not Found"}`. The credential was stored; only the landing was wrong,
which is the worst shape to debug from outside.

Asserted as a MECHANISM, not one example: every redirect the callback can emit
must carry an absolute origin, and no relative form may creep back in.
"""

from __future__ import annotations

import inspect

import pytest

from app.api import agenda as agenda_api
from app.config import settings


@pytest.fixture
def app_origin(monkeypatch):
    monkeypatch.setattr(
        settings, "cors_origins", "http://localhost:5173,http://127.0.0.1:5173"
    )
    # cors_origin_list is a computed field over cors_origins, so it follows.
    assert settings.cors_origin_list[0] == "http://localhost:5173"


def test_both_outcomes_land_on_the_app_origin(app_origin):
    for outcome in ("connected", "failed"):
        target = agenda_api._back_to_app(outcome).headers["location"]
        assert target.startswith("http://localhost:5173/"), target
        assert target.endswith(f"?agenda={outcome}"), target


def test_it_lands_on_settings_where_the_result_is_visible(app_origin):
    assert (
        agenda_api._back_to_app("connected").headers["location"]
        == "http://localhost:5173/settings?agenda=connected"
    )


def test_a_same_origin_deployment_still_gets_a_relative_path(monkeypatch):
    """Behind one reverse proxy the app and API share an origin, and the
    relative form is then the correct one — so an empty CORS list must not
    produce a redirect to a bare '/settings' on some other host."""
    monkeypatch.setattr(settings, "cors_origins", "")
    assert (
        agenda_api._back_to_app("connected").headers["location"]
        == "/settings?agenda=connected"
    )


def test_no_relative_redirect_survives_in_the_callback():
    """The mechanism. A future edit that writes RedirectResponse("/...") inline
    would reintroduce the 404 for whichever branch it was added to, and only in
    a split-origin deployment — i.e. never in CI."""
    source = inspect.getsource(agenda_api.callback)
    assert 'RedirectResponse("/' not in source
    assert source.count("_back_to_app(") >= 7, "a branch stopped using the helper"
