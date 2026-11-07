# Migration plan: adding currency to the payments table

Harbor starts accepting payments in currencies other than the account's home currency. The `payments` table needs a non null `currency` column. The table holds 310 million rows and takes writes every second, so the change has to happen online without locking it.

## Constraints

The migration must not hold an exclusive lock for more than one second, must not slow checkout writes by more than 5%, and must be reversible at every step until the last one. Any step that would lock the table is split into smaller steps.

## Steps

First, add `currency` as a nullable column with no default; this only changes metadata and is instant. Second, deploy application code that writes `currency` on every new payment. Third, backfill existing rows in batches of 10,000, setting `currency` from the account's home currency, with a pause of 200 milliseconds between batches. Fourth, add a check constraint `currency IS NOT NULL` as not valid, then validate it separately, which scans without blocking writes. Fifth, set the column to not null, which uses the validated constraint and does not rescan.

## Backfill monitoring

The backfill job reports rows done, rows left and replication lag. It stops itself if replication lag passes 30 seconds and resumes when lag falls under 5 seconds. At the planned pace the backfill takes about 9 hours.

## Rollback

Until step five, rollback means deploying the previous application code and dropping the column. After step five the column stays, and rollback is a code change only.

## Ownership and review

The payments team runs the migration and the platform team reviews each step before it runs on production. The same online rules were used to create the composite index for invoice list pagination.

## Why not a new table

Copying payments into a new table with the column already present was rejected: it doubles storage during the copy and needs a cutover window that checkout cannot afford under its error budget.
