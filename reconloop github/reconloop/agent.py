"""The reconciliation loop, end to end.

ingest -> control checks -> settlement vs bank -> payment vs invoice
       -> refund vs credit note -> open invoices -> proposed actions
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import io
from .actions import propose_actions
from .audit import AuditLog
from .checks import dispute_checks, duplicate_checks, fee_checks
from .config import DEFAULT, Config
from .match_books import BookMatcher
from .match_settlements import build_settlements, match_settlements
from .models import Batch, Link, ReconException, ReviewCase
from .resolver import OfflineResolver, Resolver

INFO_CODES = {"SETTLEMENT_IN_TRANSIT"}


@dataclass
class RunResult:
    links: list[Link]
    exceptions: list[ReconException]
    reviews: list[ReviewCase]
    actions: list[dict[str, Any]]
    audit: AuditLog
    summary: dict[str, Any]
    resolver_log: list[dict[str, Any]] = field(default_factory=list)


class ReconAgent:
    def __init__(self, cfg: Config = DEFAULT, resolver: Resolver | None = None):
        self.cfg = cfg
        self.resolver = resolver or OfflineResolver()

    def run(self, batch: Batch) -> RunResult:
        t0 = time.perf_counter()
        audit = AuditLog()
        exceptions: list[ReconException] = []

        for q in batch.quarantined:
            entity = q.get("entity_id") or f"{q['source']}:row{q['row']}"
            exceptions.append(ReconException(
                code="MALFORMED_ROW", entity_id=entity, severity="medium", amount=0,
                detail=f"{q['source']} row {q['row']} could not be read ({q['error']}). It was set aside, not dropped.",
                evidence=q,
            ))
            audit.record("ingest", entity, "quarantined", **q)

        exceptions += fee_checks(batch.txns, self.cfg, audit)
        dup_exc, dupes = duplicate_checks(batch.txns, self.cfg, audit)
        exceptions += dup_exc
        exceptions += dispute_checks(batch.txns, audit)

        settlements = build_settlements(batch.txns)
        s_links, s_exc, s_rev = match_settlements(settlements, batch.bank, self.cfg, audit, batch.statement_end)
        exceptions += s_exc

        books = BookMatcher(self.cfg, self.resolver, audit)
        books.run(batch.txns, batch.books, dupes)
        exceptions += books.exceptions

        links = s_links + books.links
        reviews = s_rev + books.reviews
        actions = propose_actions(exceptions, reviews)
        elapsed = time.perf_counter() - t0

        summary = self._summary(batch, settlements, links, exceptions, reviews, books, elapsed)
        return RunResult(links, exceptions, reviews, actions, audit, summary, books.resolver_log)

    def _summary(self, batch, settlements, links, exceptions, reviews, books, elapsed) -> dict[str, Any]:
        payments = [t for t in batch.txns if t.type == "payment"]
        refunds = [t for t in batch.txns if t.type == "refund"]
        invoices = [d for d in batch.books if d.doc_type == "invoice"]
        linked_settlements = {l.left for l in links if l.kind == "settlement_bank"}
        linked_payments = {l.left for l in links if l.kind == "payment_invoice"}
        exc_codes = Counter(e.code for e in exceptions)
        dupes = exc_codes.get("DUPLICATE_PAYMENT", 0)
        records = len(batch.txns) + len(batch.bank) + len(batch.books) + len(batch.quarantined)
        resolver = {"name": self.resolver.name}
        for attr in ("model", "calls", "cache_hits", "tokens"):
            if hasattr(self.resolver, attr):
                resolver[attr] = getattr(self.resolver, attr)
        verdicts = Counter(e["verdict"] for e in books.resolver_log)
        return {
            "merchant": batch.meta.get("merchant"),
            "period": [d.isoformat() for d in batch.period] if batch.period else None,
            "seed": batch.meta.get("seed"),
            "difficulty": batch.meta.get("difficulty"),
            "records": records,
            "inputs": {
                "razorpay_rows": len(batch.txns), "bank_lines": len(batch.bank),
                "book_docs": len(batch.books), "quarantined": len(batch.quarantined),
            },
            "settlements": {
                "count": len(settlements),
                "net_paise": sum(s.net for s in settlements.values()),
                "in_bank": len(linked_settlements),
                "in_bank_paise": sum(s.net for sid, s in settlements.items() if sid in linked_settlements),
            },
            "payments": {
                "count": len(payments),
                "duplicates_set_aside": dupes,
                "linked": len(linked_payments),
                "paise": sum(p.amount for p in payments),
                "linked_paise": sum(p.amount for p in payments if p.entity_id in linked_payments),
                "outcomes": dict(books.stage_counts()),
            },
            "refunds": {"count": len(refunds), "linked": sum(1 for l in links if l.kind == "refund_credit_note")},
            "invoices": {"count": len(invoices)},
            "link_stages": {
                kind: dict(Counter(l.stage for l in links if l.kind == kind))
                for kind in ("settlement_bank", "payment_invoice", "refund_credit_note")
            },
            "exceptions": dict(sorted(exc_codes.items())),
            "exceptions_paise": sum(abs(e.amount) for e in exceptions if e.code not in INFO_CODES),
            "reviews": len(reviews),
            "tie_breaker": {**resolver, "cases": len(books.resolver_log), "verdicts": dict(verdicts)},
            "runtime_seconds": round(elapsed, 3),
            "records_per_second": round(records / elapsed) if elapsed > 0 else None,
        }


LINK_COLUMNS = ["kind", "left", "right", "stage", "confidence", "evidence"]
EXCEPTION_COLUMNS = ["code", "entity_id", "severity", "amount", "detail", "evidence"]
REVIEW_COLUMNS = ["case_id", "kind", "subject_id", "reason", "candidates", "resolver"]


def write_outputs(result: RunResult, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    io.write_csv(out / "links.csv", (l.row() for l in result.links), LINK_COLUMNS)
    io.write_csv(out / "exceptions.csv", (e.row() for e in result.exceptions), EXCEPTION_COLUMNS)
    io.write_csv(out / "review_queue.csv", (r.row() for r in result.reviews), REVIEW_COLUMNS)
    io.write_json(out / "actions.json", result.actions)
    io.write_json(out / "summary.json", result.summary)
    io.write_json(out / "tie_breaker_log.json", result.resolver_log)
    result.audit.write(out / "audit.jsonl")
