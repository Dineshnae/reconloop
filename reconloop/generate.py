"""Synthetic batch generator with an answer key.

World: Kaveri Home Goods (fictional D2C merchant) on Razorpay. Three weeks of
orders, daily T+2 settlements into a current account, invoices and credit
notes in the books. Every planted problem and every true link is written to
ground_truth.json so an agent can be scored, not just demoed.

The Razorpay file mirrors the documented settlement recon report
(GET /v1/settlements/recon/combined): amounts in paise, `fee` includes GST,
`tax` is the GST part. `email`/`contact` are joined from the Payments API.
"""

from __future__ import annotations

import json
import random
import string
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from . import io
from .config import DEFAULT, Config
from .models import IST
from .money import expected_fee, gst_on, to_rupees_str

GENERATOR_VERSION = "1.1"
MERCHANT = "Kaveri Home Goods Pvt Ltd"

PLANTS: dict[str, dict[str, int]] = {
    "standard": dict(
        duplicate=2, missing_invoice=3, amount_mismatch=3, fee_overcharge=2, tax_miscalc=1,
        refund_with_cn=6, refund_no_cn=2, chargeback=1, settlement_short=1, settlement_missing=1,
        bank_orphan=1, utr_collision=1, open_invoice=4, repeat_purchase=1, malformed_row=0,
        split=3, combined=2, name_trap=6, coincidental_amount=1, decoy_bank_credit=0,
    ),
    "hard": dict(
        duplicate=3, missing_invoice=4, amount_mismatch=4, fee_overcharge=3, tax_miscalc=2,
        refund_with_cn=8, refund_no_cn=3, chargeback=2, settlement_short=2, settlement_missing=2,
        bank_orphan=2, utr_collision=2, open_invoice=5, repeat_purchase=2, malformed_row=1,
        split=4, combined=3, name_trap=10, coincidental_amount=2, decoy_bank_credit=1,
    ),
}
REF_MIX = {
    "standard": [("exact_receipt", 0.62), ("receipt_drift", 0.16), ("notes_ref", 0.08), ("contact_only", 0.14)],
    "hard": [("exact_receipt", 0.40), ("receipt_drift", 0.22), ("notes_ref", 0.14), ("contact_only", 0.24)],
}
UTR_STYLE = {
    "standard": [("full", 0.65), ("ref_col", 0.15), ("suffix", 0.10), ("prefix", 0.05), ("none", 0.05)],
    "hard": [("full", 0.40), ("ref_col", 0.15), ("suffix", 0.15), ("prefix", 0.15), ("none", 0.15)],
}
METHODS = [("upi", 0.55), ("card", 0.25), ("netbanking", 0.10), ("wallet", 0.10)]
PRICES = [
    299, 349, 399, 449, 499, 549, 599, 649, 699, 749, 799, 899, 999, 1099, 1199, 1299, 1499,
    1599, 1799, 1999, 2199, 2499, 2799, 2999, 3499, 3999, 4499, 4999, 5999, 6999, 7999, 8999,
]
SHIPPING = [0, 0, 0, 49, 79]

# Name traps: (true customer, decoy customer with the same amount and date, payer note, kind)
#   easy        -> deterministic evidence is enough
#   semantic    -> needs reading the note (on-behalf, nickname, business rep, near-miss names)
#   undecidable -> nobody can know; the right move is to ask a human
NAME_TRAPS = [
    ("Priya Sundaram", "Deepa Nair", "S. Priya - bedsheet order", "easy"),
    ("Lakshmi Raman", "Arjun Mehta", "Paid by Arjun for his mother's order (Mrs. Lakshmi)", "semantic"),
    ("Brightpath Interiors LLP", "Kiran Kumar", "Kiran from Brightpath, office order", "semantic"),
    ("Mohammed Irfan Shaikh", "Irfana Begum", "Mohd Irfan", "semantic"),
    ("Venkatesh Iyer", "Harish Kumar", "Venky - curtains", "semantic"),
    ("Ananya Chatterjee", "Anand Chawla", "Anu (Ananya C.) birthday gift", "easy"),
    ("Gurpreet Kaur Sandhu", "Preeti Sandhu", "for Gurpreet ji's order", "semantic"),
    ("Nilaya Homestays", "Nila Varghese", "Nilaya homestay - linen restock", "easy"),
    ("Rahul Deshpande", "Rahul Desai", "Rahul D. - cushion covers", "undecidable"),
    ("Sneha Kulkarni", "Snehal Kapoor", "gift from Rohan to Sneha K.", "semantic"),
    ("Fatima Sheikh", "Farah Siddiqui", "Paid for Ammi's order - Fatima Sheikh", "easy"),
    ("Joseph D'Souza", "Josephine Dsouza", "Joe D'Souza - lamp", "semantic"),
]

