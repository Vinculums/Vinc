---
doc_id: vinc.spec.ui-copy
layer: spec
domain: vinc/design
authority: spec
title: "UI copy: decks, voice, and the vocabulary the user sees"
supersedes: vinc.a.ui-copy
updated: 2026-08-06
inherits_from: [vinc.canon.terminology]
tags: [spec, design, copy, i18n, voice, notation]
---

# UI copy: decks, voice, and the vocabulary the user sees

> Where user-visible strings live, who owns them, which words are chosen and which are forbidden.
> Verified against `app/src/copy.ts` on 2026-08-06. Forbidden vocabulary itself is not restated
> here: [[vinc.canon.terminology]] is the single list, and the gate reads that one.

## 1. Authority, stated once so the loop closes

**The deck is canonical. This document is the rule set and the audit view.**

The predecessor of this document and the header of `copy.ts` each named the other as the source of
truth, which meant neither was. The resolution follows from what ships: the deck is compiled into
the binary and the browser bundle, the document is not, so the deck decides what a user reads. This
document decides what is allowed to be in the deck.

That has a direct consequence for how this document is written, and it is the largest change from
the version it replaces. **The full string inventory is no longer mirrored here.** The mirror was a
snapshot from 2026-07-16 and had been overtaken; by 2026-08-06 individual rows contradicted the
deck. A mirror that decays silently is worse than a pointer, because a reader trusts it. What is
kept below is the part that is stable and load-bearing: vocabulary, voice, notation, structure, and
the rules a reviewer applies.

## 2. Structure of the decks

Two decks, English and Korean, in one file, typed against one shape so a key missing from one deck
is a compile error rather than a blank on screen. A proxy resolves the active deck at access time
from the language setting, so every call site is language-unaware and a language change is a
setting plus a re-render.

Both ship in every build. `adr-design-042` chose a single artifact carrying both rather than
per-language builds; the shipping cost of a second string table is negligible next to running two
release pipelines.

Values are strings or functions. A function is used wherever a runtime value is interpolated, which
keeps the interpolation inside the deck where a translator can move it, instead of stranding a
sentence fragment in view code.

## 3. Voice

**English**, `adr-design-018`: precise, warm, craftsmanlike. Verb-first calls to action. Sanctioned
verbs: mine, map, trace, surface, bind, connect. One sentence per state.

**Korean**, `adr-design-042`: authored, not translated. The Korean deck is written as Korean product
writing, so it is allowed to differ in structure from the English line it corresponds to. Two rows
saying the same thing in two natural registers is correct; a Korean line that reads as a translation
is a defect even when it is accurate.

Errors follow a fixed three-part shape: what failed, what to do, and what is still safe. The third
part is not reassurance for its own sake, it is the answer to the question a user actually has after
a failed import, which is whether they have lost anything.

## 4. Vocabulary the user sees, against the schema names underneath

The internal field names are frozen ([[vinc.canon.terminology]]) and only the product meaning was
redefined. This table is where that redefinition becomes visible, and it is the part of the old
inventory that has not moved since it was written.

| Internal name | English surface | Korean surface | Note |
|---|---|---|---|
| record | document | `문서` | The unit of ingestion |
| domain | topic | `주제` | A free string, never a fixed list |
| corpus | data | `데이터` | Replaced `말뭉치` |
| ingest, mine | analyze, add | `분석`, `추가` | Replaced `채굴` |
| source | file | `파일` | |
| tie, bind | link | `연결` | Applies to the link-suggestion flow |
| surface | screen | `화면` | Replaced `표면` in user-facing text |
| rail | sidebar | `사이드바` | Replaced `레일` |
| concept | concept | `개념` | |
| gateway | gateway | `게이트웨이` | A node on a topic boundary |
| pin | pin | `고정` | A preference signal |
| tag | tag | `태그` | A preference signal |
| owner scope | workspace | `작업 공간` | |

Surface proper nouns stay untranslated in both decks: Intake, Records, Atlas, Search, Packs,
Settings, Vinc. They stay untranslated inside sentences too, so a Korean line reads `Atlas에서 열기`
rather than substituting a common noun. A name that changes shape inside a sentence stops being a
name.

The 2026-07-12 rewrite that produced this table replaced developer-literal wording throughout. The
before-and-after that best shows the intent: `말뭉치를 들여오세요` became `데이터 추가하기`, and
`{m}개 중 {n}개 채굴 중` became `{m}개의 문서 중 {n}개 분석 중`. The internal names in code and API
did not change, only what the screen says.

