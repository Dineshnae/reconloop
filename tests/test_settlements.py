from datetime import date

from reconloop.audit import AuditLog
from reconloop.config import DEFAULT
from reconloop.match_settlements import match_settlements
from reconloop.models import BankLine, Settlement


def line(i, d, credit, narration="BY CLG RAZORPAY SETTLEMENT", ref=""):
    return BankLine(f"B{i}", d, narration, ref, 0, credit, None)


def test_same_amount_on_consecutive_days_is_resolved_by_the_usual_lag():
    s = {
        "s0": Settlement("s0", "YESBR2026081011111111", date(2026, 8, 10), 5000),
        "sA": Settlement("sA", "YESBR2026081122222222", date(2026, 8, 11), 90000),
        "sB": Settlement("sB", "YESBR2026081233333333", date(2026, 8, 12), 90000),
    }
    bank = [
        line(0, date(2026, 8, 10), 5000, "NEFT CR-YESBR2026081011111111-RAZORPAY"),   # teaches lag 0
        line(1, date(2026, 8, 11), 90000),
        line(2, date(2026, 8, 12), 90000),
    ]
    links, exc, rev = match_settlements(s, bank, DEFAULT, AuditLog(), date(2026, 8, 20))
    assert {(l.left, l.right) for l in links} == {("s0", "B0"), ("sA", "B1"), ("sB", "B2")}
    assert exc == [] and rev == []


def test_truly_ambiguous_credits_go_to_review_not_a_guess():
    s = {
        "sA": Settlement("sA", "U1XXXXXXXXXX", date(2026, 8, 11), 90000),
        "sB": Settlement("sB", "U2XXXXXXXXXX", date(2026, 8, 11), 90000),
    }
    bank = [line(1, date(2026, 8, 12), 90000), line(2, date(2026, 8, 12), 90000)]
    links, exc, rev = match_settlements(s, bank, DEFAULT, AuditLog(), date(2026, 8, 20))
    assert links == []
    assert {r.subject_id for r in rev} == {"sA", "sB"}
    assert not [e for e in exc if e.code == "UNMATCHED_BANK_CREDIT"]


def test_short_credit_missing_settlement_and_lookalike_credit():
    s = {
        "short": Settlement("short", "YESBR2026081144444444", date(2026, 8, 11), 50000),
        "gone": Settlement("gone", "YESBR2026081255555555", date(2026, 8, 12), 70000),
    }
    bank = [
        line(1, date(2026, 8, 11), 48230, "NEFT CR-YESBR2026081144444444-RAZORPAY"),
        line(2, date(2026, 8, 12), 70000, "IMPS CR/612345678901/SHARMA TRADERS"),     # same amount, not Razorpay
        line(3, date(2026, 8, 13), 12345, "NEFT CR-YESBR2026081399999999-RAZORPAY"),
    ]
    links, exc, rev = match_settlements(s, bank, DEFAULT, AuditLog(), date(2026, 8, 20))
    codes = {(e.code, e.entity_id) for e in exc}
    assert {(l.left, l.right) for l in links} == {("short", "B1")}
    assert ("SETTLEMENT_AMOUNT_MISMATCH", "short") in codes
    assert ("SETTLEMENT_NOT_IN_BANK", "gone") in codes
    assert ("UNMATCHED_BANK_CREDIT", "B3") in codes
    assert all(e.entity_id != "B2" for e in exc)


def test_recent_settlement_is_in_transit_not_missing():
    s = {"late": Settlement("late", "U9", date(2026, 8, 19), 1000)}
    _, exc, _ = match_settlements(s, [], DEFAULT, AuditLog(), date(2026, 8, 20))
    assert [e.code for e in exc] == ["SETTLEMENT_IN_TRANSIT"]