FIRST = [
    "Aarav", "Vihaan", "Aditya", "Ishaan", "Kabir", "Reyansh", "Ayaan", "Vivaan", "Siddharth",
    "Nikhil", "Varun", "Karthik", "Pranav", "Rohit", "Abhishek", "Manish", "Suresh", "Ramesh",
    "Ganesh", "Naveen", "Sanjay", "Vikram", "Aakash", "Tanvi", "Meera", "Isha", "Diya", "Aditi",
    "Pooja", "Neha", "Shruti", "Kavitha", "Divya", "Swati", "Nandini", "Riya", "Sanya", "Tara",
    "Zoya", "Aisha", "Nisha", "Rekha", "Uma", "Jaya", "Bhavna", "Harpreet", "Simran", "Manpreet",
    "Imran", "Salman", "Ayesha", "Sana", "John", "Maria", "Anjali", "Latha", "Revathi", "Sowmya",
]
LAST = [
    "Sharma", "Verma", "Gupta", "Agarwal", "Joshi", "Pillai", "Menon", "Rao", "Reddy", "Naidu",
    "Patel", "Shah", "Jain", "Bose", "Das", "Banerjee", "Mukherjee", "Ghosh", "Singh", "Gill",
    "Khan", "Ansari", "Qureshi", "Fernandes", "Pereira", "George", "Kurian", "Hegde", "Shetty",
    "Bhat", "Kamath", "Pai", "Nambiar", "Krishnan", "Subramanian", "Chandra", "Saxena", "Tiwari",
    "Pandey", "Mishra", "Yadav", "Chauhan", "Rathore", "Trivedi", "Dutta", "Sen",
]
BUSINESSES = ["Sharma Traders", "Greenleaf Cafe", "Urban Nest Studio", "Coastal Stays LLP", "Aroma Kitchens Pvt Ltd"]
PAYER_FIRST = ["arjun", "rohan", "kiran", "amit", "sunil", "deepak", "farhan", "vinod", "ravi", "ajay"]


@dataclass(frozen=True)
class GenConfig:
    seed: int = 7
    difficulty: str = "standard"
    start: date = date(2026, 8, 3)       # a Monday
    days: int = 21
    orders: int = 150                    # regular orders, before planted specials


@dataclass
class Person:
    name: str
    email: str
    phone: str


def _weighted(rng: random.Random, items: list[tuple[str, float]]) -> str:
    r = rng.random() * sum(w for _, w in items)
    for k, w in items:
        r -= w
        if r <= 0:
            return k
    return items[-1][0]


def _skip_sunday(d: date) -> date:
    return d + timedelta(days=1) if d.weekday() == 6 else d


