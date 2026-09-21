"""Money is integer paise everywhere inside the engine.

Razorpay's API already speaks paise. Bank statements and books speak rupees
with Indian digit grouping, so they are converted once, at the edge.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .config import RateCard

_AMOUNT_RE = re.compile(r"^-?\d+(\.\d{1,2})?$")


def round_half_up(x: Decimal) -> int:
    return int(x.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_rupees(text: str | None) -> int:
    """'1,23,456.70' -> 12345670 paise. Blank -> 0. Anything else raises ValueError."""
    if text is None:
        return 0
    s = str(text).strip().replace(",", "").replace("\u20b9", "").replace("INR", "").strip()
    if s in ("", "-"):
        return 0
    if not _AMOUNT_RE.match(s):
        raise ValueError(f"not an amount: {text!r}")
    try:
        return round_half_up(Decimal(s) * 100)
    except InvalidOperation as exc:  # pragma: no cover - regex already guards this
        raise ValueError(f"not an amount: {text!r}") from exc


def to_rupees_str(paise: int) -> str:
    """12345670 -> '123456.70' (plain, for CSVs)."""
    sign = "-" if paise < 0 else ""
    p = abs(paise)
    return f"{sign}{p // 100}.{p % 100:02d}"


def inr(paise: int) -> str:
    """12345670 -> '₹1,23,456.70' (Indian grouping, for humans)."""
    sign = "-" if paise < 0 else ""
    p = abs(int(paise))
    rupees, frac = divmod(p, 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return f"{sign}\u20b9{s}.{frac:02d}"


def expected_fee(amount: int, method: str | None, card: RateCard) -> tuple[int, int] | None:
    """Return (base_fee, gst) the contract implies, or None if the method is unknown."""
    rate = card.rates.get((method or "").lower())
    if rate is None:
        return None
    base = round_half_up(Decimal(amount) * rate)
    gst = round_half_up(Decimal(base) * card.gst_rate)
    return base, gst


def gst_on(base: int, card: RateCard) -> int:
    return round_half_up(Decimal(base) * card.gst_rate)
