"""Single-file HTML report. Ledger paper: blue ink for what reconciled,
red ink for what did not, highlighter for what waits on a person."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from html import escape
from typing import Any

from .agent import INFO_CODES, RunResult
from .money import inr
from .score import SCENARIO_LABELS

EXCEPTION_TITLES = {
    "SETTLEMENT_NOT_IN_BANK": "Settlement missing from the bank",
    "SETTLEMENT_AMOUNT_MISMATCH": "Bank credited less than was settled",
    "UNMATCHED_BANK_CREDIT": "Razorpay-looking credit with no settlement",
    "DUPLICATE_PAYMENT": "Customer charged twice",
    "DISPUTE_DEBIT": "Chargeback taken from a settlement",
    "FEE_MISMATCH": "Fee above the contract rate",
    "TAX_MISMATCH": "GST on the fee does not add up",
    "MISSING_INVOICE": "Payment with no invoice",
    "PARTIAL_PAYMENT": "Invoice only partly paid",
    "AMOUNT_MISMATCH": "Paid amount differs from the invoice",
    "MISSING_CREDIT_NOTE": "Refund with no credit note",
    "MALFORMED_ROW": "Row that could not be read",
    "OPEN_INVOICE": "Invoice with no payment yet",
    "SETTLEMENT_IN_TRANSIT": "Settlement still in transit",
}
ORDER = list(EXCEPTION_TITLES)

OUTCOMES = [
    ("exact_receipt", "Receipt was the invoice number", "var(--seg1)"),
    ("reference", "Invoice number in another format", "var(--seg2)"),
    ("scored", "Matched on payer contact or name", "var(--seg3)"),
    ("grouped", "Split, combined or partial", "var(--seg4)"),
    ("tie_breaker", "Tie-breaker pick, verified by rules", "var(--seg5)"),
    ("review", "Sent to you", "var(--seg6)"),
    ("missing_invoice", "No invoice found", "var(--seg7)"),
]
KIND_TITLES = {
    "settlement_bank": "Settlement to bank",
    "payment_invoice": "Payment to invoice",
    "refund_credit_note": "Refund to credit note",
}

DARK = """--paper:#12182B;--rule:#2C3A5E;--ink:#DDE5F8;--ink-2:#A8B4D4;--red:#FF8C7C;
--margin:rgba(255,140,124,.4);--hl:#6E5A12;--seg1:#DDE5F8;--seg2:#B2C1E6;--seg3:#8A9ED3;
--seg4:#6479B8;--seg5:#44578F;--seg6:#E3C53F;--seg7:#FF8C7C"""

