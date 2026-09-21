"""Which invoice does each payment pay, and which credit note covers each refund?

Passes run cheapest and most certain first:

  1. exact_receipt      order_receipt is the invoice number
  2. normalized_ref     serial read from receipt or notes ('inv_00123', 'bill 0123');
                        multi_ref when a note lists several invoices that add up
  3. scored             identity evidence (contact, name) + amount + date, with a
                        margin over the runner-up and a check that no other payment
                        has an equal claim
  4. split / combined   subset-sum over one customer's payments or invoices
  5. scored again       splits can free up invoices that were contested
  6. holds              reference found but amount out of band: partial or review
  7. tie-breaker        model (or human) picks among what is left; every answer
                        is re-verified here before anything is linked
  8. leftovers          nothing fits at all -> MISSING_INVOICE

A payment is never linked on amount and date alone.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from .audit import AuditLog
from .config import Config
from .models import BookDoc, Link, ReconException, ReviewCase, RzpTxn
from .money import inr
from .normalize import contact_match, name_similarity, note_text, receipt_serial, text_serials
from .resolver import NONE, UNSURE, Decision, Resolver

EPS = 1e-9


@dataclass
class Scored:
    doc: str
    score: float
    identity: bool
    ev: dict[str, Any] = field(default_factory=dict)


class BookMatcher:
    def __init__(self, cfg: Config, resolver: Resolver, audit: AuditLog):
        self.cfg = cfg
        self.resolver = resolver
        self.audit = audit
        self.links: list[Link] = []
        self.exceptions: list[ReconException] = []
        self.reviews: list[ReviewCase] = []
        self.resolver_log: list[dict[str, Any]] = []
        self.outcome: dict[str, str] = {}

    # ------------------------------------------------------------------ setup
    def run(self, txns: list[RzpTxn], books: list[BookDoc], duplicates: set[str]) -> None:
        self.by_id = {t.entity_id: t for t in txns}
        payments = sorted(
            (t for t in txns if t.type == "payment" and t.entity_id not in duplicates),
            key=lambda t: (t.created_at, t.entity_id),
        )
        self.inv = {d.doc_no: d for d in books if d.doc_type == "invoice"}
        self.cns = {d.doc_no: d for d in books if d.doc_type == "credit_note"}
        self.by_serial: dict[int, list[str]] = defaultdict(list)
        for doc in sorted(self.inv):
            s = receipt_serial(doc)
            if s is not None:
                self.by_serial[s].append(doc)
        self.open: set[str] = set(self.inv)
        self.pending: dict[str, RzpTxn] = {p.entity_id: p for p in payments}
        self.pay_links: dict[str, list[str]] = defaultdict(list)
        self.ref_hold: dict[str, str] = {}
        self.ref_to_closed: dict[str, str] = {}
        self.subset_ambiguous: dict[str, list[str]] = {}
        self.dangling: dict[str, list[int]] = {}
        self.in_review_docs: set[str] = set()
        self._serial_cache: dict[str, list[int]] = {}
        self._note_cache: dict[str, str] = {}

        self._pass_exact()
        self._pass_refs()
        self._pass_scored()
        self._pass_subsets()
        self._pass_scored()
        self._pass_holds()
        self._pass_resolver()
        self._pass_leftovers()
        self._match_refunds([t for t in txns if t.type == "refund"])
        self._open_invoices()

    # --------------------------------------------------------------- helpers
    def _note(self, p: RzpTxn) -> str:
        if p.entity_id not in self._note_cache:
            self._note_cache[p.entity_id] = note_text(p.notes)
        return self._note_cache[p.entity_id]

    def _serials(self, p: RzpTxn) -> list[int]:
        if p.entity_id in self._serial_cache:
            return self._serial_cache[p.entity_id]
        out: list[int] = []
        for v in (p.order_receipt, p.notes.get("invoice_no")):
            s = receipt_serial(v) if isinstance(v, str) else None
            if s is not None and s not in out:
                out.append(s)
        free = note_text({k: v for k, v in p.notes.items() if k != "invoice_no"})
        for s in text_serials(free):
            if s not in out:
                out.append(s)
        self._serial_cache[p.entity_id] = out
        return out

    def _in_band(self, paid: int, invoiced: int) -> bool:
        return abs(paid - invoiced) <= self.cfg.tol.amount_mismatch_ratio * invoiced

    def _days(self, p: RzpTxn, doc: str) -> int:
        return abs((self.inv[doc].doc_date - p.created_date).days)

    def _candidates(self, p: RzpTxn) -> list[str]:
        win = self.cfg.tol.book_window_days
        return [
            d for d in sorted(self.open)
            if self._days(p, d) <= win and self._in_band(p.amount, self.inv[d].amount)
        ]

    def _score(self, p: RzpTxn, doc: str) -> Scored:
        inv, w, th = self.inv[doc], self.cfg.weights, self.cfg.thresholds
        s = 0.0
        ev: dict[str, Any] = {}
        if receipt_serial(doc) in self._serials(p):
            s += w.reference
            ev["reference"] = True
        cm = contact_match(p.email, p.contact, inv.customer_email, inv.customer_phone)
        ev["contact_match"] = cm
        if cm:
            s += w.contact
        delta = p.amount - inv.amount
        ev["amount_delta"] = delta
        if delta == 0:
            s += w.amount_exact
        elif self._in_band(p.amount, inv.amount):
            s += w.amount_near
        days = self._days(p, doc)
        ev["days_apart"] = days
        s += w.same_day if days == 0 else (w.next_day if days == 1 else 0.0)
        ns = name_similarity(self._note(p), inv.customer_name)
        ev["name_similarity"] = ns
        named = ns >= th.name_evidence
        if named:
            s += w.name * ns
        identity = bool(ev.get("reference")) or cm or named
        return Scored(doc, round(min(s, 1.0), 4), identity, ev)

    def _fungible_docs(self, a: str, b: str) -> bool:
        A, B = self.inv[a], self.inv[b]
        return (
            A.amount == B.amount and A.doc_date == B.doc_date
            and contact_match(A.customer_email, A.customer_phone, B.customer_email, B.customer_phone)
        )

    @staticmethod
    def _fungible_payments(p: RzpTxn, q: RzpTxn) -> bool:
        return (
            p.amount == q.amount and p.created_date == q.created_date
            and contact_match(p.email, p.contact, q.email, q.contact)
        )

    def _decide(self, scored: list[Scored]) -> tuple[str | None, str]:
        th = self.cfg.thresholds
        scored = sorted(scored, key=lambda c: (-c.score, c.doc))
        best = scored[0]
        if not best.identity:
            return None, "no identity evidence (amount and date alone)"
        if best.score < th.auto_link - EPS:
            return None, f"evidence {best.score:.2f} below {th.auto_link}"
        twins = [c for c in scored if abs(c.score - best.score) < EPS and self._fungible_docs(best.doc, c.doc)]
        twin_docs = {c.doc for c in twins}
        rest = [c for c in scored if c.doc not in twin_docs]
        runner = rest[0].score if rest else 0.0
        if best.score - runner < th.margin - EPS:
            return None, f"runner-up too close ({best.score:.2f} vs {runner:.2f})"
        return twins[0].doc, ("interchangeable twins, earliest taken" if len(twins) > 1 else "clear best")

    def _contested(self, p: RzpTxn, doc: str, score: float) -> str | None:
        """Another payment with an equal or better claim on the same invoice?"""
        inv = self.inv[doc]
        for qid, held in self.ref_hold.items():
            if held == doc and qid != p.entity_id:
                return qid
        for q in self.pending.values():
            if q.entity_id == p.entity_id or q.entity_id in self.ref_hold or self._fungible_payments(p, q):
                continue
            if self._days(q, doc) > self.cfg.tol.book_window_days or not self._in_band(q.amount, inv.amount):
                continue
            sq = self._score(q, doc)
            if sq.identity and sq.score >= score - EPS:
                return q.entity_id
        return None

    # ------------------------------------------------------------ recording
    def _link(self, pays: list[RzpTxn], docs: list[str], stage: str, conf: float,
              flag_amount: bool = True, **ev: Any) -> None:
        for p in pays:
            for d in docs:
                self.links.append(Link("payment_invoice", p.entity_id, d, stage, conf, dict(ev)))
                self.pay_links[p.entity_id].append(d)
            self.pending.pop(p.entity_id, None)
            self.ref_hold.pop(p.entity_id, None)
            self.outcome[p.entity_id] = stage
            self.audit.record("payment_invoice", p.entity_id, f"linked:{stage}", invoices=docs, confidence=conf, **ev)
        for d in docs:
            self.open.discard(d)
        if flag_amount and len(pays) == 1 and len(docs) == 1:
            p, inv = pays[0], self.inv[docs[0]]
            if p.amount != inv.amount:
                diff = inv.amount - p.amount
                self.exceptions.append(ReconException(
                    code="AMOUNT_MISMATCH", entity_id=p.entity_id, severity="low", amount=diff,
                    detail=(f"{p.entity_id} paid {inr(p.amount)} against {inv.doc_no} for {inr(inv.amount)} "
                            f"({inr(abs(diff))} {'short' if diff > 0 else 'over'}). Likely a discount applied after invoicing."),
                    evidence={"invoice": inv.doc_no, "paid": p.amount, "invoiced": inv.amount},
                ))

    def _by_ref(self, p: RzpTxn, doc: str, stage: str, conf: float, **ev: Any) -> None:
        inv = self.inv[doc]
        if self._in_band(p.amount, inv.amount):
            self._link([p], [doc], stage, conf, **ev)
        else:
            self.ref_hold[p.entity_id] = doc
            self.audit.record("payment_invoice", p.entity_id, "hold:amount_out_of_band",
                              invoice=doc, paid=p.amount, invoiced=inv.amount)

    def _case_candidates(self, p: RzpTxn, docs: list[str]) -> list[dict[str, Any]]:
        out = []
        for d in docs:
            sc = self._score(p, d)
            inv = self.inv[d]
            out.append({
                "id": d, "customer": inv.customer_name, "amount": inv.amount,
                "date": inv.doc_date.isoformat(), "score": sc.score, **sc.ev,
            })
        return out

    def _review(self, p: RzpTxn, reason: str, docs: list[str], resolver: dict[str, Any] | None = None) -> None:
        cands = self._case_candidates(p, docs)
        floor = self.cfg.thresholds.name_evidence
        strong = [c for c in cands if c.get("reference") or c["contact_match"] or c["name_similarity"] >= floor]
        held = {c["id"] for c in (strong or cands)}          # weak amount-only lookalikes stay open
        for c in cands:
            c["held"] = c["id"] in held
        self.reviews.append(ReviewCase(f"RV-P-{p.entity_id}", "payment_invoice", p.entity_id, reason, cands, resolver))
        self.in_review_docs |= held
        self.pending.pop(p.entity_id, None)
        self.ref_hold.pop(p.entity_id, None)
        self.outcome[p.entity_id] = "review"
        self.audit.record("payment_invoice", p.entity_id, "review", reason=reason, candidates=docs)

    def _missing(self, p: RzpTxn, detail: str | None = None, via: str = "rules") -> None:
        dangling = self.dangling.get(p.entity_id)
        if detail is None:
            if dangling:
                refs = ", ".join(str(s) for s in dangling)
                detail = f"{p.entity_id} ({inr(p.amount)}) cites invoice serial {refs}, which is not in the books."
            else:
                detail = (f"{p.entity_id} ({inr(p.amount)} on {p.created_date:%d %b}) has no open invoice "
                          f"within ±{self.cfg.tol.book_window_days} days that fits.")
        self.exceptions.append(ReconException(
            code="MISSING_INVOICE", entity_id=p.entity_id, severity="high", amount=p.amount, detail=detail,
            evidence={"dangling_serials": dangling or [], "decided_by": via, "receipt": p.order_receipt},
        ))
        self.pending.pop(p.entity_id, None)
        self.outcome[p.entity_id] = "missing_invoice"
        self.audit.record("payment_invoice", p.entity_id, "MISSING_INVOICE", decided_by=via)

    # ---------------------------------------------------------------- passes
    def _pass_exact(self) -> None:
        for p in list(self.pending.values()):
            doc = p.order_receipt
            if doc and doc in self.inv:
                if doc in self.open:
                    self._by_ref(p, doc, "exact_receipt", 1.0, reference=doc)
                else:
                    self.ref_to_closed[p.entity_id] = doc

    def _pass_refs(self) -> None:
        for p in list(self.pending.values()):
            if p.entity_id in self.ref_hold or p.entity_id in self.ref_to_closed:
                continue
            serials = self._serials(p)
            if not serials:
                continue
            known = [s for s in serials if s in self.by_serial]
            unknown = [s for s in serials if s not in self.by_serial]
            if unknown:
                self.dangling[p.entity_id] = unknown
            if not known:
                continue
            if len(known) == 1:
                docs = self.by_serial[known[0]]
                if len(docs) != 1:
                    self.audit.record("payment_invoice", p.entity_id, "ref_ambiguous", serial=known[0], docs=docs)
                    continue
                if docs[0] in self.open:
                    self._by_ref(p, docs[0], "normalized_ref", 0.95, serial=known[0])
                else:
                    self.ref_to_closed[p.entity_id] = docs[0]
                continue
            docs = [self.by_serial[s][0] for s in known if len(self.by_serial[s]) == 1]
            if (len(docs) == len(known) and all(d in self.open for d in docs)
                    and sum(self.inv[d].amount for d in docs) == p.amount):
                self._link([p], docs, "multi_ref", 0.95, serials=known)
            else:
                self.audit.record("payment_invoice", p.entity_id, "multi_ref_unresolved", serials=known)

    def _pass_scored(self) -> None:
        progress = True
        while progress:
            progress = False
            ranked = []
            for p in self.pending.values():
                if p.entity_id in self.ref_hold:
                    continue
                cands = self._candidates(p)
                if not cands:
                    continue
                scored = [self._score(p, d) for d in cands]
                doc, why = self._decide(scored)
                if doc is None:
                    continue
                best = next(c for c in scored if c.doc == doc)
                ranked.append((best.score, p.created_at, p.entity_id, doc, why, best))
            ranked.sort(key=lambda r: (-r[0], r[1], r[2]))
            for score, _, pid, doc, why, best in ranked:
                p = self.pending.get(pid)
                if p is None or doc not in self.open:
                    continue
                rival = self._contested(p, doc, score)
                if rival:
                    self.audit.record("payment_invoice", pid, "contested", invoice=doc, rival=rival)
                    continue
                self._link([p], [doc], "scored", round(score, 2), rule=why, **best.ev)
                progress = True

    def _pass_subsets(self) -> None:
        win = self.cfg.tol.book_window_days + 1
        kmax = self.cfg.thresholds.subset_max_size

        # One invoice paid in parts.
        for doc in sorted(self.open):
            inv = self.inv[doc]
            serial = receipt_serial(doc)
            rel = []
            for p in self.pending.values():
                held = self.ref_hold.get(p.entity_id)
                if held not in (None, doc):
                    continue
                if self._days(p, doc) > win or p.amount >= inv.amount:
                    continue
                by_ref = held == doc or serial in self._serials(p)
                by_contact = contact_match(p.email, p.contact, inv.customer_email, inv.customer_phone)
                if by_ref or by_contact:
                    rel.append(p)
            if len(rel) < 2:
                continue
            sols = [
                c for k in range(2, min(kmax, len(rel)) + 1)
                for c in combinations(rel, k) if sum(x.amount for x in c) == inv.amount
            ]
            if len(sols) == 1:
                self._link(list(sols[0]), [doc], "split_payment", 0.9, parts=len(sols[0]))
            elif sols:
                for p in {x.entity_id for s in sols for x in s}:
                    self.subset_ambiguous[p] = [doc]
                self.audit.record("payment_invoice", doc, "split_ambiguous", solutions=len(sols))

        # One payment covering several invoices.
        for p in list(self.pending.values()):
            if p.entity_id in self.ref_hold:
                continue
            serials = set(self._serials(p))
            rel = []
            for d in sorted(self.open):
                inv = self.inv[d]
                if self._days(p, d) > win or inv.amount >= p.amount:
                    continue
                if receipt_serial(d) in serials or contact_match(p.email, p.contact, inv.customer_email, inv.customer_phone):
                    rel.append(d)
            if len(rel) < 2:
                continue
            sols = [
                c for k in range(2, min(kmax, len(rel)) + 1)
                for c in combinations(rel, k) if sum(self.inv[d].amount for d in c) == p.amount
            ]
            if len(sols) == 1:
                self._link([p], list(sols[0]), "combined_payment", 0.9, invoice_count=len(sols[0]))
            elif sols:
                self.subset_ambiguous[p.entity_id] = sorted({d for s in sols for d in s})
                self.audit.record("payment_invoice", p.entity_id, "combined_ambiguous", solutions=len(sols))

    def _pass_holds(self) -> None:
        for pid, doc in list(self.ref_hold.items()):
            p = self.pending.get(pid)
            if p is None:
                continue
            inv = self.inv[doc]
            if doc in self.open and p.amount < inv.amount:
                short = inv.amount - p.amount
                self._link([p], [doc], "partial_payment", 0.9, flag_amount=False, reference=doc)
                self.exceptions.append(ReconException(
                    code="PARTIAL_PAYMENT", entity_id=p.entity_id, severity="medium", amount=short,
                    detail=f"{p.entity_id} paid {inr(p.amount)} of {inv.doc_no} ({inr(inv.amount)}); {inr(short)} is still due.",
                    evidence={"invoice": doc, "paid": p.amount, "invoiced": inv.amount},
                ))
            else:
                self._review(p, f"References {doc} but pays {inr(p.amount)} against {inr(inv.amount)}.",
                             [doc] if doc in self.open else [])

    def _resolver_case(self, p: RzpTxn, scored: list[Scored]) -> dict[str, Any]:
        """What the tie-breaker sees. No emails, no phone numbers."""
        return {
            "payment": {
                "id": p.entity_id,
                "amount": inr(p.amount),
                "date": p.created_date.isoformat(),
                "method": p.method,
                "reference": p.order_receipt,
                "payer_note": self._note(p) or None,
            },
            "candidates": [
                {
                    "id": c.doc,
                    "customer": self.inv[c.doc].customer_name,
                    "amount": inr(self.inv[c.doc].amount),
                    "date": self.inv[c.doc].doc_date.isoformat(),
                    "evidence": {
                        "contact_match": c.ev["contact_match"],
                        "reference_match": bool(c.ev.get("reference")),
                        "amount_difference": inr(c.ev["amount_delta"]),
                        "days_apart": c.ev["days_apart"],
                        "name_similarity": c.ev["name_similarity"],
                    },
                }
                for c in scored
            ],
        }

    def _verify(self, scored: list[Scored], dec: Decision) -> tuple[str, str]:
        th = self.cfg.thresholds
        ids = {c.doc for c in scored}
        if dec.error:
            return "review", "The tie-breaker call failed, so a person decides"
        if dec.choice == UNSURE:
            return "review", ("No model is configured" if dec.source == "offline"
                              else "The tie-breaker was unsure")
        if dec.confidence < th.resolver_confidence:
            return "review", f"The tie-breaker was only {dec.confidence:.0%} sure (needs {th.resolver_confidence:.0%})"
        if dec.choice == NONE:
            if any(c.ev["contact_match"] or c.ev.get("reference") for c in scored):
                return "review", "The tie-breaker said no invoice fits, but contact or reference evidence points at one"
            return "none", "The tie-breaker found no matching invoice"
        if dec.choice not in ids:
            return "review", f"The tie-breaker named {dec.choice!r}, which was not offered, so the answer was rejected"
        if dec.choice not in self.open:
            return "review", "The tie-breaker picked an invoice that is already linked"
        return "accept", "Verified: an offered candidate, still open, amount in band"

    def _rule_reason(self, scored: list[Scored]) -> str:
        doc, _ = self._decide(scored)
        if doc is not None:
            return "Another payment has an equal claim on the same invoice"
        named = sorted((c for c in scored if c.identity), key=lambda c: (-c.score, c.doc))
        if not named:
            return "Only the amount and date match"
        if len(named) > 1 and named[0].score - named[1].score < self.cfg.thresholds.margin - EPS:
            return "Two invoices fit about equally well"
        return "The evidence is too thin to link on its own"

    def _pass_resolver(self) -> None:
        for p in list(self.pending.values()):
            if p.entity_id in self.ref_hold:
                continue
            cands = self._candidates(p)
            if not cands:
                continue
            scored = sorted((self._score(p, d) for d in cands), key=lambda c: (-c.score, c.doc))
            if not any(c.identity or c.ev["amount_delta"] == 0 for c in scored):
                # A nearby amount with no name, contact or reference is not evidence.
                self.audit.record("payment_invoice", p.entity_id, "near_amount_only", candidates=[c.doc for c in scored])
                continue
            dec = self.resolver.decide(self._resolver_case(p, scored))
            verdict, why = self._verify(scored, dec)
            entry = {"payment": p.entity_id, "verdict": verdict, "why": why, **dec.as_dict()}
            self.resolver_log.append(entry)
            self.audit.record("tie_breaker", p.entity_id, verdict, why=why, resolver=dec.source, model=dec.model,
                              choice=dec.choice, confidence=dec.confidence, model_reason=dec.reason,
                              error=dec.error, cached=dec.cached)
            if verdict == "accept":
                best = next(c for c in scored if c.doc == dec.choice)
                self._link([p], [dec.choice], f"tie_breaker:{dec.source}", round(dec.confidence, 2),
                           model_reason=dec.reason, **best.ev)
            elif verdict == "none":
                self._missing(p, f"{p.entity_id} ({inr(p.amount)}): no candidate invoice fits. "
                                 f"Tie-breaker: {dec.reason}", via=dec.source)
            else:
                self._review(p, f"{self._rule_reason(scored)}. {why}.", [c.doc for c in scored],
                             resolver=dec.as_dict())

    def _pass_leftovers(self) -> None:
        for p in list(self.pending.values()):
            if p.entity_id in self.ref_to_closed:
                doc = self.ref_to_closed[p.entity_id]
                self._review(p, f"References {doc}, which another payment already settled.", [])
            elif p.entity_id in self.subset_ambiguous:
                self._review(p, "More than one combination of payments and invoices adds up.",
                             [d for d in self.subset_ambiguous[p.entity_id] if d in self.open])
            elif p.entity_id in self.ref_hold:
                self._review(p, "Reference found but the amount does not fit.", [])
            else:
                self._missing(p)

    # --------------------------------------------------------------- refunds
    def _match_refunds(self, refunds: list[RzpTxn]) -> None:
        used: set[str] = set()
        for r in sorted(refunds, key=lambda t: (t.created_at, t.entity_id)):
            ref = r.notes.get("credit_note")
            if isinstance(ref, str) and ref in self.cns and ref not in used:
                if self.cns[ref].amount == r.amount:
                    self._link_refund(r, ref, "cn_reference", 1.0, used)
                else:
                    self._refund_review(r, f"Refund cites {ref} but amounts differ.", [ref])
                continue
            pay = self.by_id.get(r.payment_id or "")
            invs = self.pay_links.get(r.payment_id or "", [])
            if invs:
                stage = "cn_chain"
                cands = [c for c in sorted(self.cns) if c not in used
                         and self.cns[c].amount == r.amount and self.cns[c].against_doc in invs]
            elif pay is not None:
                stage = "cn_contact"
                cands = [c for c in sorted(self.cns) if c not in used and self.cns[c].amount == r.amount
                         and abs((self.cns[c].doc_date - r.created_date).days) <= 3
                         and contact_match(pay.email, pay.contact, self.cns[c].customer_email, self.cns[c].customer_phone)]
            else:
                stage, cands = "cn_none", []
            if len(cands) > 1:
                gaps = sorted(((abs((self.cns[c].doc_date - r.created_date).days), c) for c in cands))
                if gaps[0][0] < gaps[1][0]:
                    cands = [gaps[0][1]]
            if len(cands) == 1:
                self._link_refund(r, cands[0], stage, 0.95 if stage == "cn_chain" else 0.85, used)
            elif cands:
                self._refund_review(r, "Several credit notes fit this refund.", cands)
            else:
                self.exceptions.append(ReconException(
                    code="MISSING_CREDIT_NOTE", entity_id=r.entity_id, severity="medium", amount=r.amount,
                    detail=(f"Refund {r.entity_id} of {inr(r.amount)} against {r.payment_id} has no credit note "
                            f"in the books{' for ' + ', '.join(invs) if invs else ''}."),
                    evidence={"payment_id": r.payment_id, "invoices": invs},
                ))
                self.audit.record("refund_credit_note", r.entity_id, "MISSING_CREDIT_NOTE")

    def _link_refund(self, r: RzpTxn, cn: str, stage: str, conf: float, used: set[str]) -> None:
        used.add(cn)
        self.links.append(Link("refund_credit_note", r.entity_id, cn, stage, conf, {"payment_id": r.payment_id}))
        self.audit.record("refund_credit_note", r.entity_id, f"linked:{stage}", credit_note=cn)

    def _refund_review(self, r: RzpTxn, reason: str, cns: list[str]) -> None:
        cands = [{"id": c, "amount": self.cns[c].amount, "date": self.cns[c].doc_date.isoformat(),
                  "against": self.cns[c].against_doc} for c in cns]
        self.reviews.append(ReviewCase(f"RV-R-{r.entity_id}", "refund_credit_note", r.entity_id, reason, cands))
        self.audit.record("refund_credit_note", r.entity_id, "review", reason=reason)

    # --------------------------------------------------------- open invoices
    def _open_invoices(self) -> None:
        for d in sorted(self.open):
            if d in self.in_review_docs:
                continue
            inv = self.inv[d]
            self.exceptions.append(ReconException(
                code="OPEN_INVOICE", entity_id=d, severity="low", amount=inv.amount,
                detail=f"{d} for {inr(inv.amount)} ({inv.customer_name}, {inv.doc_date:%d %b}) has no Razorpay payment in this batch.",
                evidence={"customer": inv.customer_name},
            ))

    def stage_counts(self) -> Counter:
        return Counter(self.outcome.values())
