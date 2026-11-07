# Checkout API error budget policy

This policy sets how much failure the Harbor checkout API may spend each month and what the team does when the budget runs out. It applies to the public `/v2/checkout` endpoints and the payment confirmation webhook.

## Service level objective

The checkout API targets 99.9% successful requests over a rolling 28 day window. A request counts as failed when it returns a 5xx status or takes longer than 2 seconds at the load balancer. Client errors (4xx) do not count against the objective.

## Error budget

At 99.9% the budget is 0.1% of requests, which was about 41,000 failed requests in September at 41 million checkout calls. The budget is shared by every change that ships to the checkout service, including infrastructure changes owned by the platform team.

## Burn rate alerts

Two alerts watch the budget. A fast burn alert pages the on call engineer when the last hour consumed more than 2% of the monthly budget. A slow burn alert opens a ticket when the last six hours consumed more than 5%. Both alerts are defined in the shared alerting repository and reviewed every quarter.

## What happens when the budget is spent

When less than 10% of the budget remains, feature releases to checkout stop and only fixes, rollbacks and reliability work may ship. The freeze ends when the rolling window recovers above 25% remaining. The engineering manager for payments can grant one exception per month, recorded in the release log with a reason.

## Ownership

The payments team owns the objective and the alerts. The release train for the mobile apps reads the same freeze flag, so a checkout freeze also holds mobile builds that change the checkout screens.

## Review

The objective was raised from 99.5% to 99.9% in July after the move to the new payment gateway. The team will revisit the threshold after two quarters of data on the new gateway.
