import filecmp
from collections import Counter

from reconloop import io
from reconloop.generate import PLANTS, generate


def test_same_seed_same_files(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate(a, seed=11)
    generate(b, seed=11)
    for name in (io.RZP_FILE, io.BANK_FILE, io.BOOKS_FILE, io.TRUTH_FILE):
        assert filecmp.cmp(a / name, b / name, shallow=False), name


def test_answer_key_points_at_real_rows(std_batch):
    batch, truth = io.load_batch(std_batch), io.load_truth(std_batch)
    txn_ids = {t.entity_id for t in batch.txns}
    bank_ids = {b.line_id for b in batch.bank}
    docs = {d.doc_no for d in batch.books}
    settlements = {t.settlement_id for t in batch.txns}
    for pay, inv in truth["links"]["payment_invoice"]:
        assert pay in txn_ids and inv in docs
    for ref, cn in truth["links"]["refund_credit_note"]:
        assert ref in txn_ids and cn in docs
    for sid, line in truth["links"]["settlement_bank"]:
        assert sid in settlements and line in bank_ids
    assert batch.quarantined == []


def test_planted_counts_match_the_recipe(std_batch):
    truth = io.load_truth(std_batch)
    codes = Counter(e["code"] for e in truth["exceptions"])
    p = PLANTS["standard"]
    assert codes["DUPLICATE_PAYMENT"] == p["duplicate"]
    assert codes["SETTLEMENT_NOT_IN_BANK"] == p["settlement_missing"]
    assert codes["MISSING_CREDIT_NOTE"] == p["refund_no_cn"]
    assert len(truth["links"]["payment_invoice"]) > 150      # comfortably above a 50-record batch


def test_hard_batch_has_an_unreadable_row(hard_batch):
    batch, truth = io.load_batch(hard_batch), io.load_truth(hard_batch)
    malformed = [e["entity_id"] for e in truth["exceptions"] if e["code"] == "MALFORMED_ROW"]
    assert [q["entity_id"] for q in batch.quarantined] == malformed


def test_true_invoice_is_not_always_created_first(tmp_path):
    """Guards against a leak where 'pick the lower invoice number' wins every name trap."""
    true_first = []
    for seed in (1, 2, 3):
        d = tmp_path / str(seed)
        generate(d, seed=seed, difficulty="hard")
        t, books = io.load_truth(d), {b.doc_no: b for b in io.load_batch(d).books}
        truth_inv = dict((p, i) for p, i in t["links"]["payment_invoice"])
        for pay, scen in t["scenarios"].items():
            if not scen.startswith("name_trap"):
                continue
            doc = books[truth_inv[pay]]
            serial = int(doc.doc_no[-5:])
            for delta in (-1, 1):
                other = books.get(f"INV/2026-27/{serial + delta:05d}")
                if other and other.amount == doc.amount and other.doc_date == doc.doc_date:
                    true_first.append(delta == 1)
    assert len(true_first) >= 20
    assert 0.2 < sum(true_first) / len(true_first) < 0.8


def test_debits_bigger_than_a_day_are_carried_forward(tmp_path):
    """Hard seed 208 once crashed the generator: one refund outweighed that day's three payments."""
    from reconloop.match_settlements import build_settlements

    generate(tmp_path, seed=208, difficulty="hard")
    settlements = build_settlements(io.load_batch(tmp_path).txns)
    assert settlements and all(s.net > 0 for s in settlements.values())