## 5. The em dash rule

**No em dash in any user-visible string.** Not in the decks, not in hardcoded strings, not in
mockups or prototypes, because a mockup is a screen. Replacements are the middle dot, parentheses, a
colon, or a line break. An empty table cell takes a middle dot, never a dash. Owner instruction,
2026-07-22, and it is also the corpus rule in [[vinc.canon.terminology]].

Measured on 2026-08-06: `copy.ts` contains 74 em dashes and **every one of them is inside a code
comment**. Zero appear in a deck value. The rule is being kept.

**It is not gated.** `scripts/gate_forbidden.py` implements the forbidden-vocabulary rules and
nothing about punctuation, so this rule is held by review alone. That is a deliberate boundary, the
same one [[vinc.canon.terminology]] draws for released payment copy: identifiers are grep-checkable,
prose quality is not. But it means the number above is a measurement of a moment, not a guarantee,
and it should be re-measured rather than assumed.

## 6. Notation: state is shape, never color

The status vocabulary is a shape language because color is reserved for focus
([[vinc.spec.atlas-visual-language]]) and because a color-only state is invisible to a
color-deficient reader.

| Mark | Meaning | Where |
|---|---|---|
| filled disc | pinned | Record row lead |
| half disc | tagged | Record row lead |
| ring | plain | Record row lead |
| `≡` | matched on the keyword arm | Search result row |
| `≈` | matched on the semantic arm | Search result row |
| `·` | meta separator | Row meta and counts throughout |
| `›` | breadcrumb separator | Atlas breadcrumb |
| `→` `←` | outbound, inbound directed degree | Atlas node detail |
| `⇣` | an imported node, once | Atlas, the session after an import |

The first three were literal glyphs and are now inline SVG icons resolved through a state helper in
`app/src/icons.ts`. The meaning is unchanged; the rendering became a shape the theme can control.

The match marks answer "why did this row match", which is the part a hybrid search result normally
hides. The numeric score stays out of the row and lives in the tooltip: a score is a debugging value
and putting it in the row invites the reader to rank by a number they cannot calibrate.

## 7. Strings that live outside the decks

Some strings are deliberately not in a deck, and the reason differs by case.

| Kind | Examples | Why it is outside |
|---|---|---|
| Untranslated proper nouns | `Settings`, `Vinc` | Section 4 |
| Enumerated code values shown raw | theme values, layout preset names | They are the values, and translating them would break the mapping between what is shown and what is stored |
| Language names | `English`, `한국어` | Each is written in its own language, so neither deck can own both |
| Notation glyphs | section 6 | They belong to the notation system, not to a language |
| Query operator examples in a placeholder | `domain:` and `tag:` prefixes | They are syntax |
| Download filename patterns | the share pack filename | A filename is an interface, not copy |
| Accessible names on dialogs | command palette, keyboard shortcuts | Fixed English, matching the platform convention for dialog roles |

The rule that separates the two lists: **if translating it would change what the software does, it
is not copy.**

## 8. Known gaps

- **The deck contains keys for screens that no longer exist.** The grid and matrix labels are still
  in both decks although those frames were removed on 2026-08-02 ([[vinc.spec.atlas-views]]). Dead
  keys are harmless at runtime and misleading to a reader auditing the deck, since they suggest a
  screen exists.
- **Documentation of the shell is behind the shell.** The keyboard help sheet as-built offers three
  surface jumps, not five; the sidebar toggle and the surface list both changed with the shell
  rework. Any inventory of shortcuts written before that rework is wrong.
- **No test asserts deck parity with the screen.** Nothing fails when a string is added to the code
  instead of the deck, or when a key is orphaned. The compile-time shape check catches a missing
  translation and nothing else.

## Provenance

| Source | Relation |
|---|---|
| `app/src/copy.ts` | The decks. Canonical for every user-visible string |
| `app/src/icons.ts` | The state mark helper behind section 6 |
| `app/src/surfaces/search.ts` | The match mark map |
| `scripts/gate_forbidden.py` | Enforces the forbidden vocabulary, and nothing about punctuation |
| [[vinc.canon.terminology]] | The forbidden lists and the notation rules for the whole corpus |
| [[vinc.spec.surface-tree]] | Which screens the copy belongs to |
