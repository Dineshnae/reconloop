"""Load the three sources. Bad rows are quarantined, never silently dropped."""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from .models import Batch, BankLine, BookDoc, RzpTxn
from .money import parse_rupees

RZP_FILE = "razorpay_recon.csv"
BANK_FILE = "bank_statement.csv"
BOOKS_FILE = "books.csv"
META_FILE = "meta.json"
TRUTH_FILE = "ground_truth.json"

RZP_COLUMNS = [
    "entity_id", "type", "debit", "credit", "amount", "currency", "fee", "tax",
    "on_hold", "settled", "created_at", "settled_at", "settlement_id",
    "settlement_utr", "order_id", "order_receipt", "method", "notes",
    "payment_id", "dispute_id", "description", "email", "contact",
]
BANK_COLUMNS = ["line_id", "txn_date", "narration", "ref_no", "debit", "credit", "balance"]
BOOK_COLUMNS = [
    "doc_no", "doc_type", "doc_date", "customer_name", "customer_email",
    "customer_phone", "amount", "against_doc",
]


def _none(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


def _int(v: str | None) -> int:
    v = _none(v)
    return int(v) if v is not None else 0


def _bool(v: str | None) -> bool:
    return (v or "").strip().lower() in ("true", "1", "yes")


def parse_date(v: str) -> date:
    v = v.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"not a date: {v!r}")


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_batch(folder: str | Path) -> Batch:
    folder = Path(folder)
    quarantined: list[dict[str, Any]] = []

    txns: list[RzpTxn] = []
    for i, r in enumerate(_read(folder / RZP_FILE), start=2):
        try:
            notes_raw = _none(r.get("notes"))
            notes = json.loads(notes_raw) if notes_raw else {}
            if not isinstance(notes, dict):
                notes = {"text": str(notes)}
            txns.append(
                RzpTxn(
                    entity_id=r["entity_id"].strip(),
                    type=r["type"].strip().lower(),
                    debit=_int(r["debit"]),
                    credit=_int(r["credit"]),
                    amount=_int(r["amount"]),
                    currency=(r.get("currency") or "INR").strip(),
                    fee=_int(r["fee"]),
                    tax=_int(r["tax"]),
                    on_hold=_bool(r.get("on_hold")),
                    settled=_bool(r.get("settled")),
                    created_at=_int(r["created_at"]),
                    settled_at=_int(r["settled_at"]) if _none(r.get("settled_at")) else None,
                    settlement_id=_none(r.get("settlement_id")),
                    settlement_utr=_none(r.get("settlement_utr")),
                    order_id=_none(r.get("order_id")),
                    order_receipt=_none(r.get("order_receipt")),
                    method=_none(r.get("method")),
                    notes=notes,
                    payment_id=_none(r.get("payment_id")),
                    dispute_id=_none(r.get("dispute_id")),
                    description=_none(r.get("description")),
                    email=_none(r.get("email")),
                    contact=_none(r.get("contact")),
                )
            )
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            quarantined.append(
                {"source": RZP_FILE, "row": i, "entity_id": r.get("entity_id", ""), "error": str(exc)}
            )

    bank: list[BankLine] = []
    for i, r in enumerate(_read(folder / BANK_FILE), start=2):
        try:
            bank.append(
                BankLine(
                    line_id=r["line_id"].strip(),
                    txn_date=parse_date(r["txn_date"]),
                    narration=(r.get("narration") or "").strip(),
                    ref_no=(r.get("ref_no") or "").strip(),
                    debit=parse_rupees(r.get("debit")),
                    credit=parse_rupees(r.get("credit")),
                    balance=parse_rupees(r["balance"]) if _none(r.get("balance")) else None,
                )
            )
        except (ValueError, KeyError) as exc:
            quarantined.append(
                {"source": BANK_FILE, "row": i, "entity_id": (r.get("line_id") or "").strip(), "error": str(exc)}
            )

    books: list[BookDoc] = []
    for i, r in enumerate(_read(folder / BOOKS_FILE), start=2):
        try:
            books.append(
                BookDoc(
                    doc_no=r["doc_no"].strip(),
                    doc_type=r["doc_type"].strip().lower(),
                    doc_date=parse_date(r["doc_date"]),
                    customer_name=(r.get("customer_name") or "").strip(),
                    customer_email=(r.get("customer_email") or "").strip().lower(),
                    customer_phone=(r.get("customer_phone") or "").strip(),
                    amount=parse_rupees(r["amount"]),
                    against_doc=_none(r.get("against_doc")),
                )
            )
        except (ValueError, KeyError) as exc:
            quarantined.append(
                {"source": BOOKS_FILE, "row": i, "entity_id": (r.get("doc_no") or "").strip(), "error": str(exc)}
            )

    period = None
    statement_end = None
    meta: dict[str, Any] = {}
    meta_path = folder / META_FILE
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        period = (parse_date(meta["period_start"]), parse_date(meta["period_end"]))
        if meta.get("statement_end"):
            statement_end = parse_date(meta["statement_end"])
    return Batch(txns=txns, bank=bank, books=books, quarantined=quarantined, period=period,
                 statement_end=statement_end, meta=meta)


def load_truth(folder: str | Path) -> dict[str, Any] | None:
    p = Path(folder) / TRUTH_FILE
    return json.loads(p.read_text()) if p.exists() else None


def write_csv(path: Path, rows: Iterable[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
