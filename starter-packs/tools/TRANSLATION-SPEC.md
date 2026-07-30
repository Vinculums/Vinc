# Translation spec (ko → en) for starter-pack bodies

Shared contract for all translation agents. Deviations break the converter.

## Input / output
- Input: source notes listed in `translation-batches.json` (absolute `path` per item).
- Output: `D:\Projects\AIProjects\vinc-project\starter-packs\work\{domain}\{stem}.md`
  — **same stem as the source file** (keep the Korean filename exactly), UTF-8.
- Output contains **body only** — NO YAML frontmatter. First line = `# {title_en}`
  where `title_en` comes from `tools/glossary.json` (key = `{domain}/{stem}`).

## Hard rules
1. `## 한 줄 핵심` → **exactly** `## One-line essence` (the converter extracts the
   one-liner from this heading). Its content = one translated sentence.
2. Translate the ENTIRE body — no omission, no summarizing, no added commentary.
   Every section, table row, list item, and caption must survive.
3. Preserve structure verbatim: heading levels/order, numbered-circle headings
   (`## ① …` keeps `①`), `#### A → B (Tx)` influence headings (translate names to
   their English forms, keep `→` and `(Tx)` codes), blockquotes, hrules.
4. LaTeX (`$…$`, `$$…$$`) and code blocks: keep math/code untouched; translate
   only Korean prose around or inside table cells.
5. Wikilinks: `[[…]]` → replace with the **plain English title** (no brackets,
   no path). Look up the target in `glossary.json` (match by file stem, ignoring
   `../` path parts); if absent, use a natural English rendering.
   `[[a|display]]` → translate the display text.
6. Terminology: `glossary.json` `title_en` values are BINDING for note titles and
   whenever the concept is mentioned. Standard technical terms use their
   canonical English form (no romanized Korean).
7. Result must contain no Hangul. Hanja/kana inside proper names (北斎) may stay.
8. Tone: precise, textbook-register English. Keep the author's argumentative
   structure; don't flatten rhetorical sections (e.g. "세 장면" scene devices —
   translate them faithfully).

## Verification (each agent, before finishing)
- Re-read each output file: first line is `# {title_en}`; `## One-line essence`
  present; no `[[` remains; no Hangul (regex `[가-힣]`); LaTeX `$` count matches
  source ±0.
- Report per file: stem, output path, line count vs source line count (must be
  within ±15%), any glossary misses.
