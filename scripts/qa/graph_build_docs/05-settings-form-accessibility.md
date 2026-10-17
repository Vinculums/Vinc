# Accessibility review of the account settings form

The account settings form lets a Harbor user change their name, email, password, invoice defaults and notification choices. This review tested it with a keyboard, with a screen reader and against the contrast rules in the color token guide.

## Method

Two reviewers used the form with keyboard only, then with NVDA on Windows and VoiceOver on macOS. Automated checks ran with the axe ruleset. Every finding below was reproduced by both reviewers.

## Focus order

Tab order jumped from the email field to the save button, skipping the password section, because the password section was rendered later and positioned with CSS. The fix renders the sections in visual order. After the fix the focus order matches the reading order on every breakpoint.

## Labels and errors

Four inputs used placeholder text as their only label, so screen readers announced them as "edit text". Each input now has a visible label tied to it with `for`. Validation errors appeared only as red borders; they now appear as text under the field and are announced through an `aria-live` region when the user submits.

## Contrast

Two muted text tokens measured 4.1:1 against the raised surface, below the 4.5:1 rule. The design systems engineer moved both tokens one step darker, and the contrast check in CI now enforces the ratio for every text token.

## Dialogs

The "delete account" confirmation dialog did not trap focus, so keyboard users could tab behind it. It now uses the shared dialog component, which traps focus, closes on Escape and returns focus to the button that opened it.

## Result

The form went from 14 automated violations to 0, and both manual passes completed every task without a mouse. The frontend code review guidelines now ask reviewers to tab through any form a pull request changes.
