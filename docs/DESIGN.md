# Design notes

The short version is in the README. This file explains the numbers behind the decisions.

## 1. Payment to invoice evidence

Each open invoice within ±2 days whose amount is within 10% of the payment gets a score. All weights live in `reconloop/config.py`.

| Evidence | Weight | Counts as identity? |
|---|---|---|
| Invoice serial found in the receipt or note | 0.60 | yes |
| Payer email or phone equals the customer's | 0.50 | yes |
| Payer note names the customer | 0.50 x name similarity (only if similarity >= 0.6) | yes |
| Amount exactly equal | 0.30 | no |
| Amount within 10% | 0.10 | no |
| Same day / next day | 0.10 / 0.05 | no |

A payment links on its own only if all of these hold:

- the best candidate has identity evidence,
- its score is at least 0.80,
- it beats the runner-up by at least 0.15, unless the runner-up is an interchangeable twin (same customer contact, amount and day),
- no other open payment has an equal or better identity-backed claim on the same invoice, and no reference-backed partial payment is waiting on it.

Worked examples from the generated data:

| Payment | Best candidate | Runner-up | Outcome |
|---|---|---|---|
| No note, payer phone matches | 0.50 + 0.30 + 0.10 = 0.90 | stranger with same amount: 0.40 | linked |
| Note "S. Priya - bedsheet order" | Priya Sundaram: 0.30 + 0.10 + 0.50 x 0.9 = 0.85 | Deepa Nair: 0.40 | linked |
| Note "for Gurpreet ji's order" | Gurpreet Kaur Sandhu: 0.30 + 0.10 + 0.50 x 0.6 = 0.70 | Preeti Sandhu: 0.40 | below 0.80, tie-breaker |
| Note "Kiran from Brightpath, office order" | Brightpath Interiors LLP: 0.70 | Kiran Kumar: 0.70 | tie, tie-breaker |
| Stranger, no note, same amount as an open invoice | 0.40, no identity | none | never linked; tie-breaker may say NONE |
| Stranger, no note, amount within 10% of an invoice | 0.20, no identity | none | missing invoice, nobody asked |

The last row is deliberate: a nearby amount with nothing else is not evidence, so it does not cost reviewer or model time.

## 2. Name similarity

`normalize.name_similarity` returns one of four values:

| Value | Meaning | Example |
|---|---|---|
| 1.0 | every name token present | "Priya Sundaram" |
| 0.9 | full tokens plus matching initials | "S. Priya" for Priya Sundaram |
| 0.6 | at least one full token | "Kiran" for Kiran Kumar, "Brightpath" for Brightpath Interiors LLP |
| 0.0 | nothing, or initials only | "Venky" for Venkatesh Iyer |

Honorifics and legal suffixes are dropped, a possessive 's is stripped so "mother's" does not produce an initial S, and a few abbreviations are expanded (Mohd to Mohammed). Near spellings count when difflib's ratio is at least 0.88 and both words have four or more letters. That is why Sneha and Snehal score the same, and why ties like that go to the tie-breaker instead of being decided by string distance.

## 3. Settlement to bank

Only credits whose narration or reference mentions Razorpay are candidates, which is what keeps an IMPS credit for the same amount out of the picture.

1. Full UTR in the narration or reference column.
2. A UTR fragment of 8+ characters. If it fits exactly one settlement and the credit lands within T+0 to T+3, it links even when the amount differs (and the difference is flagged). If several settlements share it, as a month prefix does, it also needs an exact amount.
3. Exact amount inside T+0 to T+3. Ties are broken with the bank lag learned from steps 1 and 2 (the most common lag), and only mutual best pairs link. Anything still tied goes to review.
4. Leftover settlements are missing if the statement runs at least three days past them, otherwise in transit. Leftover Razorpay-looking credits are unmatched.

## 4. What the grader rewards

The scorer (`reconloop/score.py`) is written to make guessing unprofitable:

- A link on a case marked undecidable counts as wrong, even if the guess happens to be right.
- Interchangeable invoices are scored as one answer, so the agent is not punished for picking either.
- Review items are never "wrong", but each costs 1 unit.
- Error cost: wrong link 5, missed exception 3, false exception 1, review item 1.
- Invoices tied to an undecidable case are not expected to be flagged as open.
- In-transit settlements are informational and not scored.

On the reported held-out seeds, the greedy baseline costs more than sending everything unclear to a person: 7.48 against 5.43 per 100 records on standard batches, 11.96 against 8.39 on hard ones. On a handful of seeds it can come out cheaper, because some of its guesses land. Its wrong links are still wrong, which is why the wrong-link count is reported on its own and gated in CI rather than folded into the cost.

## 5. The tie-breaker contract

The model receives a case like [`example_tie_breaker_case.json`](example_tie_breaker_case.json) and must answer through a forced tool call whose `choice` field is an enum of the offered invoice ids plus `NONE` and `UNSURE`.

| Model answer | What the agent does |
|---|---|
| An offered id, confidence >= 0.75, invoice still open | Links it, stage `tie_breaker:claude`, reason kept in the audit log |
| `NONE`, confidence >= 0.75, no contact or reference evidence on any candidate | Flags a missing invoice |
| `NONE` while contact or reference evidence exists | Review |
| `UNSURE`, low confidence, an id it was not offered, an invoice already taken | Review |
| API error, timeout, or no tool call | Review, error recorded; not cached, so a rerun retries |

The prompt describes the kinds of evidence to read (people paying for relatives or employers, nicknames, initials, near-miss spellings) without naming any case from the generator.

## 6. Actions

Every exception maps to one proposed action in `reconloop/actions.py`. Actions that move money (refunding a double capture) or post to the ledger (raising an invoice, issuing a credit note, posting an adjustment) carry `requires_approval`, a `max_amount_paise` ceiling equal to the amount in question, and an idempotency key derived from the exception, so a rerun proposes the same action rather than a second one. `approve()` records an approver and nothing else.

## 7. What to build next

- A hidden trap set that is not in the repo, so the benchmark cannot be tuned against.
- Claude results on the same held-out seeds, then per-trap accuracy for the model.
- Cross-check settlement totals against the Settlements API instead of recomputing them from rows.
- Run the recon report connector against a real test-mode account.
- A review screen where a person picks the match and approves actions, feeding decisions back as labelled data.
- GSTR-2B reconciliation of Razorpay's GST invoices against the `tax` column.
