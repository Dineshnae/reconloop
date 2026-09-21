# Benchmark results

Seeds 301-310 (10 batches per difficulty), generated fresh and never inspected while building.

Command: `python -m reconloop bench --seeds 301-310 --difficulty standard,hard --tie-breaker offline,greedy,oracle --out bench`

| Batch | Tie-breaker | Settlement to bank (P / R) | Payment to invoice (P / R) | Refund to credit note (P / R) | Wrong links (all batches) | Exceptions (P / R) | Reviews per batch | Error cost per 100 records |
|---|---|---|---|---|---|---|---|---|
| standard | offline | 100.0% / 100.0% | 100.0% / 97.9% | 100.0% / 100.0% | 0 | 100.0% / 80.6% | 5.2 | 5.43 |
| standard | greedy | 100.0% / 100.0% | 98.1% / 99.0% | 100.0% / 100.0% | 33 | 91.5% / 87.0% | 0.0 | 7.48 |
| standard | oracle | 100.0% / 100.0% | 100.0% / 100.0% | 100.0% / 100.0% | 0 | 100.0% / 100.0% | 0.6 | 0.15 |
| hard | offline | 100.0% / 100.0% | 100.0% / 96.8% | 100.0% / 100.0% | 0 | 100.0% / 79.8% | 8.8 | 8.39 |
| hard | greedy | 100.0% / 100.0% | 96.9% / 98.5% | 100.0% / 100.0% | 57 | 91.4% / 85.4% | 0.0 | 11.96 |
| hard | oracle | 100.0% / 100.0% | 100.0% / 100.0% | 100.0% / 100.0% | 0 | 100.0% / 100.0% | 1.0 | 0.23 |

P = precision, R = recall, both averaged over batches. Error cost: 5 per wrong link, 3 per missed exception, 1 per false exception, 1 per item sent to review.

Tie-breakers:

- **offline**: rules only; unclear cases go to a person
- **greedy**: baseline that always takes the top rule candidate (no abstaining)
- **oracle**: reads the answer key; the ceiling a perfect tie-breaker could reach

## Payment outcomes by scenario: standard, offline

| Scenario | Payments | Correct | Sent to review | Wrong | Missed |
|---|---|---|---|---|---|
| Receipt is the invoice number | 928 | 928 | 0 | 0 | 0 |
| Receipt in another format | 227 | 227 | 0 | 0 | 0 |
| Invoice number only in notes | 120 | 120 | 0 | 0 | 0 |
| No reference, payer contact matches | 205 | 204 | 1 | 0 | 0 |
| One invoice paid in parts | 60 | 60 | 0 | 0 | 0 |
| One payment for several invoices | 20 | 20 | 0 | 0 | 0 |
| Same customer, same amount, same day | 20 | 20 | 0 | 0 | 0 |
| First capture of a double charge | 20 | 20 | 0 | 0 | 0 |
| Second capture of a double charge | 20 | 20 | 0 | 0 | 0 |
| Paid amount differs from invoice | 30 | 30 | 0 | 0 | 0 |
| Invoice never raised | 30 | 30 | 0 | 0 | 0 |
| Stranger's payment, same amount as an open invoice | 10 | 0 | 10 | 0 | 0 |
| Payer note names the customer | 19 | 19 | 0 | 0 | 0 |
| Payer note needs reading (nickname, on behalf of) | 35 | 0 | 35 | 0 | 0 |
| Nobody can tell (should go to a human) | 6 | 6 | 0 | 0 | 0 |

Mean runtime per batch: 0.013 s for about 400 records. Undecidable cases correctly sent to a person: 6 of 6.

## Payment outcomes by scenario: standard, greedy

| Scenario | Payments | Correct | Sent to review | Wrong | Missed |
|---|---|---|---|---|---|
| Receipt is the invoice number | 928 | 928 | 0 | 0 | 0 |
| Receipt in another format | 227 | 227 | 0 | 0 | 0 |
| Invoice number only in notes | 120 | 120 | 0 | 0 | 0 |
| No reference, payer contact matches | 205 | 205 | 0 | 0 | 0 |
| One invoice paid in parts | 60 | 60 | 0 | 0 | 0 |
| One payment for several invoices | 20 | 20 | 0 | 0 | 0 |
| Same customer, same amount, same day | 20 | 20 | 0 | 0 | 0 |
| First capture of a double charge | 20 | 20 | 0 | 0 | 0 |
| Second capture of a double charge | 20 | 20 | 0 | 0 | 0 |
| Paid amount differs from invoice | 30 | 30 | 0 | 0 | 0 |
| Invoice never raised | 30 | 30 | 0 | 0 | 0 |
| Stranger's payment, same amount as an open invoice | 10 | 0 | 0 | 10 | 0 |
| Payer note names the customer | 19 | 19 | 0 | 0 | 0 |
| Payer note needs reading (nickname, on behalf of) | 35 | 18 | 0 | 17 | 0 |
| Nobody can tell (should go to a human) | 6 | 0 | 0 | 6 | 0 |

Mean runtime per batch: 0.012 s for about 400 records. Undecidable cases correctly sent to a person: 0 of 6.

## Payment outcomes by scenario: standard, oracle