class _World:
    def __init__(self, gc: GenConfig, cfg: Config):
        if gc.difficulty not in PLANTS:
            raise ValueError(f"difficulty must be one of {sorted(PLANTS)}")
        self.gc = gc
        self.cfg = cfg
        self.rng = random.Random(gc.seed)
        self.p = PLANTS[gc.difficulty]
        self.end = gc.start + timedelta(days=gc.days - 1)
        self.serial = 101
        self.cn_serial = 11
        self.ids: set[str] = set()
        self.invoices: dict[str, dict[str, Any]] = {}
        self.credit_notes: dict[str, dict[str, Any]] = {}
        self.txns: list[dict[str, Any]] = []
        self.links: dict[str, list[list[str]]] = {"payment_invoice": [], "refund_credit_note": [], "settlement_bank": []}
        self.exceptions: list[dict[str, str]] = []
        self.scenario: dict[str, str] = {}
        self.expected_review: list[dict[str, Any]] = []
        self.equivalent: list[list[str]] = []
        self.regular: list[dict[str, Any]] = []     # simple exact-receipt payments, safe to decorate
        self.customers = self._customers()

    # -- ids, time, people ---------------------------------------------------
    def rid(self, prefix: str) -> str:
        alphabet = string.ascii_letters + string.digits
        while True:
            v = prefix + "".join(self.rng.choice(alphabet) for _ in range(14))
            if v not in self.ids:
                self.ids.add(v)
                return v

    def ts(self, d: date, lo: int = 8, hi: int = 23) -> int:
        t = time(self.rng.randint(lo, hi - 1), self.rng.randint(0, 59), self.rng.randint(0, 59))
        return int(datetime.combine(d, t, tzinfo=IST).timestamp())

    def day(self, margin_end: int = 0) -> date:
        return self.gc.start + timedelta(days=self.rng.randint(0, self.gc.days - 1 - margin_end))

    def _phone(self) -> str:
        return "9" + "".join(self.rng.choice(string.digits) for _ in range(9))

    def person(self, name: str) -> Person:
        handle = "".join(c for c in name.lower() if c.isalpha())[:12]
        return Person(name, f"{handle}{self.rng.randint(10, 99)}@example.in", self._phone())

    def _customers(self) -> list[Person]:
        names: set[str] = set()
        while len(names) < 85:
            names.add(f"{self.rng.choice(FIRST)} {self.rng.choice(LAST)}")
        pool = [self.person(n) for n in sorted(names)] + [self.person(b) for b in BUSINESSES]
        self.rng.shuffle(pool)
        return pool

    def payer(self) -> Person:
        first = self.rng.choice(PAYER_FIRST)
        return Person(first.title(), f"{first}.{self.rng.randint(1000, 9999)}@example.net", self._phone())

    def amount(self) -> int:
        items = self.rng.choice([1, 1, 1, 2, 2, 3])
        rupees = sum(self.rng.choice(PRICES) for _ in range(items)) + self.rng.choice(SHIPPING)
        return rupees * 100

    def unique_amount(self, around: date) -> int:
        taken = {
            inv["amount"] for inv in self.invoices.values()
            if abs((inv["date"] - around).days) <= 3
        } | {t["amount"] for t in self.txns}
        while True:
            a = (self.rng.randint(300, 9000) * 100) + self.rng.choice([0, 50])
            if a not in taken:
                return a

    # -- building blocks -----------------------------------------------------
    def add_invoice(self, d: date, amount: int, cust: Person, serial: int | None = None) -> str:
        if serial is None:
            serial = self.next_serial()
        doc = f"INV/2026-27/{serial:05d}"
        self.invoices[doc] = dict(doc=doc, serial=serial, date=d, amount=amount, cust=cust)
        return doc

    def next_serial(self) -> int:
        s = self.serial
        self.serial += 1
        return s

    def add_payment(
        self, d: date, amount: int, payer: Person, *, receipt: str | None = None,
        notes: dict[str, Any] | None = None, method: str | None = None,
        order_id: str | None = None, created_at: int | None = None,
    ) -> dict[str, Any]:
        method = method or _weighted(self.rng, METHODS)
        base, gst = expected_fee(amount, method, self.cfg.rate_card)  # type: ignore[misc]
        row = dict(
            entity_id=self.rid("pay_"), type="payment", debit=0, credit=amount - base - gst,
            amount=amount, currency="INR", fee=base + gst, tax=gst, on_hold=False, settled=True,
            created_at=created_at or self.ts(d), settled_at=None, settlement_id=None,
            settlement_utr=None, order_id=order_id or self.rid("order_"), order_receipt=receipt,
            method=method, notes=notes or {}, payment_id=None, dispute_id=None,
            description=None, email=payer.email, contact=payer.phone, _day=d,
        )
        self.txns.append(row)
        return row

    def link(self, kind: str, left: str, right: str) -> None:
        self.links[kind].append([left, right])

    def flag(self, code: str, entity: str) -> None:
        self.exceptions.append({"code": code, "entity_id": entity})

    def reference(self, serial: int, style: str) -> tuple[str | None, dict[str, Any]]:
        doc = f"INV/2026-27/{serial:05d}"
        if style == "exact_receipt":
            return doc, {}
        if style == "receipt_drift":
            fmt = self.rng.choice(["inv_{:05d}", "INV-{:05d}", "{:05d}", "INV{}", "inv-{}"])
            return fmt.format(serial), {}
        if style == "notes_ref":
            note = self.rng.choice([
                {"invoice_no": doc},
                {"note": f"payment against bill {serial:04d}"},
                {"note": f"pymt agnst invc no. {serial}"},
                {"note": f"order #{serial} - thanks"},
            ])
            return None, note
        return None, self.rng.choice([{}, {"note": "thanks"}, {"note": "home order"}])

    # -- scenarios -----------------------------------------------------------
    def regular_orders(self) -> None:
        for _ in range(self.gc.orders):
            d, cust, amt = self.day(), self.rng.choice(self.customers), self.amount()
            style = _weighted(self.rng, REF_MIX[self.gc.difficulty])
            inv = self.add_invoice(d, amt, cust)
            receipt, notes = self.reference(self.invoices[inv]["serial"], style)
            pay = self.add_payment(d, amt, cust, receipt=receipt, notes=notes)
            self.link("payment_invoice", pay["entity_id"], inv)
            self.scenario[pay["entity_id"]] = style
            if style == "exact_receipt":
                self.regular.append(pay)

    def pick_regular(self, *, not_upi: bool = False, max_day_offset: int | None = None) -> dict[str, Any]:
        pool = [
            p for p in self.regular
            if not p.get("_used") and (not not_upi or p["method"] != "upi")
            and (max_day_offset is None or (self.end - p["_day"]).days >= max_day_offset)
        ]
        p = self.rng.choice(pool)
        p["_used"] = True
        return p

    def duplicates(self) -> None:
        for _ in range(self.p["duplicate"]):
            orig = self.pick_regular()
            twin = self.add_payment(
                orig["_day"], orig["amount"], Person("", orig["email"], orig["contact"]),
                receipt=orig["order_receipt"], notes=dict(orig["notes"]), method=orig["method"],
                order_id=orig["order_id"], created_at=orig["created_at"] + self.rng.randint(180, 900),
            )
            self.flag("DUPLICATE_PAYMENT", twin["entity_id"])
            self.scenario[orig["entity_id"]] = "duplicate_original"
            self.scenario[twin["entity_id"]] = "duplicate_twin"

    def missing_invoices(self) -> None:
        for _ in range(self.p["missing_invoice"]):
            d = self.day()
            cust = self.person(f"{self.rng.choice(FIRST)} {self.rng.choice(LAST)}")
            serial = self.next_serial()                    # allocated, never raised
            pay = self.add_payment(d, self.unique_amount(d), cust, receipt=f"INV/2026-27/{serial:05d}")
            self.flag("MISSING_INVOICE", pay["entity_id"])
            self.scenario[pay["entity_id"]] = "missing_invoice"

    def amount_mismatches(self) -> None:
        for _ in range(self.p["amount_mismatch"]):
            d, cust = self.day(), self.rng.choice(self.customers)
            paid = self.rng.choice(PRICES[15:]) * 100
            delta = self.rng.choice([5000, 10000, 15000])   # coupon applied after invoicing
            delta = min(delta, (paid // 9) // 5000 * 5000)  # keep it inside the 10% band
            inv = self.add_invoice(d, paid + delta, cust)
            pay = self.add_payment(d, paid, cust, receipt=inv)
            self.link("payment_invoice", pay["entity_id"], inv)
            self.flag("AMOUNT_MISMATCH", pay["entity_id"])
            self.scenario[pay["entity_id"]] = "amount_mismatch"

    def splits(self) -> None:
        hard = self.gc.difficulty == "hard"
        for i in range(self.p["split"]):
            d, cust = self.day(margin_end=1), self.rng.choice(self.customers)
            total = self.rng.choice(PRICES[20:]) * 100 + self.rng.choice(PRICES[10:20]) * 100
            first = int(total * self.rng.uniform(0.35, 0.6)) // 100 * 100
            inv = self.add_invoice(d, total, cust)
            p1 = self.add_payment(d, first, cust, receipt=inv, notes={"note": "part payment 1 of 2"})
            if hard and i % 2 == 0:
                p2 = self.add_payment(d + timedelta(days=1), total - first, cust, notes={"note": "balance"})
            else:
                p2 = self.add_payment(d + timedelta(days=1), total - first, cust, receipt=inv, notes={"note": "part 2 of 2"})
            for p in (p1, p2):
                self.link("payment_invoice", p["entity_id"], inv)
                self.scenario[p["entity_id"]] = "split_payment"

    def combined(self) -> None:
        hard = self.gc.difficulty == "hard"
        for i in range(self.p["combined"]):
            d, cust = self.day(), self.rng.choice(self.customers)
            a1, a2 = self.amount(), self.amount()
            inv1 = self.add_invoice(d, a1, cust)
            inv2 = self.add_invoice(d, a2, cust)
            s1, s2 = self.invoices[inv1]["serial"], self.invoices[inv2]["serial"]
            note = {"note": "for both my orders"} if (hard and i % 2 == 0) else {"note": f"for invoices {s1} & {s2}"}
            pay = self.add_payment(d, a1 + a2, cust, notes=note)
            self.link("payment_invoice", pay["entity_id"], inv1)
            self.link("payment_invoice", pay["entity_id"], inv2)
            self.scenario[pay["entity_id"]] = "combined_payment"

    def repeat_purchases(self) -> None:
        for _ in range(self.p["repeat_purchase"]):
            d, cust, amt = self.day(), self.rng.choice(self.customers), self.amount()
            inv1, inv2 = self.add_invoice(d, amt, cust), self.add_invoice(d, amt, cust)
            p1 = self.add_payment(d, amt, cust, created_at=self.ts(d, 9, 13))
            p2 = self.add_payment(d, amt, cust, created_at=self.ts(d, 15, 22))
            self.link("payment_invoice", p1["entity_id"], inv1)
            self.link("payment_invoice", p2["entity_id"], inv2)
            self.equivalent.append(sorted([inv1, inv2]))
            for p in (p1, p2):
                self.scenario[p["entity_id"]] = "repeat_purchase"

    def name_traps(self) -> None:
        chosen = self.rng.sample(NAME_TRAPS, self.p["name_trap"])
        for true_name, decoy_name, note, kind in chosen:
            d = self.day()
            amt = self.amount()
            true_c, decoy_c = self.person(true_name), self.person(decoy_name)
            # Shuffle creation order so "lowest invoice number wins" is not a free answer.
            if self.rng.random() < 0.5:
                inv_true = self.add_invoice(d, amt, true_c)
                inv_decoy = self.add_invoice(d, amt, decoy_c)
            else:
                inv_decoy = self.add_invoice(d, amt, decoy_c)
                inv_true = self.add_invoice(d, amt, true_c)
            pay = self.add_payment(d, amt, self.payer(), notes={"note": note})
            self.link("payment_invoice", pay["entity_id"], inv_true)
            self.scenario[pay["entity_id"]] = f"name_trap_{kind}"
            if kind == "undecidable":
                self.expected_review.append({"subject": pay["entity_id"], "related": [inv_true, inv_decoy]})
            else:
                self.flag("OPEN_INVOICE", inv_decoy)

    def coincidental_amounts(self) -> None:
        for _ in range(self.p["coincidental_amount"]):
            d, amt = self.day(), self.amount()
            inv = self.add_invoice(d, amt, self.rng.choice(self.customers))
            pay = self.add_payment(d, amt, self.payer(), notes=self.rng.choice([{}, {"note": "thanks"}]))
            self.flag("OPEN_INVOICE", inv)
            self.flag("MISSING_INVOICE", pay["entity_id"])
            self.scenario[pay["entity_id"]] = "coincidental_amount"

    def open_invoices(self) -> None:
        for _ in range(self.p["open_invoice"]):
            inv = self.add_invoice(self.day(), self.unique_amount(self.gc.start), self.rng.choice(self.customers))
            self.flag("OPEN_INVOICE", inv)

    def fee_problems(self) -> None:
        for _ in range(self.p["fee_overcharge"]):
            p = self.pick_regular(not_upi=True)
            bump = int(round(p["amount"] * self.rng.choice([0.004, 0.005, 0.0075])))
            base = p["fee"] - p["tax"] + max(bump, 100)
            p["tax"] = gst_on(base, self.cfg.rate_card)
            p["fee"] = base + p["tax"]
            p["credit"] = p["amount"] - p["fee"]
            self.flag("FEE_MISMATCH", p["entity_id"])
        for _ in range(self.p["tax_miscalc"]):
            p = self.pick_regular(not_upi=True)
            base = p["fee"] - p["tax"]                        # contract base stays correct
            p["tax"] += self.rng.randint(25, 95)              # GST line is wrong
            p["fee"] = base + p["tax"]
            p["credit"] = p["amount"] - p["fee"]
            self.flag("TAX_MISMATCH", p["entity_id"])

    def refunds(self) -> None:
        for with_cn in (True, False):
            count = self.p["refund_with_cn"] if with_cn else self.p["refund_no_cn"]
            for _ in range(count):
                p = self.pick_regular(max_day_offset=2)
                rd = p["_day"] + timedelta(days=self.rng.randint(1, 2))
                amt = p["amount"] if self.rng.random() < 0.6 else (p["amount"] // 200) * 100
                notes: dict[str, Any] = {}
                ref = self.rid("rfnd_")
                if with_cn:
                    cn = f"CN/2026-27/{self.cn_serial:05d}"
                    self.cn_serial += 1
                    self.credit_notes[cn] = dict(doc=cn, date=rd, amount=amt, cust=self._cust_of(p), against=p["order_receipt"])
                    if self.rng.random() < 0.5:
                        notes = {"credit_note": cn}
                    self.link("refund_credit_note", ref, cn)
                else:
                    self.flag("MISSING_CREDIT_NOTE", ref)
                self.txns.append(dict(
                    entity_id=ref, type="refund", debit=amt, credit=0, amount=amt, currency="INR",
                    fee=0, tax=0, on_hold=False, settled=True, created_at=self.ts(rd),
                    settled_at=None, settlement_id=None, settlement_utr=None, order_id=None,
                    order_receipt=None, method=None, notes=notes, payment_id=p["entity_id"],
                    dispute_id=None, description=None, email=None, contact=None, _day=rd,
                ))

    def _cust_of(self, pay: dict[str, Any]) -> Person:
        return self.invoices[pay["order_receipt"]]["cust"]

    def chargebacks(self) -> None:
        for _ in range(self.p["chargeback"]):
            p = self.pick_regular(max_day_offset=3)
            ad = p["_day"] + timedelta(days=self.rng.randint(2, 3))
            adj = self.rid("adj_")
            self.txns.append(dict(
                entity_id=adj, type="adjustment", debit=p["amount"], credit=0, amount=p["amount"],
                currency="INR", fee=0, tax=0, on_hold=False, settled=True, created_at=self.ts(ad),
                settled_at=None, settlement_id=None, settlement_utr=None, order_id=None,
                order_receipt=None, method=None, notes={}, payment_id=p["entity_id"],
                dispute_id=self.rid("disp_"), description="Dispute debit - chargeback",
                email=None, contact=None, _day=ad,
            ))
            self.flag("DISPUTE_DEBIT", adj)

    # -- settlements and bank ------------------------------------------------
    def settle(self) -> list[dict[str, Any]]:
        batches: dict[date, list[dict[str, Any]]] = {}
        for t in self.txns:
            lag = 2 if t["type"] == "payment" else 1
            sd = _skip_sunday(t["_day"] + timedelta(days=lag))
            batches.setdefault(sd, []).append(t)
        # A day whose refunds and chargebacks exceed its collections cannot settle.
        # Carry those debits into the next settlement, as a negative balance would be.
        dates = sorted(batches)
        for i, sd in enumerate(dates):
            rows = batches[sd]
            if sum(t["credit"] - t["debit"] for t in rows) > 0:
                continue
            target = dates[i + 1] if i + 1 < len(dates) else _skip_sunday(sd + timedelta(days=1))
            if target not in batches:
                dates.append(target)
            batches[sd] = [t for t in rows if t["debit"] == 0]
            batches.setdefault(target, []).extend(t for t in rows if t["debit"] > 0)
        batches = {d: rows for d, rows in batches.items() if rows}
        settlements = []
        for sd in sorted(batches):
            sid = self.rid("setl_")
            utr = f"YESBR{sd:%Y%m%d}{self.rng.randint(10**7, 10**8 - 1)}"
            s = dict(id=sid, date=sd, utr=utr, rows=batches[sd])
            settlements.append(s)
        self._collision_traps(settlements)
        for s in settlements:
            at = int(datetime.combine(s["date"], time(11, self.rng.randint(0, 59)), tzinfo=IST).timestamp())
            for t in s["rows"]:
                t.update(settled_at=at, settlement_id=s["id"], settlement_utr=s["utr"])
            s["net"] = sum(t["credit"] - t["debit"] for t in s["rows"])
            if s["net"] <= 0:
                raise RuntimeError("generator produced a non-positive settlement; try another seed")
        return settlements

    def _collision_traps(self, settlements: list[dict[str, Any]]) -> None:
        self.collision_ids: set[str] = set()
        pairs = [
            (a, b) for a, b in zip(settlements, settlements[1:])
            if (b["date"] - a["date"]).days == 1
        ]
        self.rng.shuffle(pairs)
        made = 0
        for a, b in pairs:
            if made >= self.p["utr_collision"]:
                break
            if {a["id"], b["id"]} & self.collision_ids:
                continue
            na = sum(t["credit"] - t["debit"] for t in a["rows"])
            nb = sum(t["credit"] - t["debit"] for t in b["rows"])
            low, diff = (a, nb - na) if na < nb else (b, na - nb)
            if diff <= 0:
                continue
            low["rows"].append(dict(
                entity_id=self.rid("adj_"), type="adjustment", debit=0, credit=diff, amount=diff,
                currency="INR", fee=0, tax=0, on_hold=False, settled=True,
                created_at=int(datetime.combine(low["date"], time(9, 0), tzinfo=IST).timestamp()),
                settled_at=None, settlement_id=None, settlement_utr=None, order_id=None,
                order_receipt=None, method=None, notes={}, payment_id=None, dispute_id=None,
                description="Reserve release", email=None, contact=None, _day=low["date"],
            ))
            self.txns.append(low["rows"][-1])
            self.collision_ids |= {a["id"], b["id"]}
            made += 1

    def bank(self, settlements: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], date]:
        stmt_end = max(s["date"] for s in settlements) + timedelta(days=1)
        free = [s for s in settlements if s["id"] not in self.collision_ids]
        self.rng.shuffle(free)
        short = {s["id"] for s in free[: self.p["settlement_short"]]}
        overdue = [s for s in free[self.p["settlement_short"]:] if (stmt_end - s["date"]).days >= 3]
        missing = {s["id"] for s in overdue[: self.p["settlement_missing"]]}

        lines: list[dict[str, Any]] = []
        for s in settlements:
            if s["id"] in missing:
                self.flag("SETTLEMENT_NOT_IN_BANK", s["id"])
                continue
            in_trap = s["id"] in self.collision_ids
            lag = 0 if (in_trap or self.rng.random() < 0.85) else 1
            style = "none" if in_trap else _weighted(self.rng, UTR_STYLE[self.gc.difficulty])
            if s["id"] in short:
                style = "full"
            credit = s["net"]
            if s["id"] in short:
                credit -= self.rng.choice([1770, 2950])      # bank charge + GST netted off
                self.flag("SETTLEMENT_AMOUNT_MISMATCH", s["id"])
            narration, ref = self._narration(s["utr"], style)
            lines.append(dict(date=s["date"] + timedelta(days=lag), narration=narration, ref=ref,
                              debit=0, credit=credit, settlement=s["id"]))

        nets = {s["net"] for s in settlements}
        for _ in range(self.p["bank_orphan"]):
            amt = self.unique_amount(self.gc.start) * 7
            while amt in nets:
                amt += 100
            fake = f"YESBR{self.end:%Y%m%d}{self.rng.randint(10**7, 10**8 - 1)}"
            lines.append(dict(date=self.day(), narration=f"NEFT CR-{fake}-RAZORPAY SOFTWARE PVT LTD-KAVERI HOME GOODS",
                              ref=self._bank_ref(), debit=0, credit=amt, orphan=True))
        for sid in sorted(missing)[: self.p["decoy_bank_credit"]]:
            s = next(x for x in settlements if x["id"] == sid)
            lines.append(dict(date=s["date"], narration="IMPS CR/612345678901/SHARMA TRADERS",
                              ref=self._bank_ref(), debit=0, credit=s["net"]))
        noise = [
            ("NEFT DR-SRI BALAJI PACKAGING-INV 4471", "debit"), ("SALARY TRF AUG-2026", "debit"),
            ("GST PMT CHALLAN CPIN 26080012", "debit"), ("ACH DR-HDFC ERGO INSURANCE", "debit"),
            ("NEFT DR-DELHIVERY LTD", "debit"), ("INT.PD:SWEEP DEPOSIT", "credit"),
            ("UPI/CR/623411/DIRECT TRANSFER/OKAXIS", "credit"), ("CHQ DEP 000211 CLG", "credit"),
        ]
        for k in range(self.rng.randint(9, 14)):
            text, side = noise[0] if k < self.p["malformed_row"] else self.rng.choice(noise)
            amt = self.rng.randint(500, 60000) * 100
            lines.append(dict(date=self.day(), narration=text, ref=self._bank_ref(),
                              debit=amt if side == "debit" else 0, credit=amt if side == "credit" else 0,
                              malformed=k < self.p["malformed_row"]))

        lines.sort(key=lambda x: (x["date"], x["narration"]))
        balance = 425_000_00
        rows = []
        for i, ln in enumerate(lines, start=1):
            balance += ln["credit"] - ln["debit"]
            line_id = f"BNK{i:05d}"
            debit_s = to_rupees_str(ln["debit"]) if ln["debit"] else ""
            if ln.get("malformed"):
                debit_s = debit_s.replace("0", "O", 1)           # OCR-style typo: letter O for zero
                self.flag("MALFORMED_ROW", line_id)
            rows.append(dict(
                line_id=line_id, txn_date=ln["date"].strftime("%d-%m-%Y"), narration=ln["narration"],
                ref_no=ln["ref"], debit=debit_s,
                credit=to_rupees_str(ln["credit"]) if ln["credit"] else "",
                balance=to_rupees_str(balance),
            ))
            if ln.get("settlement"):
                self.link("settlement_bank", ln["settlement"], line_id)
            if ln.get("orphan"):
                self.flag("UNMATCHED_BANK_CREDIT", line_id)
        return rows, stmt_end

    def _bank_ref(self) -> str:
        return f"S{self.rng.randint(10**7, 10**8 - 1)}"

    def _narration(self, utr: str, style: str) -> tuple[str, str]:
        if style == "full":
            return f"NEFT CR-{utr}-RAZORPAY SOFTWARE PVT LTD-KAVERI HOME GOODS", self._bank_ref()
        if style == "ref_col":
            return "RAZORPAY SOFTWARE PRIVATE LIMITED", utr
        if style == "suffix":
            return f"NEFT CR-RAZORPAY SOFTW-{utr[-10:]}", self._bank_ref()
        if style == "prefix":
            return f"RTGS/{utr[:11]}/RAZORPAY", self._bank_ref()
        return self.rng.choice(["BY CLG RAZORPAY SETTLEMENT", "RAZORPAY SOFTWARE PVT LTD"]), self._bank_ref()

    # -- output ----------------------------------------------------------------
    def build(self, out: Path) -> dict[str, Any]:
        self.regular_orders()
        self.splits()
        self.combined()
        self.repeat_purchases()
        self.name_traps()
        self.coincidental_amounts()
        self.open_invoices()
        self.missing_invoices()
        self.amount_mismatches()
        self.duplicates()
        self.fee_problems()
        self.refunds()
        self.chargebacks()
        settlements = self.settle()
        bank_rows, stmt_end = self.bank(settlements)

        out.mkdir(parents=True, exist_ok=True)
        txns = sorted(self.txns, key=lambda t: (t["created_at"], t["entity_id"]))
        io.write_csv(out / io.RZP_FILE, (self._rzp_row(t) for t in txns), io.RZP_COLUMNS)
        io.write_csv(out / io.BANK_FILE, bank_rows, io.BANK_COLUMNS)
        docs = []
        for inv in self.invoices.values():
            c = inv["cust"]
            docs.append(dict(doc_no=inv["doc"], doc_type="invoice", doc_date=inv["date"].isoformat(),
                             customer_name=c.name, customer_email=c.email, customer_phone=c.phone,
                             amount=to_rupees_str(inv["amount"]), against_doc=""))
        for cn in self.credit_notes.values():
            c = cn["cust"]
            docs.append(dict(doc_no=cn["doc"], doc_type="credit_note", doc_date=cn["date"].isoformat(),
                             customer_name=c.name, customer_email=c.email, customer_phone=c.phone,
                             amount=to_rupees_str(cn["amount"]), against_doc=cn["against"]))
        docs.sort(key=lambda r: (r["doc_date"], r["doc_no"]))
        io.write_csv(out / io.BOOKS_FILE, docs, io.BOOK_COLUMNS)

        meta = dict(
            merchant=MERCHANT, seed=self.gc.seed, difficulty=self.gc.difficulty,
            generator_version=GENERATOR_VERSION, period_start=self.gc.start.isoformat(),
            period_end=self.end.isoformat(), statement_end=stmt_end.isoformat(),
            counts=dict(razorpay_rows=len(txns), bank_lines=len(bank_rows), book_docs=len(docs),
                        settlements=len(settlements)),
        )
        io.write_json(out / io.META_FILE, meta)
        truth = dict(
            meta=meta,
            links={k: sorted(v) for k, v in self.links.items()},
            exceptions=sorted(self.exceptions, key=lambda e: (e["code"], e["entity_id"])),
            expected_review=self.expected_review,
            equivalent_invoices=self.equivalent,
            scenarios=dict(sorted(self.scenario.items())),
        )
        io.write_json(out / io.TRUTH_FILE, truth)
        return meta

    @staticmethod
    def _rzp_row(t: dict[str, Any]) -> dict[str, Any]:
        row = {k: v for k, v in t.items() if not k.startswith("_")}
        row["notes"] = json.dumps(row["notes"], sort_keys=True) if row["notes"] else ""
        row["on_hold"] = "true" if row["on_hold"] else "false"
        row["settled"] = "true" if row["settled"] else "false"
        return {k: ("" if v is None else v) for k, v in row.items()}


def generate(out: str | Path, seed: int = 7, difficulty: str = "standard", cfg: Config = DEFAULT) -> dict[str, Any]:
    return _World(GenConfig(seed=seed, difficulty=difficulty), cfg).build(Path(out))
