import pytest
from fastapi.testclient import TestClient

from stockbot.web.app import create_app
from tests.test_engine import make_engine


@pytest.fixture
def client(settings, db):
    eng, hub = make_engine(settings, db)
    return TestClient(create_app(settings, eng), raise_server_exceptions=True)


def test_open_mode_research_and_gates(client):
    assert client.get("/").status_code == 200 and "تداول" in client.get("/").text
    me = client.get("/api/me").json()
    assert me["auth"] == "open" and me["user"] is None
    d = client.get("/api/analyze/aapl").json()
    assert d["summary"]["ticker"] == "AAPL" and d["summary"]["sharia"] == "HALAL" and d["summary"]["xtb"] == "UNVERIFIED"
    assert d["summary"]["investable"] is False and len(d["embeds"]) == 7
    assert len(d["chart"]["close"]) > 200 and "ema200" in d["chart"] and d["news"][0]["url"].startswith("https://")
    s = client.get("/api/sharia/AAPL?method=FTSE").json()
    assert s["verdict"]["method"] == "FTSE" and s["verdict"]["reference"].startswith("https://") and "AAOIFI" in s["methods"]
    assert client.get("/api/sharia/AAPL?method=XYZ").status_code == 400
    assert client.get("/api/news/AAPL").status_code == 200
    c = client.get("/api/compare?a=AAPL&b=JPM").json()
    assert c["a"]["ticker"] == "AAPL" and c["b"]["sharia"] == "NON_COMPLIANT"
    assert client.get("/api/opportunities").json()["results"] == []
    assert client.get("/api/help").json()["sections"][0]["key"] == "about"
    assert client.get("/api/xtb/AAPL").json()["investable"] is False


def test_open_mode_blocks_personal_and_admin(client):
    assert client.get("/api/portfolio").status_code == 401
    assert client.post("/api/scan").status_code == 403
    assert client.get("/api/status").status_code == 403
    assert client.get("/api/analyze/AA%20PL").status_code == 400
    assert client.get("/static/../.env").status_code in (404, 400)


def test_code_login_and_personal_features(settings, db, monkeypatch):
    import dataclasses

    s2 = dataclasses.replace(settings, web_auth="code", web_access_code="secret-123", web_public_url="http://testserver")
    eng, hub = make_engine(s2, db)
    c = TestClient(create_app(s2, eng), raise_server_exceptions=True)
    assert c.get("/api/portfolio").status_code == 401
    assert c.post("/auth/code", json={"code": "wrong"}).status_code == 401
    assert c.post("/auth/code", json={"code": "secret-123"}).json()["ok"] is True
    assert c.get("/api/me").json()["user"]["name"] == "عضو"
    assert c.post("/api/portfolio", json={"symbol": "aapl", "qty": 10, "avg_price": 50}).status_code == 200
    pf = c.get("/api/portfolio").json()
    assert pf["positions"][0]["ticker"] == "AAPL" and pf["total_value"] > 0 and pf["embeds"]
    assert c.post("/api/watchlist", json={"symbol": "JPM", "note": "bank"}).status_code == 200
    wl = c.get("/api/watchlist").json()["items"]
    assert wl[0]["ticker"] == "JPM" and wl[0]["sharia"] == "NON_COMPLIANT"
    a = c.post("/api/alerts", json={"symbol": "AAPL", "kind": "target", "threshold": 10}).json()
    assert c.post("/api/alerts", json={"symbol": "AAPL", "kind": "target"}).status_code == 400
    al = c.get("/api/alerts").json()
    assert al["rules"][0]["id"] == a["id"] and "AAPL" in al["auto"] and "JPM" in al["auto"]
    assert c.delete(f"/api/alerts/{a['id']}").json()["removed"] == 1
    assert c.post("/api/ask", json={"question": "hello"}).status_code == 503   # no Gemini key in tests
    # no ADMIN_USER_IDS configured -> code user is admin: scan + xtb registry work
    assert c.get("/api/status").status_code == 200
    r = c.post("/api/xtb", json={"symbol": "AAPL", "status": "REAL_STOCK", "market": "NASDAQ", "currency": "USD"}).json()
    assert r["status"] == "REAL_STOCK"
    assert c.get("/api/analyze/AAPL").json()["summary"]["investable"] is True
    assert c.post("/auth/logout").json()["ok"] and c.get("/api/portfolio").status_code == 401
