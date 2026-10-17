# Decision: cursor pagination for the invoice list

The invoice list in the Harbor web app and the public `/v2/invoices` endpoint move from offset pagination to cursor pagination. This document records the problem, the options and the decision.

## Problem

Offset pagination (`?page=40&per_page=50`) slowed down as accounts grew. For the largest account, with 1.2 million invoices, page 400 took 3.8 seconds because the database scanned and discarded every earlier row. Offset pages also skipped or repeated invoices when new invoices arrived while a user paged.

## Options considered

Keep offsets and add a cap at page 100. This was rejected because support teams page deep into history to answer billing questions. Keyset pagination on `(created_at, id)` with an opaque cursor was the second option. A search index for the list was the third, rejected because it adds a second source of truth for a list that must match the ledger exactly.

## Decision

The list uses keyset pagination on `(created_at, id)`, returned as an opaque, base64 encoded cursor. Clients pass `next_cursor` back unchanged and never build cursors themselves. Page size stays at 50 by default with a maximum of 200.

## Consequences

The deepest page for the largest account now loads in 90 milliseconds. The web app can no longer jump to an arbitrary page number, so the page number control was replaced by "load more" and a date filter. The public API keeps offset pagination on `/v1/invoices` until its sunset in March, with a deprecation header on every response.

## Index

The change needs a composite index on `invoices (account_id, created_at, id)`. It was created concurrently during a low traffic window, following the same online migration rules as the payments currency migration.

## Related work

Invoice search keeps its own ranking and pagination; the search tuning notes cover that path. Mobile clients adopt the cursor in the next release train.
