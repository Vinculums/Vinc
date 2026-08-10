# The Vinc logo

**Final, 2026-07-19, made by the owner.** From a reference photograph (a black inverted
triangle over grain) that the owner vectorized and edited: a **two-stroke calligraphic V**,
a long sweep on the left including the curve at the bottom vertex, and a short blade on the
right. There is no connecting line across the top and it is not a closed triangle. Each
stroke carries a **tip indigo gradient**: the upper 62% is ink, and indigo `#32355C` seeps in
faintly towards the lower end.

## Files

| File | Use |
|---|---|
| `vinc-mark.svg` | master (light), ink `#161618` to tip indigo `#32355C` gradient |
| `vinc-mark-dark.svg` | for dark backgrounds, ivory `#EAE5DD` to tip periwinkle `#BDBFE3` |
| `vinc-mark-mono.svg` | single colour `currentColor`, for inline UI, the favicon and small sizes where a gradient means nothing |

The viewBox is tight to the art bounding box (`1.9 0 66.4 63`). The gradients are vertical in
objectBoundingBox per stroke, applied the same way as the Figma original.

## Specification

- **Clear space**: at least 25% of the mark's width on every side.
- **Small sizes (24px and under)**: the strokes are thin and the gradient does not read, so
  use `vinc-mark-mono.svg`. For the favicon, check a real-size render and add a
  stroke-reinforced variant if it turns out to be needed.
- **Colour**: only the three defined variants, light, dark and mono. No arbitrary colours, no
  vivid primaries, no strong gradients.
- **Forbidden**: rotation, distorting the aspect ratio, adding an outline, restoring the top
  connecting line, moving the strokes apart.

## Wordmark

Undecided. Add the mark plus "Vinc" lockup outline here once the brand font is settled.

## Source of truth

- **Canonical in Figma**: file `WUXLeDDNBqih2KTZOz9wCX`, the `Homepage (Design system)` page,
  the `Logo` frame (78:164, the owner's final), the `Logo / Mark` component (91:164) and
  `Logo · dark sample` (91:169).
- These SVGs are the Figma vector paths extracted as they are. Change the shape in Figma and
  re-extract here.