CSS = """
:root{--paper:#F2F5EE;--rule:#BCD0E6;--ink:#1E2B5E;--ink-2:#4A5680;--red:#B42318;
--margin:rgba(196,48,43,.55);--hl:#F4DC57;--seg1:#1E2B5E;--seg2:#34488A;--seg3:#5A6FAE;
--seg4:#8698CB;--seg5:#B3C0E2;--seg6:#F2D84B;--seg7:#B42318;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){""" + DARK + """;color-scheme:dark}}
:root[data-theme="dark"]{""" + DARK + """;color-scheme:dark}
*{box-sizing:border-box}
html{background:var(--paper)}
body{margin:0;background:var(--paper);color:var(--ink);font:400 16px/1.55 "Source Sans 3",system-ui,-apple-system,"Segoe UI",sans-serif;
font-variant-numeric:tabular-nums}
body::before{content:"";position:fixed;top:0;bottom:0;left:56px;width:5px;pointer-events:none;
border-left:1.5px solid var(--margin);border-right:1.5px solid var(--margin)}
main{max-width:1000px;padding:44px 32px 96px 104px}
h1,h2,h3,.hero{font-family:"Zilla Slab",Georgia,serif;font-weight:600;color:var(--ink)}
.masthead{margin:0;font-size:1rem;font-weight:600;font-family:"Source Sans 3",system-ui,sans-serif;color:var(--ink-2)}
.hero{font-size:2.2rem;font-size:clamp(1.7rem,3.4vw,2.5rem);line-height:1.2;max-width:32ch;margin:14px 0 18px}
.lede{max-width:70ch;color:var(--ink-2);margin:0}
h2{font-size:1.4rem;margin:52px 0 6px;padding-top:26px;border-top:1px solid var(--rule)}
h2+p{margin-top:0;color:var(--ink-2);max-width:70ch}
h3{font-size:1.08rem;margin:30px 0 4px;display:flex;justify-content:space-between;gap:16px;align-items:baseline}
h3 .num{font-family:"Source Sans 3",system-ui,sans-serif;font-weight:600}
.wide{overflow-x:auto}
table{border-collapse:collapse;width:100%;margin:6px 0 4px}
th,td{text-align:left;vertical-align:top;padding:7px 10px 7px 0;border-bottom:1px solid var(--rule)}
thead th{font-weight:600;color:var(--ink-2);border-bottom:3px double var(--ink-2)}
td.num,th.num{text-align:right;white-space:nowrap}
tr.total td{border-bottom:3px double var(--ink);font-weight:600}
.id{font-size:.88rem;white-space:nowrap;color:var(--ink-2)}
.red{color:var(--red)}
mark{background:linear-gradient(transparent 30%,var(--hl) 30%,var(--hl) 88%,transparent 88%);color:inherit;padding:0 .15em}
.chip{display:inline-block;font-size:.8rem;line-height:1.5;padding:0 8px;border:1px solid currentColor;border-radius:999px;white-space:nowrap}
.chip.gate{color:var(--red)}
.chip.free{color:var(--ink-2)}
.bar{display:flex;height:26px;border:1.5px solid var(--ink);margin:14px 0 10px;max-width:760px}
.bar span{display:block;height:100%}
.bar span+span{border-left:1px solid var(--paper)}
.legend{max-width:760px;width:auto}.legend td{border-bottom:none;padding:2px 14px 2px 0}
.sw{width:14px;height:14px;border:1px solid var(--ink)}
.cands{margin:0;padding:0;list-style:none}
.cands li{margin:0 0 2px}
.small{font-size:.88rem;color:var(--ink-2)}
details{margin-top:10px}
summary{cursor:pointer;font-weight:600}
summary:focus-visible,a:focus-visible{outline:2px solid var(--red);outline-offset:3px}
code{font-size:.85rem;word-break:break-word}
footer{margin-top:64px;padding-top:16px;border-top:3px double var(--ink-2);color:var(--ink-2);font-size:.9rem}
@media (max-width:760px){body::before{display:none}main{padding:28px 16px 64px}th,td{padding-right:8px}}
@media print{body::before{display:none}main{padding:0}}
"""

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600&'
    'family=Zilla+Slab:wght@600&display=swap" rel="stylesheet">'
)


def _e(x: Any) -> str:
    return escape(str(x), quote=True)


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _period(summary: dict[str, Any]) -> str:
    p = summary.get("period")
    if not p:
        return ""
    a, b = (date.fromisoformat(x) for x in p)
    return f"{a.day} {a:%B} to {b.day} {b:%B %Y}"


def _outcome_group(stage: str) -> str:
    if stage in ("normalized_ref", "multi_ref"):
        return "reference"
    if stage in ("split_payment", "combined_payment", "partial_payment"):
        return "grouped"
    if stage.startswith("tie_breaker"):
        return "tie_breaker"
    return stage


def headline(result: RunResult) -> str:
    s = result.summary
    st, pay = s["settlements"], s["payments"]
    net, in_bank = st["net_paise"], st["in_bank_paise"]
    considered = pay["count"] - pay["duplicates_set_aside"]
    first = f"{inr(net)} settled across {st['count']} batches"
    first += ", and all of it is confirmed in the bank." if in_bank == net else f"; {inr(in_bank)} of it is confirmed in the bank."
    second = f"{pay['linked']} of {considered} payments are tied to invoices."
    attention = [e for e in result.exceptions if e.code not in INFO_CODES]
    blocking = sum(1 for e in attention if e.severity in ("high", "medium")) + len(result.reviews)
    total = len(attention) + len(result.reviews)
    third = f"{total} items need attention, {blocking} of them before you close the books."
    return f"{first} {second} {third}"


