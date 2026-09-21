import io as stdio
import json

from reconloop import io
from reconloop.connectors.razorpay_api import fetch_recon, write_recon_csv

ITEM = {
    "entity_id": "pay_DEXrnipqTmWVGE", "type": "payment", "debit": 0, "credit": 97100, "amount": 100000,
    "currency": "INR", "fee": 2900, "tax": 442, "on_hold": False, "settled": True,
    "created_at": 1567674599, "settled_at": 1568176960, "settlement_id": "setl_DGlQ1Rj8os78Ec",
    "settlement_utr": "1568176960vxp0rj", "order_id": "order_DEXrnRiR3SNDHA", "order_receipt": "INV/2026-27/00101",
    "method": "card", "notes": {"note": "thanks"}, "payment_id": None, "dispute_id": None, "description": None,
}


class FakeResponse(stdio.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_pages_until_a_short_page(tmp_path):
    calls = []

    def opener(req, timeout):
        calls.append(req.full_url)
        assert req.headers["Authorization"].startswith("Basic ")
        page = [ITEM, ITEM] if len(calls) == 1 else [ITEM]
        return FakeResponse(json.dumps({"entity": "collection", "count": len(page), "items": page}).encode())

    items = fetch_recon("rzp_test_key", "secret", 2026, 8, page_size=2, opener=opener)
    assert len(items) == 3 and len(calls) == 2
    assert "skip=2" in calls[1] and "year=2026" in calls[0]

    write_recon_csv(items, tmp_path / io.RZP_FILE)
    (tmp_path / io.BANK_FILE).write_text(",".join(io.BANK_COLUMNS) + "\n")
    (tmp_path / io.BOOKS_FILE).write_text(",".join(io.BOOK_COLUMNS) + "\n")
    batch = io.load_batch(tmp_path)
    assert len(batch.txns) == 3 and batch.quarantined == []
    assert batch.txns[0].notes == {"note": "thanks"} and batch.txns[0].settled
