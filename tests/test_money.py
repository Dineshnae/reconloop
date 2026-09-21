import pytest

from reconloop.config import DEFAULT
from reconloop.money import expected_fee, inr, parse_rupees, to_rupees_str


@pytest.mark.parametrize("text,paise", [
    ("1,23,456.70", 12345670), ("123456.7", 12345670), ("0.05", 5), ("", 0), ("-", 0), ("₹499", 49900),
])
def test_parse_rupees(text, paise):
    assert parse_rupees(text) == paise


@pytest.mark.parametrize("bad", ["1,23,4O5.00", "12.345", "abc", "1.2.3"])
def test_parse_rupees_rejects_junk(bad):
    with pytest.raises(ValueError):
        parse_rupees(bad)


def test_indian_grouping():
    assert inr(12345670) == "₹1,23,456.70"
    assert inr(100000000) == "₹10,00,000.00"
    assert inr(99) == "₹0.99"
    assert inr(-5050) == "-₹50.50"
    assert to_rupees_str(12345670) == "123456.70"


def test_fee_is_base_plus_gst():
    assert expected_fee(149900, "card", DEFAULT.rate_card) == (2923, 526)
    assert expected_fee(149900, "upi", DEFAULT.rate_card) == (0, 0)
    assert expected_fee(149900, "cod", DEFAULT.rate_card) is None