def _lede(result: RunResult) -> str:
    s = result.summary
    i = s["inputs"]
    tb = s["tie_breaker"]
    text = (f"Checked {i['razorpay_rows']} Razorpay rows, {i['bank_lines']} bank lines and "
            f"{i['book_docs']} book entries in {s['runtime_seconds']:.2f} seconds. ")
    if i["quarantined"]:
        text += f"{i['quarantined']} unreadable row{'s were' if i['quarantined'] > 1 else ' was'} set aside and listed below. "
    name = tb["name"]
    if name == "offline":
        text += "No model was used: anything the rules could not settle went to review."
    elif name == "claude":
        text += (f"Claude ({_e(tb.get('model', ''))}) was asked about {tb['cases']} unclear payments; "
                 f"every answer was re-checked by the rules before anything was linked.")
    elif name == "oracle":
        text += "Tie-breaker: oracle. It reads the answer key and only shows the ceiling, not a real result."
    else:
        text += f"Tie-breaker: {_e(name)}."
    return text


def _outcome_bar(result: RunResult) -> str:
    counts: dict[str, int] = defaultdict(int)
    for stage, n in result.summary["payments"]["outcomes"].items():
        counts[_outcome_group(stage)] += n
    total = sum(counts.values()) or 1
    segs, rows = [], []
    for key, label, color in OUTCOMES:
        n = counts.get(key, 0)
        if not n:
            continue
        share = n / total
        segs.append(f'<span style="width:{100 * share:.3f}%;background:{color}" title="{_e(label)}: {n}"></span>')
        rows.append(f'<tr><td><div class="sw" style="background:{color}"></div></td><td>{_e(label)}</td>'
                    f'<td class="num">{n}</td><td class="num small">{_pct(share)}</td></tr>')
    return (f'<div class="bar" role="img" aria-label="How {total} payments were settled">{"".join(segs)}</div>'
            f'<table class="legend"><tbody>{"".join(rows)}</tbody></table>')


def _exceptions(result: RunResult) -> str:
    actions = {a["entity_id"] + a["because"]: a for a in result.actions}
    groups: dict[str, list] = defaultdict(list)
    for e in result.exceptions:
        groups[e.code].append(e)
    if not groups:
        return "<p>Nothing needs a decision. Every settlement, payment and refund reconciled.</p>"
    out = []
    for code in sorted(groups, key=lambda c: ORDER.index(c) if c in ORDER else len(ORDER)):
        items = sorted(groups[code], key=lambda e: e.entity_id)
        total = sum(abs(e.amount) for e in items)
        amount_head = f" {inr(total)}" if total else ""
        out.append(f'<h3><span>{_e(EXCEPTION_TITLES.get(code, code))}</span>'
                   f'<span class="num">{len(items)}{"," if total else ""}{amount_head}</span></h3>')
        rows = []
        for e in items:
            a = actions.get(e.entity_id + e.code)
            if a is None:
                act = ""
            else:
                chip = ('<span class="chip gate">Needs approval</span>' if a["requires_approval"]
                        else '<span class="chip free">No money moves</span>')
                act = f"{_e(a['summary'])}<br>{chip}"
            amt = f'<span class="red">{inr(abs(e.amount))}</span>' if e.amount else ""
            rows.append(f'<tr><td class="id">{_e(e.entity_id)}</td><td>{_e(e.detail)}</td>'
                        f'<td class="num">{amt}</td><td>{act}</td></tr>')
        out.append('<div class="wide"><table><thead><tr><th>Item</th><th>What happened</th>'
                   '<th class="num">Amount</th><th>Proposed action</th></tr></thead><tbody>'
                   + "".join(rows) + "</tbody></table></div>")
    return "".join(out)


