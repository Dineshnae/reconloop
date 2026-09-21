import pytest

from reconloop.normalize import (
    contact_match, is_razorpay_credit, name_similarity, receipt_serial, text_serials, utr_evidence,
)

UTR = "YESBR2026081512345678"


@pytest.mark.parametrize("receipt,serial", [
    ("INV/2026-27/00123", 123), ("inv_00123", 123), ("00123", 123), ("INV123", 123), ("20260815", None), (None, None),
])
def test_receipt_serial(receipt, serial):
    assert receipt_serial(receipt) == serial


@pytest.mark.parametrize("text,serials", [
    ("payment against bill 0123", [123]),
    ("for invoices 123 & 124", [123, 124]),
    ("bills 210, 211 and 212", [210, 211, 212]),
    ("pymt agnst invc no. 187", [187]),
    ("Inv no: INV/2026-27/00145", [145]),
    ("order #123 - thanks", [123]),
    ("Order for 2 bedsheets", []),        # regression: 'for ' was once read as a prefix
    ("invoice amount 1499", []),
])
def test_text_serials(text, serials):
    assert text_serials(text) == serials


def test_utr_evidence():
    assert utr_evidence(f"NEFT CR-{UTR}-RAZORPAY", "", UTR) == "exact"
    assert utr_evidence("RAZORPAY SOFTWARE PRIVATE LIMITED", UTR, UTR) == "exact"
    assert utr_evidence(f"NEFT CR-RAZORPAY SOFTW-{UTR[-10:]}", "S123", UTR) == "fragment"
    assert utr_evidence("BY CLG RAZORPAY SETTLEMENT", "S12345678", UTR) is None


def test_only_razorpay_credits_are_candidates():
    assert is_razorpay_credit("NEFT CR-XYZ-RAZORPAY SOFTWARE PVT LTD")
    assert not is_razorpay_credit("IMPS CR/612345678901/SHARMA TRADERS")


@pytest.mark.parametrize("note,name,expected", [
    ("Priya Sundaram", "Priya Sundaram", 1.0),
    ("S. Priya - bedsheet order", "Priya Sundaram", 0.9),
    ("S. Priya - bedsheet order", "Deepa Nair", 0.0),
    ("Nilaya homestay - linen restock", "Nilaya Homestays", 1.0),
    ("Kiran from Brightpath", "Brightpath Interiors LLP", 0.6),
    ("Kiran from Brightpath", "Kiran Kumar", 0.6),            # a tie the rules must not break
    ("Paid by Arjun for his mother's order", "Arjun Sharma", 0.6),
    ("Lakshmi, his mother's order", "Lakshmi Sharma", 0.6),  # possessive 's is not an initial
    ("Venky - curtains", "Venkatesh Iyer", 0.0),              # nicknames are left to the tie-breaker
])
def test_name_similarity(note, name, expected):
    assert name_similarity(note, name) == expected


def test_contact_match_normalises_phone_and_email():
    assert contact_match(None, "+91 98450 12345", None, "9845012345")
    assert contact_match("A@X.IN ", None, "a@x.in", None)
    assert not contact_match("", "", "", "")
