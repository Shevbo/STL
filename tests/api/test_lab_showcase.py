"""Витрина кампаний: API читает файлы сборщика и НЕ выдумывает то, чего в них нет."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trader.api import lab_showcase
from trader.auth.portal import make_session_token

SECRET = "test-bridge-secret"


class _Settings:
    shectory_auth_bridge_secret = SECRET


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(lab_showcase, "DIR", tmp_path)
    lab_showcase._cache.clear()
    app = FastAPI()
    app.include_router(lab_showcase.router)
    app.state.settings = _Settings()
    c = TestClient(app)
    c.headers.update({"Authorization": "Bearer " + make_session_token("op@example.com", SECRET)})
    c.tmp = tmp_path
    return c


def test_no_index_says_why_instead_of_an_empty_showcase(client):
    """Нет index.json — это не «кампаний ноль», а «витрина не собрана»."""
    r = client.get("/api/v1/lab/showcase/campaigns").json()
    assert r["available"] is False
    assert r["campaigns"] == []
    assert "не собрана" in r["reason"]
    assert r["built_at_ms"] is None


def test_index_is_served_as_written_and_nulls_stay_null(client):
    card = {"slug": "grid-r01", "title": "Радиация", "thumb": None,
            "no_curve_reason": "перепрогон не делали", "headline": {"net": None}}
    (client.tmp / "index.json").write_text(json.dumps([card]), encoding="utf-8")
    r = client.get("/api/v1/lab/showcase/campaigns").json()
    assert r["available"] is True
    # Карточку не правим и не дополняем: thumb=null — нет кривой, а не нулевая линия.
    assert r["campaigns"] == [card]
    assert r["built_at_ms"] > 0


def test_broken_index_is_not_applied_and_the_reason_is_named(client):
    """Битый файл не применяем: полусписок выглядел бы полной витриной."""
    (client.tmp / "index.json").write_text("[{\"slug\": ", encoding="utf-8")
    r = client.get("/api/v1/lab/showcase/campaigns").json()
    assert r["available"] is False and r["campaigns"] == []
    assert "не читается" in r["reason"]


def test_index_of_wrong_shape_is_refused(client):
    (client.tmp / "index.json").write_text('{"campaigns": []}', encoding="utf-8")
    r = client.get("/api/v1/lab/showcase/campaigns").json()
    assert r["available"] is False and "формат" in r["reason"]


def test_report_by_slug(client):
    (client.tmp / "grid-r01.json").write_text(
        json.dumps({"slug": "grid-r01", "leaders": []}), encoding="utf-8")
    r = client.get("/api/v1/lab/showcase/campaigns/grid-r01")
    assert r.status_code == 200 and r.json()["slug"] == "grid-r01"


def test_unknown_slug_is_404_not_500(client):
    assert client.get("/api/v1/lab/showcase/campaigns/nope").status_code == 404


@pytest.mark.parametrize("bad", ["..", "..%2f..%2fetc%2fpasswd", "a.json", "A-B", "-x", "x" * 200])
def test_slug_cannot_be_a_path(client, bad):
    """Slug — имя файла из URL. Без жёсткой проверки это путь чтения.

    Кладём рядом секретный файл и убеждаемся, что до него не дотянуться."""
    (client.tmp.parent / "secret.json").write_text('{"leak": true}', encoding="utf-8")
    r = client.get(f"/api/v1/lab/showcase/campaigns/{bad}")
    assert r.status_code in (404, 422)
    assert "leak" not in r.text


def test_report_edit_is_visible_after_mtime_changes(client):
    import os
    p = client.tmp / "x1.json"
    p.write_text(json.dumps({"slug": "x1", "v": 1}), encoding="utf-8")
    assert client.get("/api/v1/lab/showcase/campaigns/x1").json()["v"] == 1
    p.write_text(json.dumps({"slug": "x1", "v": 2}), encoding="utf-8")
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 5))      # файловые системы с грубой mtime
    assert client.get("/api/v1/lab/showcase/campaigns/x1").json()["v"] == 2


def test_requires_auth(client):
    client.headers.pop("Authorization")
    assert client.get("/api/v1/lab/showcase/campaigns").status_code in (401, 403)
    assert client.get("/api/v1/lab/showcase/campaigns/x1").status_code in (401, 403)
