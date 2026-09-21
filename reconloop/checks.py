"""Control checks that need no matching: fees, GST, duplicate captures, disputes."""

from __future__ import annotations

from collections import defaultdict

from .audit import AuditLog
from .config import Config
from .models import ReconException, RzpTxn
from .money import expected_fee, gst_on, inr


def fee_checks(txns: list[RzpTxn], cfg: Config, audit: AuditLog) -> list[ReconException]:
    out: list[ReconException] = []
    for t in txns:
        if t.type != "payment":
            continue
        base = t.fee - t.tax
        gst_for_base = gst_on(base, cfg.rate_card)
        if abs(t.tax - gst_for_base) > cfg.tol.tax_paise:
            out.append(ReconException(
                code="TAX_MISMATCH", entity_id=t.entity_id, severity="medium",
                amount=t.tax - gst_for_base,
                detail=f"GST {inr(t.tax)} on a fee base of {inr(base)}; 18% would be {inr(gst_for_base)}.",
                evidence={"fee": t.fee, "tax": t.tax, "base": base, "expected_tax": gst_for_base},
            ))
            audit.record("checks", t.entity_id, "TAX_MISMATCH", base=base, tax=t.tax, expected=gst_for_base)
        exp = expected_fee(t.amount, t.method, cfg.rate_card)
        if exp is None:
            audit.record("checks", t.entity_id, "fee_skipped", reason=f"no contract rate for method {t.method!r}")
            continue
        exp_base, exp_gst = exp
        if abs(base - exp_base) > cfg.tol.fee_paise:
            overcharge = t.fee - (exp_base + exp_gst)
            out.append(ReconException(
                code="FEE_MISMATCH", entity_id=t.entity_id, severity="medium", amount=overcharge,
                detail=(f"Fee {inr(t.fee)} on {inr(t.amount)} via {t.method}; the contract implies "
                        f"{inr(exp_base + exp_gst)} ({inr(overcharge)} difference)."),
                evidence={"fee": t.fee, "base": base, "expected_base": exp_base, "method": t.method},
            ))
            audit.record("checks", t.entity_id, "FEE_MISMATCH", base=base, expected_base=exp_base)
    return out


def duplicate_checks(txns: list[RzpTxn], cfg: Config, audit: AuditLog) -> tuple[list[ReconException], set[str]]:
    """Two captured payments on one Razorpay order for the same amount: keep the first."""
    by_order: dict[str, list[RzpTxn]] = defaultdict(list)
    for t in txns:
        if t.type == "payment" and t.order_id:
            by_order[t.order_id].append(t)
    out: list[ReconException] = []
    dupes: set[str] = set()
    for order_id, group in by_order.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda t: (t.created_at, t.entity_id))
        first = group[0]
        for t in group[1:]:
            if t.amount != first.amount:
                audit.record("checks", t.entity_id, "same_order_different_amount", order_id=order_id)
                continue
            gap_min = (t.created_at - first.created_at) / 60
            quick = gap_min <= cfg.tol.duplicate_window_minutes
            dupes.add(t.entity_id)
            out.append(ReconException(
                code="DUPLICATE_PAYMENT", entity_id=t.entity_id, severity="high" if quick else "medium",
                amount=t.amount,
                detail=(f"Order {order_id} was captured twice for {inr(t.amount)}, "
                        f"{gap_min:.0f} min apart. First capture {first.entity_id} is kept."),
                evidence={"order_id": order_id, "original": first.entity_id, "gap_minutes": round(gap_min, 1)},
            ))
            audit.record("checks", t.entity_id, "DUPLICATE_PAYMENT", original=first.entity_id, gap_minutes=round(gap_min, 1))
    return out, dupes


def dispute_checks(txns: list[RzpTxn], audit: AuditLog) -> list[ReconException]:
    out = []
    for t in txns:
        if t.type == "adjustment" and t.dispute_id and t.debit > 0:
            out.append(ReconException(
                code="DISPUTE_DEBIT", entity_id=t.entity_id, severity="high", amount=t.debit,
                detail=f"Chargeback debit of {inr(t.debit)} against {t.payment_id} (dispute {t.dispute_id}).",
                evidence={"payment_id": t.payment_id, "dispute_id": t.dispute_id},
            ))
            audit.record("checks", t.entity_id, "DISPUTE_DEBIT", payment_id=t.payment_id)
    return out
