"""Did every Razorpay settlement actually land in the bank?

Purely deterministic. Numbers and dates do not need a language model.
Order of evidence: full UTR, then a unique UTR fragment, then exact amount
inside the lag window, using the bank lag learned from confident matches
to break ties.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

from .audit import AuditLog
from .config import Config
from .models import BankLine, Link, ReconException, ReviewCase, RzpTxn, Settlement
from .money import inr
from .normalize import alnum, is_razorpay_credit, utr_evidence, utr_fragments


def build_settlements(txns: list[RzpTxn]) -> dict[str, Settlement]:
    out: dict[str, Settlement] = {}
    for t in txns:
        if not t.settled or not t.settlement_id or t.settled_date is None:
            continue
        s = out.get(t.settlement_id)
        if s is None:
            s = out[t.settlement_id] = Settlement(t.settlement_id, t.settlement_utr, t.settled_date, 0)
        s.net += t.credit - t.debit
        s.txn_ids.append(t.entity_id)
    return out


def match_settlements(
    settlements: dict[str, Settlement],
    bank: list[BankLine],
    cfg: Config,
    audit: AuditLog,
    statement_end: date | None = None,
) -> tuple[list[Link], list[ReconException], list[ReviewCase]]:
    links: list[Link] = []
    exceptions: list[ReconException] = []
    reviews: list[ReviewCase] = []
    lines = {b.line_id: b for b in bank if b.credit > 0 and is_razorpay_credit(b.narration, b.ref_no)}
    open_s = dict(sorted(settlements.items(), key=lambda kv: (kv[1].settled_date, kv[0])))
    open_l = dict(lines)
    end = statement_end or max((b.txn_date for b in bank), default=None)
    lags: list[int] = []

    def take(s: Settlement, b: BankLine, stage: str, conf: float, **ev) -> None:
        lag = (b.txn_date - s.settled_date).days
        lags.append(lag)
        links.append(Link("settlement_bank", s.settlement_id, b.line_id, stage, conf,
                          {"utr": s.utr, "lag_days": lag, "net": s.net, "credited": b.credit, **ev}))
        audit.record("settlement_bank", s.settlement_id, f"linked:{stage}", line=b.line_id, lag_days=lag)
        if b.credit != s.net:
            diff = s.net - b.credit
            exceptions.append(ReconException(
                code="SETTLEMENT_AMOUNT_MISMATCH", entity_id=s.settlement_id, severity="high", amount=diff,
                detail=(f"Settlement {s.settlement_id} (UTR {s.utr}) should credit {inr(s.net)}; "
                        f"bank line {b.line_id} shows {inr(b.credit)}. Short by {inr(diff)}."),
                evidence={"line_id": b.line_id, "expected": s.net, "credited": b.credit},
            ))
            audit.record("settlement_bank", s.settlement_id, "SETTLEMENT_AMOUNT_MISMATCH", diff=diff)
        del open_s[s.settlement_id]
        del open_l[b.line_id]

    # Pass 1: full UTR in narration or reference column.
    for sid, s in list(open_s.items()):
        hits = [b for b in open_l.values() if utr_evidence(b.narration, b.ref_no, s.utr) == "exact"]
        if len(hits) == 1:
            take(s, hits[0], "utr_exact", 1.0)

    # Pass 2: UTR fragment. A fragment that fits exactly one settlement is identity
    # evidence on its own; a shared fragment (bank code + month) only counts with
    # an exact amount.
    all_utrs = {sid: alnum(s.utr) for sid, s in settlements.items() if s.utr}
    for lid, b in list(open_l.items()):
        owners: set[str] = set()
        for frag in utr_fragments(b.narration, b.ref_no):
            owners |= {sid for sid, u in all_utrs.items() if frag in u and frag != u}
        if not owners:
            continue
        live = [open_s[sid] for sid in owners if sid in open_s]
        in_window = lambda s: 0 <= (b.txn_date - s.settled_date).days <= cfg.tol.bank_lag_days  # noqa: E731
        if len(owners) == 1 and len(live) == 1 and in_window(live[0]):
            take(live[0], b, "utr_fragment_unique", 0.97, fragment_owners=1)
            continue
        same_amt = [s for s in live if s.net == b.credit and in_window(s)]
        if len(same_amt) == 1:
            take(same_amt[0], b, "utr_fragment_amount", 0.93, fragment_owners=len(owners))

    # Pass 3: exact amount inside the lag window, tie-broken by the usual lag.
    usual_lag = Counter(lags).most_common(1)[0][0] if lags else 0

    def cands(s: Settlement) -> list[BankLine]:
        return [
            b for b in open_l.values()
            if b.credit == s.net and 0 <= (b.txn_date - s.settled_date).days <= cfg.tol.bank_lag_days
        ]

    def key(s: Settlement, b: BankLine) -> tuple[int, int]:
        lag = (b.txn_date - s.settled_date).days
        return (0 if lag == usual_lag else 1, lag)

    changed = True
    while changed:
        changed = False
        by_line: dict[str, list[Settlement]] = defaultdict(list)
        pref: dict[str, BankLine] = {}
        for s in open_s.values():
            cs = sorted(cands(s), key=lambda b: key(s, b))
            if not cs:
                continue
            if len(cs) == 1 or key(s, cs[0]) < key(s, cs[1]):
                pref[s.settlement_id] = cs[0]
            for b in cs:
                by_line[b.line_id].append(s)
        for sid, b in list(pref.items()):
            if sid not in open_s or b.line_id not in open_l:
                continue
            rivals = sorted(by_line[b.line_id], key=lambda s: key(s, b))
            best = rivals[0]
            if best.settlement_id == sid and (len(rivals) == 1 or key(rivals[0], b) < key(rivals[1], b)):
                take(open_s[sid], b, "amount_lag", 0.85 if len(rivals) == 1 else 0.8,
                     usual_lag=usual_lag, rivals=len(rivals))
                changed = True

    for sid, s in list(open_s.items()):
        cs = cands(s)
        if cs:
            reviews.append(ReviewCase(
                case_id=f"RV-S-{sid}", kind="settlement_bank", subject_id=sid,
                reason="Several bank credits fit this settlement's amount and date; none carries its UTR.",
                candidates=[{"id": b.line_id, "date": b.txn_date.isoformat(), "credit": b.credit,
                             "narration": b.narration} for b in cs],
            ))
            audit.record("settlement_bank", sid, "review", candidates=[b.line_id for b in cs])
            continue
        overdue = end is None or (end - s.settled_date).days >= cfg.tol.bank_lag_days
        if overdue:
            exceptions.append(ReconException(
                code="SETTLEMENT_NOT_IN_BANK", entity_id=sid, severity="high", amount=s.net,
                detail=(f"Settlement {sid} of {inr(s.net)} (UTR {s.utr}, settled {s.settled_date:%d %b}) "
                        f"has no matching bank credit within T+{cfg.tol.bank_lag_days}."),
                evidence={"utr": s.utr, "settled": s.settled_date.isoformat()},
            ))
            audit.record("settlement_bank", sid, "SETTLEMENT_NOT_IN_BANK")
        else:
            exceptions.append(ReconException(
                code="SETTLEMENT_IN_TRANSIT", entity_id=sid, severity="info", amount=s.net,
                detail=f"Settlement {sid} of {inr(s.net)} is within the T+{cfg.tol.bank_lag_days} window; check the next statement.",
                evidence={"utr": s.utr},
            ))
            audit.record("settlement_bank", sid, "in_transit")

    reviewed_lines = {c["id"] for r in reviews for c in r.candidates}
    for lid, b in open_l.items():
        if lid in reviewed_lines:
            continue
        exceptions.append(ReconException(
            code="UNMATCHED_BANK_CREDIT", entity_id=lid, severity="medium", amount=b.credit,
            detail=f"Bank credit {inr(b.credit)} on {b.txn_date:%d %b} looks like Razorpay ('{b.narration}') but matches no settlement.",
            evidence={"narration": b.narration, "ref_no": b.ref_no},
        ))
        audit.record("settlement_bank", lid, "UNMATCHED_BANK_CREDIT")
    return links, exceptions, reviews
