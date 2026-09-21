"""Plain dataclasses. No behaviour beyond small conveniences."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone, timedelta
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))


def ist_date(ts: int | None) -> date | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), tz=IST).date()


@dataclass
class RzpTxn:
    """One row of Razorpay's settlement recon report (GET /v1/settlements/recon/combined).

    `email` and `contact` are not in the recon report; they come from the
    Payments API (payment entity) and are joined in by the loader.
    """

    entity_id: str
    type: str                      # payment | refund | adjustment
    debit: int
    credit: int
    amount: int
    currency: str
    fee: int
    tax: int
    on_hold: bool
    settled: bool
    created_at: int
    settled_at: int | None
    settlement_id: str | None
    settlement_utr: str | None
    order_id: str | None
    order_receipt: str | None
    method: str | None
    notes: dict[str, Any]
    payment_id: str | None
    dispute_id: str | None
    description: str | None
    email: str | None = None
    contact: str | None = None

    @property
    def created_date(self) -> date:
        return ist_date(self.created_at)  # type: ignore[return-value]

    @property
    def settled_date(self) -> date | None:
        return ist_date(self.settled_at)


@dataclass
class BankLine:
    line_id: str
    txn_date: date
    narration: str
    ref_no: str
    debit: int
    credit: int
    balance: int | None


@dataclass
class BookDoc:
    doc_no: str
    doc_type: str                  # invoice | credit_note
    doc_date: date
    customer_name: str
    customer_email: str
    customer_phone: str
    amount: int
    against_doc: str | None = None


@dataclass
class Settlement:
    settlement_id: str
    utr: str | None
    settled_date: date
    net: int                       # sum(credit) - sum(debit), paise
    txn_ids: list[str] = field(default_factory=list)


@dataclass
class Link:
    kind: str                      # settlement_bank | payment_invoice | refund_credit_note
    left: str
    right: str
    stage: str
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)

    def row(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence"] = _compact(self.evidence)
        return d


@dataclass
class ReconException:
    code: str
    entity_id: str
    severity: str                  # high | medium | low | info
    amount: int                    # paise at stake
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def row(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence"] = _compact(self.evidence)
        return d


@dataclass
class ReviewCase:
    case_id: str
    kind: str
    subject_id: str
    reason: str
    candidates: list[dict[str, Any]]
    resolver: dict[str, Any] | None = None

    def row(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "kind": self.kind,
            "subject_id": self.subject_id,
            "reason": self.reason,
            "candidates": ";".join(c["id"] for c in self.candidates),
            "resolver": _compact(self.resolver or {}),
        }


@dataclass
class Batch:
    txns: list[RzpTxn]
    bank: list[BankLine]
    books: list[BookDoc]
    quarantined: list[dict[str, Any]] = field(default_factory=list)
    period: tuple[date, date] | None = None
    statement_end: date | None = None
    meta: dict[str, Any] = field(default_factory=dict)


def _compact(d: dict[str, Any]) -> str:
    import json

    return json.dumps(d, sort_keys=True, default=str, separators=(",", ":"))
