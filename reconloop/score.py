"""Score a run against the generator's answer key.

Rules the grader applies:
  - A link on an undecidable case counts as a wrong link even if it happens to be
    right. Guessing is the failure mode, not bad luck.
  - Interchangeable invoices (same customer, amount and day) are one answer.
  - Sending a case to review is never "wrong", but it costs reviewer time.
  - SETTLEMENT_IN_TRANSIT is informational and not scored.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .agent import RunResult
from .config import DEFAULT, Config

KINDS = ("settlement_bank", "payment_invoice", "refund_credit_note")
NOT_SCORED = {"SETTLEMENT_IN_TRANSIT"}

SCENARIO_LABELS = {
    "exact_receipt": "Receipt is the invoice number",
    "receipt_drift": "Receipt in another format",
    "notes_ref": "Invoice number only in notes",
    "contact_only": "No reference, payer contact matches",
    "split_payment": "One invoice paid in parts",
    "combined_payment": "One payment for several invoices",
    "repeat_purchase": "Same customer, same amount, same day",
    "duplicate_original": "First capture of a double charge",
    "duplicate_twin": "Second capture of a double charge",
    "amount_mismatch": "Paid amount differs from invoice",
    "missing_invoice": "Invoice never raised",
    "coincidental_amount": "Stranger's payment, same amount as an open invoice",
    "name_trap_easy": "Payer note names the customer",
    "name_trap_semantic": "Payer note needs reading (nickname, on behalf of)",
    "name_trap_undecidable": "Nobody can tell (should go to a human)",
}


def _ratio(a: int, b: int) -> float:
    return round(a / b, 4) if b else 1.0


def score(truth: dict[str, Any], result: RunResult, cfg: Config = DEFAULT) -> dict[str, Any]:
    eq = {d: min(cls) for cls in truth.get("equivalent_invoices", []) for d in cls}
    undecidable = {e["subject"] for e in truth.get("expected_review", [])}
    related = {d for e in truth.get("expected_review", []) for d in e["related"]}

    def canon(kind: str, a: str, b: str) -> tuple[str, str]:
        return (a, eq.get(b, b)) if kind == "payment_invoice" else (a, b)

    links: dict[str, Any] = {}
    wrong_total = 0
    for kind in KINDS:
        truth_set = {
            canon(kind, a, b) for a, b in truth["links"][kind]
            if not (kind == "payment_invoice" and a in undecidable)
        }
        pred_all = {canon(kind, l.left, l.right) for l in result.links if l.kind == kind}
        guessed = {x for x in pred_all if x[0] in undecidable}
        pred = pred_all - guessed
        tp = len(pred & truth_set)
        wrong = sorted((pred - truth_set) | guessed)
        missed = sorted(truth_set - pred)
        wrong_total += len(wrong)
        links[kind] = {
            "truth": len(truth_set), "linked": len(pred_all), "correct": tp,
            "wrong": len(wrong), "missed": len(missed),
            "precision": _ratio(tp, tp + len(wrong)), "recall": _ratio(tp, len(truth_set)),
            "wrong_pairs": [list(x) for x in wrong[:25]], "missed_pairs": [list(x) for x in missed[:25]],
        }

    truth_exc = {
        (e["code"], e["entity_id"]) for e in truth["exceptions"]
        if not (e["code"] == "OPEN_INVOICE" and e["entity_id"] in related)
    }
    pred_exc = {(e.code, e.entity_id) for e in result.exceptions if e.code not in NOT_SCORED}
    per_code = {}
    for code in sorted({c for c, _ in truth_exc | pred_exc}):
        t = {x for x in truth_exc if x[0] == code}
        p = {x for x in pred_exc if x[0] == code}
        per_code[code] = {"truth": len(t), "flagged": len(p), "correct": len(t & p),
                          "false": len(p - t), "missed": len(t - p)}
    exc_tp = len(truth_exc & pred_exc)
    false_exc = sorted(pred_exc - truth_exc)
    missed_exc = sorted(truth_exc - pred_exc)

    reviewed = {r.subject_id for r in result.reviews}
    c = cfg.cost
    cost = {
        "wrong_links": c.wrong_link * wrong_total,
        "missed_exceptions": c.missed_exception * len(missed_exc),
        "false_exceptions": c.false_exception * len(false_exc),
        "review_items": c.review_item * len(result.reviews),
    }
    records = result.summary.get("records") or 1
    return {
        "links": links,
        "wrong_links": wrong_total,
        "exceptions": {
            "truth": len(truth_exc), "flagged": len(pred_exc), "correct": exc_tp,
            "false": len(false_exc), "missed": len(missed_exc),
            "precision": _ratio(exc_tp, len(pred_exc)), "recall": _ratio(exc_tp, len(truth_exc)),
            "by_code": per_code,
            "false_items": [list(x) for x in false_exc[:25]],
            "missed_items": [list(x) for x in missed_exc[:25]],
        },
        "reviews": len(result.reviews),
        "abstained_correctly": len(undecidable & reviewed),
        "undecidable_cases": len(undecidable),
        "cost": {**cost, "total": sum(cost.values()),
                 "per_100_records": round(100 * sum(cost.values()) / records, 2)},
        "scenarios": scenario_outcomes(truth, result),
    }


def scenario_outcomes(truth: dict[str, Any], result: RunResult) -> dict[str, dict[str, int]]:
    eq = {d: min(cls) for cls in truth.get("equivalent_invoices", []) for d in cls}
    true_links: dict[str, set[str]] = defaultdict(set)
    for pay, inv in truth["links"]["payment_invoice"]:
        true_links[pay].add(eq.get(inv, inv))
    linked: dict[str, set[str]] = defaultdict(set)
    for l in result.links:
        if l.kind == "payment_invoice":
            linked[l.left].add(eq.get(l.right, l.right))
    flagged = {(e.code, e.entity_id) for e in result.exceptions}
    reviewed = {r.subject_id for r in result.reviews}

    table: dict[str, Counter] = defaultdict(Counter)
    for pay, scen in truth["scenarios"].items():
        if scen == "duplicate_twin":
            ok = ("DUPLICATE_PAYMENT", pay) in flagged and not linked[pay]
        elif scen in ("missing_invoice", "coincidental_amount"):
            ok = ("MISSING_INVOICE", pay) in flagged and not linked[pay]
        elif scen == "name_trap_undecidable":
            ok = pay in reviewed and not linked[pay]
        else:
            ok = linked[pay] == true_links[pay]
        if ok:
            status = "correct"
        elif pay in reviewed:
            status = "review"
        elif linked[pay]:
            status = "wrong"
        else:
            status = "missed"
        table[scen]["payments"] += 1
        table[scen][status] += 1
    order = list(SCENARIO_LABELS)
    return {
        k: {s: table[k].get(s, 0) for s in ("payments", "correct", "review", "wrong", "missed")}
        for k in sorted(table, key=lambda x: order.index(x) if x in order else len(order))
    }
