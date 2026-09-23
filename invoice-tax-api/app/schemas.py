"""Pydantic 请求/响应模型。金额一律以字符串接收，内部转 Decimal。"""
from __future__ import annotations

import re
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

_DECIMAL_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


class LineIn(BaseModel):
    description: str | None = Field(default=None, max_length=200)
    currency: str = Field(min_length=2, max_length=8)
    net: str = Field(description="不含税净额，十进制字符串，如 199.99")
    tax_rate: str = Field(description="税率，0~1 之间，如 0.13")
    fx_rate: str | None = Field(default=None, description="行币种 -> 发票币种的汇率")

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("net", "tax_rate", "fx_rate")
    @classmethod
    def _decimal_string(cls, v: str | None) -> str:
        if v is None:
            return v
        v = v.strip()
        if not _DECIMAL_RE.match(v):
            raise ValueError("必须是十进制数字字符串，禁止科学计数法/float 特殊值")
        return v

    @field_validator("description")
    @classmethod
    def _desc(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
        return v or None


class InvoiceCreate(BaseModel):
    invoice_no: str = Field(min_length=1, max_length=64)
    currency: str = Field(min_length=2, max_length=8)
    lines: list[LineIn] = Field(min_length=1, max_length=500)

    @field_validator("invoice_no")
    @classmethod
    def _invoice_no(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("invoice_no 不能为空")
        return v

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.strip().upper()


class LineOut(BaseModel):
    index: int
    description: str
    currency: str
    min_unit: str
    net: str
    tax_rate: str
    tax_original_exact: str
    tax_original_rounded: str
    fx_rate_to_invoice: str
    tax_invoice_exact: str
    tax_invoice_rounded: str
    allocation_adjust_units: int
    allocation_adjust_amount: str
    tax_final: str


class CurrencyTotalOut(BaseModel):
    currency: str
    min_unit: str
    net_total: str
    tax_total: str


class TaxResultOut(BaseModel):
    invoice_currency: str
    invoice_min_unit: str
    mixed_currency: bool
    total_tax_exact: str
    total_tax: str
    sum_line_rounded_tax: str
    residual: str
    residual_units: int
    residual_rule: str
    currency_totals: list[CurrencyTotalOut]
    lines: list[LineOut]


class InvoiceOut(BaseModel):
    id: int
    invoice_no: str
    currency: str
    created_at: str
    request: dict
    result: TaxResultOut
    check_ok: bool
