# Color tokens for the Harbor web app

Harbor's interface colors come from two layers of design tokens. Components read only semantic tokens; semantic tokens point at primitive tokens. This note explains the naming and the rules a designer or engineer follows when adding a color.

## Primitive tokens

Primitive tokens name a raw value by hue and step, for example `blue-600` or `gray-100`. There are 11 steps per hue, from 50 to 950. Primitives never appear in component code, which lets the palette change without touching components.

## Semantic tokens

Semantic tokens name a purpose, such as `surface-raised`, `text-muted` or `border-danger`. Each semantic token maps to one primitive in the light theme and one in the dark theme. Components use semantic tokens only, and the lint rule `no-primitive-color` blocks a primitive in a component file.

## Contrast rule

Every text token must reach a contrast ratio of at least 4.5:1 against the surfaces it is allowed on, and 3:1 for large text and icons. The contrast check runs in CI on every change to the token file and fails the build below those ratios. The accessibility review of the settings form found two muted text tokens at 4.1:1, which led to this rule being enforced in CI instead of by eye.

## Adding a color

A new color starts as a semantic token with a written purpose. If no existing primitive fits, the designer proposes a primitive in the same pull request and the design systems engineer reviews both. One purpose, one token: two tokens that always resolve to the same value are merged.

## Dark theme

The dark theme is not an inversion. Raised surfaces get lighter in dark mode, so `surface-raised` maps to `gray-800` on a `gray-900` page. Shadows are replaced by borders in dark mode because shadows disappear against dark surfaces.

## Where tokens live

The token file is the single source of the values, and the generated CSS variables and the mobile theme are built from it. Nobody edits the generated files by hand; the build overwrites them.