def _reviews(result: RunResult) -> str:
    if not result.reviews:
        return "<p>No cases are waiting on a person.</p>"
    rows = []
    for r in result.reviews:
        cands = []
        for c in r.candidates:
            bits = []
            if c.get("customer"):
                bits.append(_e(c["customer"]))
            if isinstance(c.get("amount"), int):
                bits.append(inr(c["amount"]))
            if c.get("date"):
                bits.append(_e(c["date"]))
            if "name_similarity" in c and c.get("held", True):
                ev = []
                if c.get("contact_match"):
                    ev.append("contact matches")
                if c.get("name_similarity"):
                    ev.append(f"name {c['name_similarity']:.1f}")
                if ev:
                    bits.append(", ".join(ev))
            weak = "" if c.get("held", True) else ' class="small"'
            cands.append(f'<li{weak}><span class="id">{_e(c["id"])}</span> {"; ".join(bits)}</li>')
        note = ""
        if r.resolver and r.resolver.get("source") not in (None, "offline"):
            note = (f'<br><span class="small">Tie-breaker said {_e(r.resolver.get("choice"))} '
                    f'({r.resolver.get("confidence", 0):.2f}): {_e(r.resolver.get("reason", ""))}</span>')
        rows.append(f'<tr><td class="id">{_e(r.subject_id)}</td><td><mark>{_e(r.reason)}</mark>{note}</td>'
                    f'<td><ul class="cands">{"".join(cands) or "<li>No open candidate</li>"}</ul></td></tr>')
    return ('<div class="wide"><table><thead><tr><th>Item</th><th>Why it is here</th><th>Candidates</th></tr></thead>'
            "<tbody>" + "".join(rows) + "</tbody></table></div>")


def _score(sc: dict[str, Any] | None) -> str:
    if sc is None:
        return "<p>This folder has no answer key, so accuracy is not scored.</p>"
    rows = []
    for kind, title in KIND_TITLES.items():
        L = sc["links"][kind]
        wrong = f'<span class="red">{L["wrong"]}</span>' if L["wrong"] else "0"
        rows.append(f'<tr><td>{title}</td><td class="num">{L["truth"]}</td><td class="num">{L["linked"]}</td>'
                    f'<td class="num">{L["correct"]}</td><td class="num">{wrong}</td><td class="num">{L["missed"]}</td>'
                    f'<td class="num">{_pct(L["precision"])}</td><td class="num">{_pct(L["recall"])}</td></tr>')
    E = sc["exceptions"]
    rows.append(f'<tr class="total"><td>Exceptions raised</td><td class="num">{E["truth"]}</td>'
                f'<td class="num">{E["flagged"]}</td><td class="num">{E["correct"]}</td>'
                f'<td class="num">{E["false"]}</td><td class="num">{E["missed"]}</td>'
                f'<td class="num">{_pct(E["precision"])}</td><td class="num">{_pct(E["recall"])}</td></tr>')
    table = ('<div class="wide"><table><thead><tr><th></th><th class="num">In answer key</th><th class="num">Agent said</th>'
             '<th class="num">Correct</th><th class="num">Wrong</th><th class="num">Missed</th>'
             '<th class="num">Precision</th><th class="num">Recall</th></tr></thead><tbody>'
             + "".join(rows) + "</tbody></table></div>")
    c = sc["cost"]
    cost = (f"<p>Error cost {c['total']} units, {c['per_100_records']} per 100 records: "
            f"{c['wrong_links']} from wrong links (5 each), {c['missed_exceptions']} from missed exceptions (3 each), "
            f"{c['false_exceptions']} from false exceptions (1 each) and {c['review_items']} from review items (1 each). "
            f"Undecidable cases sent to a person: {sc['abstained_correctly']} of {sc['undecidable_cases']}.</p>")
    srows = []
    for key, v in sc["scenarios"].items():
        wrong = f'<span class="red">{v["wrong"]}</span>' if v["wrong"] else "0"
        srows.append(f'<tr><td>{_e(SCENARIO_LABELS.get(key, key))}</td><td class="num">{v["payments"]}</td>'
                     f'<td class="num">{v["correct"]}</td><td class="num">{v["review"]}</td>'
                     f'<td class="num">{wrong}</td><td class="num">{v["missed"]}</td></tr>')
    scen = ('<h3><span>Payments by scenario</span></h3><div class="wide"><table><thead><tr><th>Scenario</th>'
            '<th class="num">Payments</th><th class="num">Correct</th><th class="num">Sent to review</th>'
            '<th class="num">Wrong</th><th class="num">Missed</th></tr></thead><tbody>' + "".join(srows)
            + "</tbody></table></div>")
    return table + cost + scen


