"""Deterministic string evidence. Cheap, explainable, and tested."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Iterable

# --- bank narrations -------------------------------------------------------

_NON_ALNUM = re.compile(r"[^A-Z0-9]")
_TOKEN8 = re.compile(r"[A-Z0-9]{8,}")
_RZP = re.compile(r"RAZORPAY|\bRZP\b")


def alnum(s: str | None) -> str:
    return _NON_ALNUM.sub("", (s or "").upper())


def is_razorpay_credit(narration: str, ref_no: str = "") -> bool:
    return bool(_RZP.search(f"{narration} {ref_no}".upper()))


def utr_evidence(narration: str, ref_no: str, utr: str | None) -> str | None:
    """'exact' if the full UTR appears, 'fragment' if an 8+ char piece of it does."""
    u = alnum(utr)
    if not u:
        return None
    if u in alnum(narration) or u == alnum(ref_no):
        return "exact"
    for tok in _TOKEN8.findall(f"{narration} {ref_no}".upper()):
        if tok != u and tok in u:
            return "fragment"
    return None


def utr_fragments(narration: str, ref_no: str) -> list[str]:
    return _TOKEN8.findall(f"{narration} {ref_no}".upper())


# --- invoice references ----------------------------------------------------

_TAIL_SERIAL = re.compile(r"(?<!\d)(\d{1,6})\s*$")
_REF = (
    r"(?:[A-Za-z]{2,4}[/\-_])?"        # optional prefix like INV/ or inv_ (no bare words)
    r"(?:\d{4}-\d{2}[/\-])?"           # optional FY like 2026-27/
    r"\d{1,6}"
)
_REF_LIST = re.compile(
    r"\b(?:inv(?:oice)?s?|invc|bills?|orders?)\b"
    r"(?:\s*(?:no\.?|nos\.?|number|num|#))?\s*[:#/\-]?\s*"
    rf"(?P<first>{_REF})"
    rf"(?P<rest>(?:\s*(?:,|&|and|\+)\s*#?{_REF})*)",
    re.IGNORECASE,
)
_EACH_REF = re.compile(_REF)


def receipt_serial(value: str | None) -> int | None:
    """'INV/2026-27/00123', 'inv_00123', '00123' -> 123. A bare 8-digit date -> None."""
    if not value:
        return None
    m = _TAIL_SERIAL.search(value.strip())
    return int(m.group(1)) if m else None


def text_serials(text: str | None) -> list[int]:
    """Invoice serials mentioned in free text: 'for bills 123 & 124' -> [123, 124]."""
    if not text:
        return []
    out: list[int] = []
    for m in _REF_LIST.finditer(text):
        chunk = m.group("first") + (m.group("rest") or "")
        for ref in _EACH_REF.findall(chunk):
            s = receipt_serial(ref)
            if s is not None and s not in out:
                out.append(s)
    return out


def note_text(notes: dict[str, Any]) -> str:
    parts = []
    for k in sorted(notes):
        v = notes[k]
        if isinstance(v, (str, int, float)):
            parts.append(str(v))
    return " | ".join(parts)


# --- contacts --------------------------------------------------------------


def norm_phone(p: str | None) -> str:
    digits = re.sub(r"\D", "", p or "")
    return digits[-10:] if len(digits) >= 10 else ""


def norm_email(e: str | None) -> str:
    return (e or "").strip().lower()


def contact_match(
    email: str | None, phone: str | None, other_email: str | None, other_phone: str | None
) -> bool:
    e1, e2 = norm_email(email), norm_email(other_email)
    p1, p2 = norm_phone(phone), norm_phone(other_phone)
    return bool((e1 and e1 == e2) or (p1 and p1 == p2))


# --- names -----------------------------------------------------------------

_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "shri", "smt", "sri", "kumari", "m/s"}
_LEGAL = {"llp", "pvt", "ltd", "private", "limited", "co", "company", "the", "and", "inc"}
_ALIASES = {"mohd": "mohammed", "md": "mohammed", "mohd.": "mohammed", "muhammad": "mohammed"}
_POSSESSIVE = re.compile(r"(\w)['\u2019]s\b")
_WORD = re.compile(r"[a-z]+")


def name_tokens(text: str, *, keep_initials: bool) -> list[str]:
    text = _POSSESSIVE.sub(r"\1", (text or "").lower())
    out = []
    for w in _WORD.findall(text):
        w = _ALIASES.get(w, w)
        if w in _HONORIFICS or w in _LEGAL:
            continue
        if len(w) == 1 and not keep_initials:
            continue
        out.append(w)
    return out


def _close(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) < 4:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.88


def name_similarity(note: str, candidate_name: str) -> float:
    """How strongly free text points at a named customer.

    1.0  every name token present          ("Priya Sundaram")
    0.9  full tokens plus matching initials ("S. Priya")
    0.6  at least one full token            ("Priya", "Brightpath")
    0.0  nothing
    Initials never count on their own.
    """
    cand = [t for t in name_tokens(candidate_name, keep_initials=False) if len(t) >= 2]
    if not cand:
        return 0.0
    words = name_tokens(note, keep_initials=True)
    full_words = [w for w in words if len(w) >= 2]
    initials = {w for w in words if len(w) == 1}
    full_hits = 0
    initial_hits = 0
    for t in cand:
        if any(_close(w, t) for w in full_words):
            full_hits += 1
        elif t[0] in initials:
            initial_hits += 1
    if full_hits == 0:
        return 0.0
    if full_hits == len(cand):
        return 1.0
    if full_hits + initial_hits == len(cand):
        return 0.9
    return 0.6


def best_name_hits(note: str, names: Iterable[tuple[str, str]], floor: float) -> list[tuple[str, float]]:
    """[(id, similarity)] for every candidate whose name the note plausibly mentions."""
    hits = []
    for cid, nm in names:
        s = name_similarity(note, nm)
        if s >= floor:
            hits.append((cid, s))
    return sorted(hits, key=lambda x: -x[1])
