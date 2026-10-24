# Design System (DESIGN.md)

> Design SSoT read by both humans and AI (LLMs). Standard section structure per methodology §5.
> The token source of truth is `design-tokens.json`; `tokens.css` is a generated artifact (`gen_tokens.py`).

## 1. Principles

<!-- 3-5 design principles — decision-making criteria. -->

1. Clarity first — prioritize conveying information over decoration.
2. Consistency — the same solution for the same problem (tokens only, no hardcoding).
3. Accessibility by default — WCAG 2.2 AA.

## 2. Tokens

<!-- Color/typography/spacing token table — definition of values (explicit values and rationale). -->

| Token | Value(primitive) | Usage |
|---|---|---|
| `--background` | {grain_100} (#F2F2F0) | _Describe usage_ |
| `--foreground` | {ink_900} (#17171A) | _Describe usage_ |
| `--card` | {grain_50} (#FBFBFA) | _Describe usage_ |
| `--card-foreground` | {ink_900} (#17171A) | _Describe usage_ |
| `--muted` | {grain_200} (#E9E9E6) | _Describe usage_ |
| `--muted-foreground` | {ink_600} (#55565E) | _Describe usage_ |
| `--caption-foreground` | {ink_400} (#82838D) | _Describe usage_ |
| `--border` | {hairline} (#DEDEDA) | _Describe usage_ |
| `--input` | {hairline} (#DEDEDA) | _Describe usage_ |
| `--ring` | {indigo_600} (#4A4F8C) | Focus ring and selection; aliases `--point` (dark {peri_400} #8F96D9) |
| `--point` | {indigo_600} (#4A4F8C) | The one point colour: focus, selection, active item, graph focus node and its edges, and the fill of a primary button (one per screen). Never a large surface or decoration (dark {peri_400} #8F96D9) |
| `--point-text` | {indigo_600} (#4A4F8C) | Links and at most one emphasised phrase per heading or card, class `.pt` (dark {peri_200} #B9BEF0, Lc 69) |
| `--primary` | {ink_900} (#17171A) | _Describe usage_ |
| `--primary-foreground` | {grain_100} (#F2F2F0) | _Describe usage_ |
| `--accent` | {grain_200} (#E9E9E6) | _Describe usage_ |
| `--accent-foreground` | {ink_900} (#17171A) | _Describe usage_ |
| `--link` | {indigo_600} (#4A4F8C) | Text links; aliases `--point-text` (dark {peri_200} #B9BEF0) |
| `--info` | {ink_600} (#55565E) | _Describe usage_ |
| `--info-strong` | {ink_900} (#17171A) | _Describe usage_ |
| `--info-foreground` | {grain_100} (#F2F2F0) | _Describe usage_ |
| `--success` | {ok_700} (#2F6B4E) | _Describe usage_ |
| `--success-foreground` | {grain_100} (#F2F2F0) | _Describe usage_ |
| `--warning` | {warn_bg} (#E5DCC3) | _Describe usage_ |
| `--warning-foreground` | {ink_900} (#17171A) | _Describe usage_ |
| `--danger` | {danger_700} (#A03A3A) | _Describe usage_ |
| `--danger-foreground` | {grain_100} (#F2F2F0) | _Describe usage_ |

## 3. Components

<!-- Component list and usage — what to use when (variant/size/state/accessibility). -->

## 4. Patterns

<!-- Composition patterns and layout — screen composition rules. -->

## 5. Accessibility

<!-- Contrast/keyboard/aria rules — WCAG 2.2 AA compliance criteria. -->

| Text token | Contrast (on bg) | Verdict |
|---|---|---|
| foreground on background | 16.0 | ✓ |
| muted-foreground on background | 6.5 | ✓ |
| primary on background | 10.4 | ✓ |
| info-strong on background | 6.7 | ✓ |
| success on background | 5.6 | ✓ |
| danger on background | 5.9 | ✓ |
| info on background | 6.7 | ✓ |
| primary-foreground on primary | 10.4 | ✓ |
| success-foreground on success | 5.6 | ✓ |
| danger-foreground on danger | 5.9 | ✓ |
| info-foreground on info | 6.7 | ✓ |
| warning-foreground on warning | 13.1 | ✓ |
| accent-foreground on accent | 14.7 | ✓ |
| card-foreground on card | 17.3 | ✓ |

- Body text ≥4.5:1, large (≥18.66px bold/24px) ≥3:1. Focus indicators required.
- Do not convey state by color alone (pair with icon/text).

## 6. Do / Don't

<!-- Recommended vs. forbidden examples — preventing misuse (tokens vs. hardcoding). -->

- ✅ Use tokens: `hsl(var(--primary))` / `var(--space-4)`
- ❌ Hardcoded: `#BA3D04` / `16px`

## 7. Voice & Tone

<!-- UX writing rules — text consistency. -->

## 8. Governance

<!-- semver/contribution/adoption metrics — the operating model. -->
