"""SQLite 持久化。结果以 JSON 存储，金额全程是字符串，不经过 float。"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

_DB_PATH = os.getenv("SQLITE_PATH", "/data/invoices.db")
_LOCK = threading.Lock()


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invoices (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_no  TEXT NOT NULL UNIQUE,
                currency    TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                request     TEXT NOT NULL,
                result      TEXT NOT NULL,
                check_ok    INTEGER NOT NULL DEFAULT 1
            )
            """
        )


def insert_invoice(
    invoice_no: str,
    currency: str,
    request: dict,
    result: dict,
    check_ok: bool,
) -> int:
    with _LOCK, _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO invoices (invoice_no, currency, created_at, request, result, check_ok)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                invoice_no,
                currency,
                datetime.now(timezone.utc).isoformat(),
                json.dumps(request, ensure_ascii=False),
                json.dumps(result, ensure_ascii=False),
                1 if check_ok else 0,
            ),
        )
        return int(cur.lastrowid)


def get_invoice_by_id(invoice_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM invoices WHERE id = ?", (invoice_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None


def get_invoice_by_no(invoice_no: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM invoices WHERE invoice_no = ?", (invoice_no,)
        ).fetchone()
        return _row_to_dict(row) if row else None


def list_invoices(limit: int, offset: int, currency: str | None = None) -> tuple[list[dict], int]:
    with _connect() as conn:
        if currency:
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM invoices WHERE currency = ?",
                (currency.upper(),),
            ).fetchone()["c"]
            rows = conn.execute(
                "SELECT * FROM invoices WHERE currency = ? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (currency.upper(), limit, offset),
            ).fetchall()
        else:
            total = conn.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"]
            rows = conn.execute(
                "SELECT * FROM invoices ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [_row_to_dict(r) for r in rows], int(total)


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "invoice_no": row["invoice_no"],
        "currency": row["currency"],
        "created_at": row["created_at"],
        "request": json.loads(row["request"]),
        "result": json.loads(row["result"]),
        "check_ok": bool(row["check_ok"]),
    }
