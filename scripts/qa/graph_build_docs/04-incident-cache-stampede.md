# Incident review: cache stampede after the pricing config change

On a Tuesday afternoon the checkout API returned errors for 23 minutes after a pricing configuration change. This review describes what happened, why, and what changed afterwards. It is blameless: it looks at the system, not at the person who pressed deploy.

## Impact

Between 14:02 and 14:25, 6.4% of checkout requests failed with 503 responses. The incident consumed 61% of the checkout API's monthly error budget in under half an hour, which triggered the release freeze defined in the error budget policy.

## Timeline

At 14:00 a pricing configuration change shipped that changed the cache key format for price lists. At 14:02 every cached price list missed at once and about 9,000 requests per second fell through to the pricing database. At 14:06 the fast burn alert paged the on call engineer. At 14:19 the change was rolled back, and at 14:25 the cache had refilled and errors stopped.

## Root cause

Changing the key format invalidated the whole price list cache in one moment. Nothing limited how many requests could rebuild the same entry, so thousands of identical database queries ran in parallel and exhausted the connection pool. This pattern is called a cache stampede.

## What went well

The fast burn alert fired within four minutes. The rollback was a single command because configuration changes ship through the same release tooling as code.

## Changes

Cache rebuilds now use request coalescing: one request per key rebuilds the entry while the others wait up to 500 milliseconds for it. Cache key format changes must ship behind a dual read, where the service reads the new key and falls back to the old one for one hour. The pricing database connection pool alarm now fires at 80% use instead of 95%.

## Follow ups

The payments team added a load test that empties the price list cache before running, and the release checklist gained a line for cache key changes.
