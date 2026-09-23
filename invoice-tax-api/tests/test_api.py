import os
import sys

import pytest

# 在导入 app 之前把 SQLite 指到临时位置，避免触碰 /data
os.environ.setdefault("SQLITE_PATH", "/tmp/invoice-test/invoices.db")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    if os.path.exists(os.environ["SQLITE_PATH"]):
        os.remove(os.environ["SQLITE_PATH"])
    db.init_db()
    with TestClient(app) as c:
        yield c


def make_payload(invoice_no="INV-001", currency="CNY", lines=None):
    if lines is None:
        lines = [
            {"description": "货物A", "currency": "CNY", "net": "199.99", "tax_rate": "0.13"},
            {"description": "货物B", "currency": "CNY", "net": "10.00", "tax_rate": "0.13"},
        ]
    return {"invoice_no": invoice_no, "currency": currency, "lines": lines}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_create_get_list_recompute(client):
    r = client.post("/api/v1/invoices", json=make_payload("INV-API-1"))
    assert r.status_code == 201, r.text
    created = r.json()
    inv_id = created["id"]
    assert created["result"]["total_tax"] == "27.30"
    assert created["check_ok"] is True

    # 按 ID 查
    r = client.get(f"/api/v1/invoices/{inv_id}")
    assert r.status_code == 200
    assert r.json()["invoice_no"] == "INV-API-1"

    # 按发票号查
    r = client.get("/api/v1/invoices/by-no/INV-API-1")
    assert r.status_code == 200
    assert r.json()["id"] == inv_id

    # 列表分页
    r = client.get("/api/v1/invoices", params={"page": 1, "page_size": 10})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert any(i["id"] == inv_id for i in body["items"])

    # 复算
    r = client.post(f"/api/v1/invoices/{inv_id}/recompute")
    assert r.status_code == 200
    body = r.json()
    assert body["identical"] is True
    assert body["recomputed_total_tax"] == "27.30"


def test_duplicate_invoice_no_conflict(client):
    r = client.post("/api/v1/invoices", json=make_payload("INV-DUP"))
    assert r.status_code == 201
    r = client.post("/api/v1/invoices", json=make_payload("INV-DUP"))
    assert r.status_code == 409


def test_404(client):
    r = client.get("/api/v1/invoices/99999999")
    assert r.status_code == 404


def test_cross_currency_create(client):
    payload = make_payload(
        "INV-FX-1",
        currency="CNY",
        lines=[
            {"description": "进口件", "currency": "USD", "net": "100.00", "tax_rate": "0.10", "fx_rate": "7.1000"},
            {"description": "本地件", "currency": "CNY", "net": "50.00", "tax_rate": "0.13"},
        ],
    )
    r = client.post("/api/v1/invoices", json=payload)
    assert r.status_code == 201, r.text
    result = r.json()["result"]
    assert result["mixed_currency"] is True
    assert result["total_tax"] == "77.50"  # 10*7.1 + 6.5
    usd = [c for c in result["currency_totals"] if c["currency"] == "USD"][0]
    assert usd["tax_total"] == "10.00"  # 原币金额保留


def test_validation_rejects_float(client):
    bad = make_payload(
        "INV-BAD",
        lines=[{"currency": "CNY", "net": 199.99, "tax_rate": "0.13"}],
    )
    r = client.post("/api/v1/invoices", json=bad)
    assert r.status_code == 422