| Scenario | Payments | Correct | Sent to review | Wrong | Missed |
|---|---|---|---|---|---|
| Receipt is the invoice number | 928 | 928 | 0 | 0 | 0 |
| Receipt in another format | 227 | 227 | 0 | 0 | 0 |
| Invoice number only in notes | 120 | 120 | 0 | 0 | 0 |
| No reference, payer contact matches | 205 | 205 | 0 | 0 | 0 |
| One invoice paid in parts | 60 | 60 | 0 | 0 | 0 |
| One payment for several invoices | 20 | 20 | 0 | 0 | 0 |
| Same customer, same amount, same day | 20 | 20 | 0 | 0 | 0 |
| First capture of a double charge | 20 | 20 | 0 | 0 | 0 |
| Second capture of a double charge | 20 | 20 | 0 | 0 | 0 |
| Paid amount differs from invoice | 30 | 30 | 0 | 0 | 0 |
| Invoice never raised | 30 | 30 | 0 | 0 | 0 |
| Stranger's payment, same amount as an open invoice | 10 | 10 | 0 | 0 | 0 |
| Payer note names the customer | 19 | 19 | 0 | 0 | 0 |
| Payer note needs reading (nickname, on behalf of) | 35 | 35 | 0 | 0 | 0 |
| Nobody can tell (should go to a human) | 6 | 6 | 0 | 0 | 0 |

Mean runtime per batch: 0.012 s for about 400 records. Undecidable cases correctly sent to a person: 6 of 6.

## Payment outcomes by scenario: hard, offline

| Scenario | Payments | Correct | Sent to review | Wrong | Missed |
|---|---|---|---|---|---|
| Receipt is the invoice number | 597 | 597 | 0 | 0 | 0 |
| Receipt in another format | 334 | 334 | 0 | 0 | 0 |
| Invoice number only in notes | 207 | 207 | 0 | 0 | 0 |
| No reference, payer contact matches | 332 | 332 | 0 | 0 | 0 |
| One invoice paid in parts | 80 | 80 | 0 | 0 | 0 |
| One payment for several invoices | 30 | 30 | 0 | 0 | 0 |
| Same customer, same amount, same day | 40 | 40 | 0 | 0 | 0 |
| First capture of a double charge | 30 | 30 | 0 | 0 | 0 |
| Second capture of a double charge | 30 | 30 | 0 | 0 | 0 |
| Paid amount differs from invoice | 40 | 40 | 0 | 0 | 0 |
| Invoice never raised | 40 | 40 | 0 | 0 | 0 |
| Stranger's payment, same amount as an open invoice | 20 | 0 | 20 | 0 | 0 |
| Payer note names the customer | 32 | 32 | 0 | 0 | 0 |
| Payer note needs reading (nickname, on behalf of) | 58 | 0 | 58 | 0 | 0 |
| Nobody can tell (should go to a human) | 10 | 10 | 0 | 0 | 0 |

Mean runtime per batch: 0.024 s for about 437 records. Undecidable cases correctly sent to a person: 10 of 10.

## Payment outcomes by scenario: hard, greedy

| Scenario | Payments | Correct | Sent to review | Wrong | Missed |
|---|---|---|---|---|---|
| Receipt is the invoice number | 597 | 597 | 0 | 0 | 0 |
| Receipt in another format | 334 | 334 | 0 | 0 | 0 |
| Invoice number only in notes | 207 | 207 | 0 | 0 | 0 |
| No reference, payer contact matches | 332 | 332 | 0 | 0 | 0 |
| One invoice paid in parts | 80 | 80 | 0 | 0 | 0 |
| One payment for several invoices | 30 | 30 | 0 | 0 | 0 |
| Same customer, same amount, same day | 40 | 40 | 0 | 0 | 0 |
| First capture of a double charge | 30 | 30 | 0 | 0 | 0 |
| Second capture of a double charge | 30 | 30 | 0 | 0 | 0 |
| Paid amount differs from invoice | 40 | 40 | 0 | 0 | 0 |
| Invoice never raised | 40 | 40 | 0 | 0 | 0 |
| Stranger's payment, same amount as an open invoice | 20 | 0 | 0 | 20 | 0 |
| Payer note names the customer | 32 | 32 | 0 | 0 | 0 |
| Payer note needs reading (nickname, on behalf of) | 58 | 31 | 0 | 27 | 0 |
| Nobody can tell (should go to a human) | 10 | 0 | 0 | 10 | 0 |

Mean runtime per batch: 0.022 s for about 437 records. Undecidable cases correctly sent to a person: 0 of 10.

## Payment outcomes by scenario: hard, oracle

| Scenario | Payments | Correct | Sent to review | Wrong | Missed |
|---|---|---|---|---|---|
| Receipt is the invoice number | 597 | 597 | 0 | 0 | 0 |
| Receipt in another format | 334 | 334 | 0 | 0 | 0 |
| Invoice number only in notes | 207 | 207 | 0 | 0 | 0 |
| No reference, payer contact matches | 332 | 332 | 0 | 0 | 0 |
| One invoice paid in parts | 80 | 80 | 0 | 0 | 0 |
| One payment for several invoices | 30 | 30 | 0 | 0 | 0 |
| Same customer, same amount, same day | 40 | 40 | 0 | 0 | 0 |
| First capture of a double charge | 30 | 30 | 0 | 0 | 0 |
| Second capture of a double charge | 30 | 30 | 0 | 0 | 0 |
| Paid amount differs from invoice | 40 | 40 | 0 | 0 | 0 |
| Invoice never raised | 40 | 40 | 0 | 0 | 0 |
| Stranger's payment, same amount as an open invoice | 20 | 20 | 0 | 0 | 0 |
| Payer note names the customer | 32 | 32 | 0 | 0 | 0 |
| Payer note needs reading (nickname, on behalf of) | 58 | 58 | 0 | 0 | 0 |
| Nobody can tell (should go to a human) | 10 | 10 | 0 | 0 | 0 |

Mean runtime per batch: 0.022 s for about 437 records. Undecidable cases correctly sent to a person: 10 of 10.

