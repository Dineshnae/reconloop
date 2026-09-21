# Build log

What broke while building ReconLoop, how it showed up, and what changed. In the order it happened.

## Seed protocol

| Seeds | Role |
|---|---|
| 7 (standard), 8 (hard), 101 (standard), 102 (hard) | Development. Every rule and threshold was tuned while looking at these. |
| 201-210 | First held-out run. It crashed once and then exposed a mislabelled plant (items 13 and 14). Both were generator bugs. |
| 301-310 | Reported numbers. Generated for the first time after those fixes. |

No matching rule, threshold or weight changed after the first held-out run. Its table is at the bottom for comparison.

## What broke

**1. The note parser invented an invoice.**
"Order for 2 bedsheets" came back as a reference to invoice 2. The optional prefix pattern `[A-Za-z]{2,4}[/\-_ ]?` accepted "for " as a prefix. The prefix must now end in `/`, `-` or `_`. Caught by a spot check; now a parametrized test.

**2. Floats in the generator's money math.**
Split amounts and fee overcharges were computed with floats and passed to a `Decimal` rounding helper, which failed. Both now use integer paise. Caught in review, before the first run.

**3. A planted GST error also broke the fee base.**
The first version changed the GST line and recomputed the fee in a way that shifted the base, so every tax error would also have been flagged as a fee error. The base now stays correct and only GST is wrong. Caught in review before it produced data.

**4. The malformed bank row was planted by luck.**
It was only planted if a randomly chosen noise line happened to be a vendor debit, which roughly 7% of hard seeds would not have had. The first noise line is now always the corrupted one. Caught in review.

**5. A unique UTR fragment linked regardless of date.**
A fragment that fits one settlement is strong evidence, but not strong enough to ignore a credit that arrives weeks later. It now has to land inside the T+0 to T+3 window. Caught in review.

**6. Keyword collision in the audit log.**
Combined-payment links passed `invoices=` to the audit logger twice and raised `TypeError` on the first hard batch that had a combined payment. Renamed to `invoice_count`.

**7. Review holds hid open invoices.**
With the oracle on dev seed 102, two open invoices were never flagged. The undecidable "Rahul D." case had held three lookalike invoices that shared only a nearby amount (score 0.1), and anything held for review is not reported as open. Now, when any candidate carries identity evidence (reference, contact or name), only those are held.

**8. The generator leaked the answer.**
Every true invoice was created just before its decoy, so "take the lower invoice number" would have won every name trap. This surfaced while writing the greedy baseline, which would have looked far better than it should. Creation order is now shuffled, and a test checks that the true invoice comes first in between 20% and 80% of traps.

**9. Review time spent on non-evidence.**
On dev seed 102, 2 of 11 reviews were payments whose only link to any invoice was an amount within 10%, with no name, contact or reference. Those are now flagged as missing invoices without asking anyone. Reviews on that seed went from 11 to 9, with no other change.

**10. Review reasons said nothing.**
Every offline review read "tie-breaker could not decide", which only became obvious in the rendered report. Reasons now say what the rules saw ("Two invoices fit about equally well", "Only the amount and date match") and then what the tie-breaker did.

**11. The report broke in an older rendering engine.**
`clamp()` was ignored, so the headline rendered at body size, and the CSS grid legend collapsed into one line. The headline has a fixed size declared before `clamp()`, the legend is a table, and IDs no longer wrap mid-string.

**12. A test that could never fail.**
The first version of the invoice-order test compared a serial number to that serial plus one. It now finds the actual decoy (same amount and date, adjacent serial) before comparing.

**13. The first held-out run crashed.**
On hard seed 208, a ₹4,149 refund landed on a day with only three small payments, so that day's settlement came out at minus ₹753 and the generator stopped. Debits that outweigh a day's collections now carry into the next settlement, the way a negative balance would. The change draws no random numbers, so every other seed produced byte-identical files. A regression test generates seed 208.

**14. The same run exposed a mislabelled plant.**
On hard seed 206, even the oracle scored one false and one missed exception. A planted "discount after invoicing" of ₹150 on a ₹1,299 payment is 10.35% of the ₹1,449 invoice, just outside the 10% band. The agent called it a partial payment, which is exactly what its rules say, while the answer key said amount mismatch.

The cause was an earlier fix for this very problem: it assumed the price list from index 15 started at ₹1,599. It starts at ₹1,299. The planted discount is now clamped to the band without changing the random stream, and every development and held-out seed was checked. Because results on 201-210 had now been seen, the reported benchmark moved to fresh seeds 301-310.

## First held-out run (seeds 201-210)

After fix 13, before fix 14. Same agent code as the reported run.

| Batch | Tie-breaker | Settlement to bank (P / R) | Payment to invoice (P / R) | Refund to credit note (P / R) | Wrong links | Exceptions (P / R) | Reviews per batch | Error cost per 100 records |
|---|---|---|---|---|---|---|---|---|
| standard | offline | 100.0% / 100.0% | 100.0% / 98.1% | 100.0% / 100.0% | 0 | 100.0% / 83.1% | 4.8 | 4.80 |
| standard | greedy | 100.0% / 100.0% | 98.1% / 99.0% | 100.0% / 100.0% | 33 | 91.5% / 87.0% | 0.0 | 7.47 |
| standard | oracle | 100.0% / 100.0% | 100.0% / 100.0% | 100.0% / 100.0% | 0 | 100.0% / 100.0% | 0.6 | 0.15 |
| hard | offline | 100.0% / 100.0% | 100.0% / 96.9% | 100.0% / 100.0% | 0 | 99.7% / 79.6% | 8.5 | 8.42 |
| hard | greedy | 100.0% / 100.0% | 97.5% / 99.0% | 100.0% / 100.0% | 47 | 93.5% / 87.2% | 0.0 | 10.06 |
| hard | oracle | 100.0% / 100.0% | 100.0% / 100.0% | 100.0% / 100.0% | 0 | 99.8% / 99.8% | 0.9 | 0.30 |

The 99.7% and 99.8% exception precision on hard batches is item 14.