def _tie_breaker_log(result: RunResult) -> str:
    log = [e for e in result.resolver_log if e.get("source") != "offline"]
    if not log:
        return ""
    rows = "".join(
        f'<tr><td class="id">{_e(e["payment"])}</td><td class="id">{_e(e["choice"])}</td>'
        f'<td class="num">{e["confidence"]:.2f}</td><td>{_e(e["verdict"])}: {_e(e["why"])}</td>'
        f'<td class="small">{_e(e.get("reason", ""))}{" (" + _e(e["error"]) + ")" if e.get("error") else ""}</td></tr>'
        for e in log
    )
    return ('<h2>Tie-breaker decisions</h2><p>Every answer below was checked by the rules before anything was linked.</p>'
            '<div class="wide"><table><thead><tr><th>Payment</th><th>Choice</th><th class="num">Confidence</th>'
            '<th>Verdict</th><th>Stated reason</th></tr></thead><tbody>' + rows + "</tbody></table></div>")


def _audit(result: RunResult, limit: int = 80) -> str:
    ev = result.audit.events
    rows = []
    for e in ev[:limit]:
        detail = {k: v for k, v in e.items() if k not in ("seq", "stage", "subject", "decision")}
        rows.append(f'<tr><td class="num">{e["seq"]}</td><td>{_e(e["stage"])}</td><td class="id">{_e(e["subject"])}</td>'
                    f'<td>{_e(e["decision"])}</td><td><code>{_e(json.dumps(detail, default=str)[:220])}</code></td></tr>')
    return (f'<details><summary>Show the first {min(limit, len(ev))} of {len(ev)} decisions</summary>'
            '<div class="wide"><table><thead><tr><th class="num">#</th><th>Stage</th><th>Subject</th>'
            '<th>Decision</th><th>Detail</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"
            "<p class=\"small\">The full log is in audit.jsonl next to this report.</p></details>")


def render_report(result: RunResult, sc: dict[str, Any] | None = None) -> str:
    s = result.summary
    merchant = s.get("merchant") or "Reconciliation run"
    period = _period(s)
    masthead = _e(merchant) + (f". Batch from {period}." if period else "")
    return f"""<!doctype html>
<html lang="en-IN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reconciliation: {_e(merchant)}</title>
{FONTS}<style>{CSS}</style></head>
<body><main>
<p class="masthead">{masthead}</p>
<p class="hero">{_e(headline(result))}</p>
<p class="lede">{_lede(result)}</p>

<h2>How each payment was settled</h2>
<p>Cheapest, most certain evidence first. The rules never link a payment on amount and date alone.</p>
{_outcome_bar(result)}

<h2>Needs a decision</h2>
<p>Proposed actions are drafts. Anything that moves money or posts to the ledger waits for approval.</p>
{_exceptions(result)}

<h2>Waiting for your review</h2>
<p>Cases where the evidence points two ways, or nowhere. Faint candidates share only a nearby amount.</p>
{_reviews(result)}

<h2>How far to trust this run</h2>
<p>Scored against the answer key the synthetic batch was generated with.</p>
{_score(sc)}
{_tie_breaker_log(result)}
<h2>Decision log</h2>
<p>Every link, exception and tie-breaker call, in order.</p>
{_audit(result)}

<footer>Generated by ReconLoop from synthetic data. Nothing in this report moves money.</footer>
</main></body></html>
"""
