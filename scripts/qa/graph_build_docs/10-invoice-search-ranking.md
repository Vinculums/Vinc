# Tuning invoice search ranking

Harbor's invoice search lets users find an invoice by customer name, invoice number, amount or line item text. These notes record how ranking was tuned after support reported that exact invoice numbers often did not come first.

## Baseline

Search ran a single full text query across all fields with equal weight. On a set of 300 real support queries, the right invoice was the first result 58% of the time and in the top five 79% of the time.

## Exact matches first

Queries that look like an invoice number (the `INV-` prefix followed by digits) now run an exact lookup before the full text query, and an exact hit is always first. This alone raised the first result rate to 71%.

## Field weights

Customer name now weighs three times as much as line item text, and invoice number twice as much. Weights were chosen by a grid search on half of the query set and checked on the other half, so the result is not tuned to the queries it was measured on. After weighting, the first result rate reached 83% on the held out half.

## Recency

Among results with similar scores, newer invoices rank higher, decaying with a half life of 90 days. Support asked for this because most questions concern the last billing cycle. Recency never lifts a weak match above a strong one; it only breaks near ties.

## Pagination

Search results keep score based pagination with a page size of 20, separate from the cursor pagination of the invoice list, because search order depends on the query and cannot be expressed as a stable key.

## What did not work

Adding synonyms for common customer name variations raised recall but pushed exact matches down, so the synonym list was removed. Stemming line item text helped slightly and was kept.

## Next steps

The team will add the 300 query set to CI as a regression check that fails if the first result rate drops by more than two points.
