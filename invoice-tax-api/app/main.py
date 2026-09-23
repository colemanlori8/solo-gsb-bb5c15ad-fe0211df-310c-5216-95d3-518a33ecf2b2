"""发票税额计算 API — FastAPI 入口。

端点：
  GET  /health                              健康检查
  POST /api/v1/invoices                     创建并计算发票
  GET  /api/v1/invoices                     分页列出（?page=1&page_size=20&currency=USD）
  GET  /api/v1/invoices/{invoice_id}        按自增 ID 查询
  GET  /api/v1/invoices/by-no/{invoice_no}  按发票号查询
  POST /api/v1/invoices/{invoice_id}/recompute
                                            用原始请求重新计算并比对（可复算）
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from . import db
from .schemas import InvoiceCreate, InvoiceOut
from .tax_engine import (
    TaxCalculationError,
    calculate_invoice,
    load_currency_units,
)

_CURRENCY_UNITS = None


def _units():
    global _CURRENCY_UNITS
    if _CURRENCY_UNITS is None:
        _CURRENCY_UNITS = load_currency_units()
    return _CURRENCY_UNITS


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    _units()
    yield


app = FastAPI(
    title="发票税额计算 API",
    version="1.0.0",
    description="Decimal 精确计算、按币种最小单位舍入、固定残差分摊、跨币种换算、SQLite 持久化",
    lifespan=lifespan,
)


@app.exception_handler(TaxCalculationError)
async def _tax_error_handler(request, exc: TaxCalculationError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/health")
def health():
    return {"status": "ok"}


def _normalize_request(payload: InvoiceCreate) -> list[dict]:
    lines = []
    for line in payload.lines:
        lines.append(
            {
                "description": line.description,
                "currency": line.currency,
                "net": line.net,
                "tax_rate": line.tax_rate,
                "fx_rate": line.fx_rate or "1",
            }
        )
    return lines


def _result_is_identical(a: dict, b: dict) -> bool:
    """复算结果与已存结果逐字段比对。"""
    return a == b


@app.post("/api/v1/invoices", status_code=201, response_model=InvoiceOut)
def create_invoice(payload: InvoiceCreate):
    if db.get_invoice_by_no(payload.invoice_no):
        raise HTTPException(status_code=409, detail=f"发票号已存在: {payload.invoice_no}")

    lines = _normalize_request(payload)
    try:
        result = calculate_invoice(lines, payload.currency, _units())
    except TaxCalculationError:
        raise

    # 创建时自带一次复算校验（防御性）
    verify = calculate_invoice(lines, payload.currency, _units())
    check_ok = _result_is_identical(result, verify)

    request_snapshot = {
        "invoice_no": payload.invoice_no,
        "currency": payload.currency,
        "lines": lines,
    }
    invoice_id = db.insert_invoice(
        payload.invoice_no, payload.currency, request_snapshot, result, check_ok
    )
    row = db.get_invoice_by_id(invoice_id)
    return row


@app.get("/api/v1/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: int):
    row = db.get_invoice_by_id(invoice_id)
    if not row:
        raise HTTPException(status_code=404, detail="发票不存在")
    return row


@app.get("/api/v1/invoices/by-no/{invoice_no}", response_model=InvoiceOut)
def get_invoice_by_number(invoice_no: str):
    row = db.get_invoice_by_no(invoice_no)
    if not row:
        raise HTTPException(status_code=404, detail="发票不存在")
    return row


@app.get("/api/v1/invoices")
def list_invoices(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    currency: str | None = Query(default=None),
):
    offset = (page - 1) * page_size
    rows, total = db.list_invoices(page_size, offset, currency)
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": rows,
    }


@app.post("/api/v1/invoices/{invoice_id}/recompute")
def recompute_invoice(invoice_id: int):
    row = db.get_invoice_by_id(invoice_id)
    if not row:
        raise HTTPException(status_code=404, detail="发票不存在")

    req = row["request"]
    fresh = calculate_invoice(req["lines"], req["currency"], _units())
    identical = _result_is_identical(fresh, row["result"])
    return {
        "id": row["id"],
        "invoice_no": row["invoice_no"],
        "stored_total_tax": row["result"]["total_tax"],
        "recomputed_total_tax": fresh["total_tax"],
        "identical": identical,
        "check_ok": row["check_ok"],
        "recomputed_result": fresh,
    }
