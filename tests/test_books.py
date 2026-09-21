from datetime import date, datetime, time

from reconloop import io
from reconloop.agent import ReconAgent
from reconloop.audit import AuditLog
from reconloop.config import DEFAULT
from reconloop.match_books import BookMatcher
from reconloop.models import IST, BookDoc, RzpTxn
from reconloop.resolver import NONE, ClaudeResolver, Decision, GreedyResolver, ScriptedResolver


def pay(pid, amount, d, email="", phone="", notes=None, receipt=None):
    ts = int(datetime.combine(d, time(12), tzinfo=IST).timestamp())
    return RzpTxn(pid, "payment", 0, amount, amount, "INR", 0, 0, False, True, ts, None, None, None,
                  f"order_{pid}", receipt, "upi", notes or {}, None, None, None, email, phone)


def inv(no, amount, d, name, email="", phone=""):
    return BookDoc(no, "invoice", d, name, email, phone, amount)


D = date(2026, 8, 10)


def run(resolver, txns, books):
    bm = BookMatcher(DEFAULT, resolver, AuditLog())
    bm.run(txns, books, set())
    return bm


def test_invented_invoice_id_is_rejected(loaded):
    batch, _ = loaded
    liar = ScriptedResolver(lambda case: Decision("INV/9999-99/99999", 0.99, "made up", "scripted"))
    result = ReconAgent(DEFAULT, liar).run(batch)
    assert not [l for l in result.links if l.stage.startswith("tie_breaker")]
    assert result.reviews and all("was not offered" in r.reason for r in result.reviews if r.resolver)


def test_low_confidence_pick_goes_to_a_person(loaded):
    batch, _ = loaded
    shaky = ScriptedResolver(lambda case: Decision(case["candidates"][0]["id"], 0.5, "maybe", "scripted"))
    result = ReconAgent(DEFAULT, shaky).run(batch)
    assert not [l for l in result.links if l.stage.startswith("tie_breaker")]


def test_no_match_answer_is_overruled_by_contact_evidence():
    txns = [pay("p1", 100000, D, email="a@x.in")]
    books = [inv("INV/2026-27/00101", 100000, D, "Asha Rao", email="a@x.in"),
             inv("INV/2026-27/00102", 100000, date(2026, 8, 11), "Asha Rao", email="a@x.in")]
    bm = run(ScriptedResolver(lambda c: Decision(NONE, 0.95, "none fit", "scripted")), txns, books)
    assert bm.links == []
    assert [r.subject_id for r in bm.reviews] == ["p1"]
    assert "contact or reference evidence" in bm.reviews[0].reason


def test_amount_and_date_alone_never_link():
    txns = [pay("p1", 149900, D, email="stranger@y.in")]
    books = [inv("INV/2026-27/00101", 149900, D, "Meera Pillai", email="meera@x.in")]
    bm = run(ScriptedResolver(lambda c: Decision("UNSURE", 0.0, "", "offline")), txns, books)
    assert bm.links == []
    assert bm.reviews[0].reason.startswith("Only the amount and date match")


def test_model_failure_is_not_fatal():
    class Down:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise TimeoutError("upstream timed out")

    txns = [pay("p1", 149900, D, notes={"note": "Venky - curtains"})]
    books = [inv("INV/2026-27/00101", 149900, D, "Venkatesh Iyer"), inv("INV/2026-27/00102", 149900, D, "Harish Kumar")]
    bm = run(ClaudeResolver(client=Down(), cache_dir=None), txns, books)
    assert bm.links == []
    assert bm.reviews[0].resolver["error"].startswith("TimeoutError")


def test_verified_pick_is_linked_and_logged():
    txns = [pay("p1", 149900, D, notes={"note": "Venky - curtains"})]
    books = [inv("INV/2026-27/00101", 149900, D, "Venkatesh Iyer"), inv("INV/2026-27/00102", 149900, D, "Harish Kumar")]
    pick = ScriptedResolver(lambda c: Decision("INV/2026-27/00101", 0.9, "Venky is short for Venkatesh", "scripted"))
    bm = run(pick, txns, books)
    assert [(l.left, l.right, l.stage) for l in bm.links] == [("p1", "INV/2026-27/00101", "tie_breaker:scripted")]
    assert [e.entity_id for e in bm.exceptions if e.code == "OPEN_INVOICE"] == ["INV/2026-27/00102"]
    assert any(e["stage"] == "tie_breaker" for e in bm.audit.events)


def test_split_and_combined_payments_by_subset_sum():
    txns = [pay("p1", 60000, D, email="c@x.in", receipt="INV/2026-27/00101"),
            pay("p2", 40000, date(2026, 8, 11), email="c@x.in", notes={"note": "balance"}),
            pay("p3", 70000, D, email="d@x.in", notes={"note": "for both my orders"})]
    books = [inv("INV/2026-27/00101", 100000, D, "Cyrus Mistry", email="c@x.in"),
             inv("INV/2026-27/00102", 30000, D, "Dia Sen", email="d@x.in"),
             inv("INV/2026-27/00103", 40000, D, "Dia Sen", email="d@x.in")]
    bm = run(ScriptedResolver(lambda c: Decision("UNSURE", 0, "", "offline")), txns, books)
    got = {(l.left, l.right, l.stage) for l in bm.links}
    assert got == {("p1", "INV/2026-27/00101", "split_payment"), ("p2", "INV/2026-27/00101", "split_payment"),
                   ("p3", "INV/2026-27/00102", "combined_payment"), ("p3", "INV/2026-27/00103", "combined_payment")}


def test_greedy_baseline_is_riskier_than_rules_alone(hard_batch):
    from reconloop.score import score

    batch, truth = io.load_batch(hard_batch), io.load_truth(hard_batch)
    rules = score(truth, ReconAgent(DEFAULT).run(batch))
    greedy = score(truth, ReconAgent(DEFAULT, GreedyResolver()).run(batch))
    assert rules["wrong_links"] == 0
    assert greedy["wrong_links"] > rules["wrong_links"]
