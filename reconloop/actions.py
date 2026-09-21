"""Turn exceptions into proposed actions. Nothing here executes anything.

Every action is:
  explainable  -> it cites the exception and its evidence
  bounded      -> money-moving actions carry a hard ceiling (max_amount_paise)
  gated        -> anything that moves money or posts to the ledger needs approval
  idempotent   -> a stable key, so re-running the batch never proposes it twice
"""

from __future__ import annotations

import hashlib
from typing import Any

from .models import ReconException, ReviewCase
from .money import inr

# code -> (action, owner, moves_money, posts_to_ledger, template)
PLAYBOOK: dict[str, tuple[str, str, bool, bool, str]] = {
    "DUPLICATE_PAYMENT": ("refund_duplicate_capture", "finance lead", True, False,
                          "Refund the second capture {entity} ({amount}) to the customer."),
    "SETTLEMENT_NOT_IN_BANK": ("raise_settlement_ticket", "finance lead", False, False,
                               "Raise a Razorpay support ticket for settlement {entity} ({amount}) with its UTR."),
    "SETTLEMENT_AMOUNT_MISMATCH": ("query_bank_deduction", "finance lead", False, False,
                                   "Ask the bank why {amount} was deducted from settlement {entity}."),
    "UNMATCHED_BANK_CREDIT": ("identify_remitter", "accounts", False, False,
                              "Ask the bank for remitter details on line {entity} ({amount}) before booking it."),
    "DISPUTE_DEBIT": ("submit_dispute_evidence", "operations", False, False,
                      "Collect delivery proof and submit evidence for chargeback {entity} ({amount})."),
    "FEE_MISMATCH": ("raise_fee_query", "finance lead", False, False,
                     "Raise a fee query with the Razorpay account manager for {entity} ({amount} above contract)."),
    "TAX_MISMATCH": ("raise_tax_query", "finance lead", False, False,
                     "Ask Razorpay to correct GST on {entity} (off by {amount}) before GSTR-2B reconciliation."),
    "MISSING_INVOICE": ("raise_invoice", "accounts", False, True,
                        "Raise the missing invoice for {entity} ({amount}) or tag the payment to the right one."),
    "AMOUNT_MISMATCH": ("post_discount_adjustment", "accounts", False, True,
                        "Post a {amount} discount/short-payment adjustment against the invoice for {entity}."),
    "PARTIAL_PAYMENT": ("request_balance", "collections", False, False,
                        "Send a balance-due reminder for {amount} on the invoice paid by {entity}."),
    "MISSING_CREDIT_NOTE": ("issue_credit_note", "accounts", False, True,
                            "Issue a credit note for refund {entity} ({amount})."),
    "OPEN_INVOICE": ("send_payment_reminder", "collections", False, False,
                     "Send a payment reminder for {entity} ({amount})."),
    "MALFORMED_ROW": ("fix_source_row", "accounts", False, False,
                      "Fix or re-export source row {entity}; it was set aside, not dropped."),
    "SETTLEMENT_IN_TRANSIT": ("recheck_next_statement", "accounts", False, False,
                              "Re-check settlement {entity} ({amount}) on the next bank statement."),
}


def _key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def propose_actions(exceptions: list[ReconException], reviews: list[ReviewCase]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for e in sorted(exceptions, key=lambda x: (x.code, x.entity_id)):
        spec = PLAYBOOK.get(e.code)
        if spec is None:
            continue
        action, owner, moves_money, posts, template = spec
        actions.append({
            "action_id": f"ACT-{len(actions) + 1:04d}",
            "idempotency_key": _key(e.code, e.entity_id, action),
            "action": action,
            "owner": owner,
            "because": e.code,
            "entity_id": e.entity_id,
            "summary": template.format(entity=e.entity_id, amount=inr(abs(e.amount))),
            "moves_money": moves_money,
            "posts_to_ledger": posts,
            "requires_approval": moves_money or posts,
            "max_amount_paise": abs(e.amount) if (moves_money or posts) else None,
            "status": "proposed",
        })
    for r in reviews:
        actions.append({
            "action_id": f"ACT-{len(actions) + 1:04d}",
            "idempotency_key": _key("REVIEW", r.case_id),
            "action": "review_match",
            "owner": "accounts",
            "because": "NEEDS_REVIEW",
            "entity_id": r.subject_id,
            "summary": f"Pick the right match for {r.subject_id}: {r.reason}",
            "moves_money": False,
            "posts_to_ledger": False,
            "requires_approval": False,
            "max_amount_paise": None,
            "status": "waiting_for_human",
        })
    return actions


def approve(action: dict[str, Any], approver: str) -> dict[str, Any]:
    """Record an approval. Execution stays with the approver's own tools."""
    if not approver.strip():
        raise ValueError("an approver is required")
    if action["status"] != "proposed":
        raise ValueError(f"cannot approve an action in status {action['status']!r}")
    return {**action, "status": "approved", "approved_by": approver}
