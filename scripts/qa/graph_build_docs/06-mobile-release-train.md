# Mobile release train and feature flags

Harbor ships its iOS and Android apps on a fixed release train instead of releasing when a feature is ready. This page describes the schedule, how unfinished work stays hidden, and how a release can be stopped.

## Schedule

A new train leaves every two weeks on Monday. The branch is cut on Monday morning, QA runs Tuesday and Wednesday, and the build goes to store review on Thursday. A feature that misses the cut waits for the next train; the train never waits for a feature.

## Feature flags

Work that is not finished ships dark behind a feature flag. Flags are evaluated on the server and cached on the device for 15 minutes, so a flag can be turned off without a new build. Every flag has an owner and an expiry date, and a flag older than 90 days fails the weekly flag audit until it is removed.

## Staged rollout

A release goes to 1% of users on the first day, 10% on the second, 50% on the fourth and 100% on the seventh. The rollout pauses automatically if the crash free session rate drops below 99.5% or if the checkout error rate on mobile rises above its baseline.

## Freeze rules

The train reads the same freeze flag as the checkout API error budget policy. When checkout is frozen, builds that change checkout screens are held, while other changes may still board the train.

## Hotfixes

A hotfix leaves outside the schedule only for a crash affecting more than 0.5% of sessions, a security issue, or a payment failure. Hotfixes still pass the full QA checklist, shortened to one day.

## Cursor adoption

The invoice list switches to cursor pagination in the next train, behind the flag `invoice_list_cursor`, so the old offset client and the new cursor client can run side by side during the rollout.
