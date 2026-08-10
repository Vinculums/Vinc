---
doc_id: vinc.canon.graph-conventions
layer: canon
domain: vinc/governance
authority: canon
title: GRAPH.md, the assistant writing conventions contract
supersedes: vinc.c.graph-md
updated: 2026-08-06
inherits_from: [vinc.canon.chunk-ssot, vinc.canon.mcp-contract]
tags: [canon, governance, conventions, graph-md, validation]
---

# GRAPH.md, the assistant writing conventions contract

> One page of markdown an assistant reads **before** it writes to the graph. It does for knowledge
> structuring what a coding conventions file does for code. `adr-governance-001` adopted it and this
> document is the specification.

## 1. Why it exists

The symptom is that the same material looks different depending on which assistant loaded it. The
cause is not taste. It is a **missing channel**.

- Of the nineteen MCP tools, **none reads the vocabulary.** Reading ratified types is HTTP only
- So an assistant learns the vocabulary only **after writing**, from `proposed_types` in the response
- Ratify, reject, and rename are deliberately not exposed over MCP, and that asymmetry stays

So GRAPH.md is not a convenience document. It fills **the one read channel that was empty.**
A document costs less than another tool, so the document came first. The tool came later anyway,
in section 6, and both now exist.

## 2. Form

| # | Rule | Enforced by |
|---|---|---|
| F1 | One file, two zones: prose a person writes, and a generated block the store writes. Comment markers are the boundary | validator rule 3 |
| F2 | Warn above 60 lines, fail above 100 | validator rule 8 |
| F3 | Three mutability tags: `[schema-fixed]`, `[this graph decides]`, `[varies by topic]`. **Splitting the file is forbidden** | review |
| F4 | Imperative, second person. The reader is an assistant | review |
| F5 | Follow product copy rules. No em dash. Use a middle dot, parentheses, or a colon | validator rule 9 |

**Why F3 changed.** The previous two tags marked **origin**, which read backwards against what the
reader actually needs to know, which is **mutability**. Schema-enforced node and edge kinds carried
the product tag and were therefore misread as "this belongs to the product, so I may redefine it."
The three new tags answer the real question directly: can I change this in this file? The validator
accepts both old and new vocabulary so existing user files keep working, while the template emits
only the new tags.

**Why F5 applies here.** This template is embedded verbatim as a code block on the documentation
site. It follows **product copy rules**, not internal document rules.

## 3. Sections

| Section | Zone | Holds | What breaks without it |
|---|---|---|---|
| header | generated | spec version, build time, authority declaration | no baseline for drift detection |
| What this is | prose | the status of this file, and who judges meaning | the assistant mistakes it for a schema |
| Read before you write | prose | search first, reuse ids, id prefix convention | duplicate concepts multiply |
| Node kinds | prose | the five kinds and what happens to open types | the same material gets different labels per host |
| Granularity | prose | two positive and three negative examples, plus the two-sentence test | concepts bloat or shatter |
| Relations | prose | the wire types, what direction means, which type is undirected | reversed direction collapses the prerequisite graph |
| Documents | prose | use the document path, do not paste source text into a record | the "no source" defect returns |
| Domains in this graph | generated | actual domains and notation | domains fragment |
| Ratified vocabulary | generated | ratified node and edge types | proposals pile up unreviewed |
| Do not | prose | three prohibitions, including no self-ratification | the ratification asymmetry erodes |

Ten sections, of which **a person touches seven, about 35 lines**.

> **Granularity is given as examples, not as a rule, on purpose.** Sixty years of note-taking
> practice tried to make "one idea per note" precise and ended up back at individual judgment.
> Written as a rule it is not followed. Written as examples it is.

## 4. The template

**English is the source and Korean is a line-for-line port.** Generated values come from the same
source regardless of language.

**The text is owned by code**, the template constants in `api/graphmd.py`. The code block on the
documentation site is a copy and must be changed with it. Measured: of 58 lines, only two differ,
and both are example values in the generated zone. The page shows a filled example, the code emits
an empty graph. All prose matches.

**Measured at 60 lines, and the budget has zero slack.** That is exactly the warning threshold, and
a test pins English and Korean to the same line count.

⇒ **To add a line you must delete one.** This is not a figure of speech. The line that would tell
an assistant to read `content_time` is absent for this reason, compounded by the asymmetry in
section 6.4.

## 5. The generated zone

| Generated line | Source |
|---|---|
| header build time | the generator. The spec version is a constant |
| domain list | the domain census: domain, episodes, concepts, records, total |
| ratified node and edge types | the theme store, filtered to ratified status |

> **The status string is `authored`, not "ratified".** Ratification is the transition from proposed
> to authored. Getting this name wrong makes the ratified vocabulary render as a permanently empty
> list, and nothing else fails, so the mistake survives.

- Top twelve, then "and N more". A long list eats the line budget
- **The generator runs on the sidecar and in the app.** Vocabulary reads are HTTP only, so an
  assistant cannot build this, and must not be allowed to
- **Boundary: the generator writes those three things only.** Node definitions, granularity
  examples, and the prohibition list are not generated. External research suggests
  auto-generated context files can lower task success, and this boundary absorbs that risk
- **Empty graph:** with zero domains, write one sentence rather than an empty list. An empty list
  reads as "domains are forbidden"

## 6. Where it lives

Four placements were considered. Two shipped, one is the current default, one is deferred.

| Option | Code cost | Human step needed | Follows the account graph | Verdict |
|---|---|---|---|---|
| a. a file in the user's repo | none | "go read it" | no | phase 1 |
| b. a document in the store | small | "go fetch it" | yes | phase 2 |
| c. an MCP resource | medium | varies by host | yes | **deferred** |
| d. a read tool | small | **none** | yes | phase 3, current |

Option c is deferred for three independently sufficient reasons: the server does not declare a
resources capability, resources are application-controlled so a model cannot fetch one on its own
initiative, and extending the initialize response has caused a real connector outage before.

### 6.1 The tool

```
vinc_conventions   read only, the nineteenth tool
  in : {owner, lang, target}
  out: {text, source: "doc" | "default", doc_id, updated_at, missing}
```

If the store holds a conventions document it returns that. If not it returns **the default template
with the generated zone filled in**, so a user who has done nothing still receives the live domains
and vocabulary.

Registration for a new route happens in **four places**: the sidecar route table, the hub proxy
allowlist, the account allowlist, and the dev-server proxy pattern. Miss the last and only that
request receives the HTML shell with a 200, so the app misdiagnoses it as "the sidecar is down".
Miss either middle one and that screen alone dies quietly with a 404.

One sentence is appended to the ingest tool description:
`Call vinc_conventions once before your first write in a session.`
Tool descriptions are model-controlled and always shipped in the tool listing. **That sentence is
the only structural defense against the "nobody reads it" failure mode.**

### 6.2 Two mines cleared before storing it as a document

| Mine | Fix |
|---|---|
| Local and account sync overwrite each other's conventions | Exclude the conventions id from document export. **Each graph owns its own conventions** is the invariant |
| Fragments of the conventions document leak into search and derivation | Mark the origin first, then exclude from search once measured |

### 6.3 A known asymmetry

The HTTP document write accepts `content_time`. The MCP document write **does not pass it**. The
document list **returns it**.

⇒ The asymmetry is not "unknown" but **"readable, not writable."** Whether to expose it is settled
by measurement, not by argument.

## 7. Authority is per section

| Zone | Authority | Editor | If they disagree |
|---|---|---|---|
| generated | the store | generator only | validator rule 4 fails, regenerate |
| prose | this file | a person | the store has no counterpart, so there is nothing to compare |

One sentence: **what the store knows, the store writes. Only what the store does not know is
written by a person.**

## 8. Validation

Shape and referential integrity only. **No meaning validation**, which would violate the engine
purity principle.

| # | Rule | Level |
|---|---|---|
| 1 | header marker and spec version parse | block |
| 2 | required sections present and ordered | block |
| 3 | generated block markers paired and ordered | block |
| 4 | generated zone matches the current store census | block |
| 5 | relation names within the wire types plus ratified vocabulary | block |
| 6 | domain entries exist in real data | fix |
| 7 | tool names in the body exist in the registry | fix |
| 8 | length: fix above 60, block above 100 | mixed |
| 9 | forbidden glyphs, em dash included | fix |

```
vinc graph-md build --owner O [--out FILE | --file FILE | --doc] [--lang en|ko]
vinc graph-md check --owner O [--file FILE | --doc] [--no-store]
```

- `--file` updates **in place**: it swaps the generated zone and restamps the header. **It does not
  touch prose.** A build that erased a person's examples would be run exactly once
- A file with broken markers is **returned as an error, not replaced.** The tool does not guess
- Exit codes match the existing gates: 0 pass, 1 fix-only, 2 block
- **Language detection** uses section titles. When neither language matches, it degrades to a single
  ordering fix rather than dumping nine blocks: shipping two languages is our limit, not the user's
  mistake for translating the file

### 8.1 Rule 5 must accept seven relation types, not six

**This is a required change, not a note.**

The published wire contract lists six relation types. The store has been running a **seventh** for
some time: the attachment edge from a record to a concept, eighty-eight of them, carrying a role.
It never appeared in the contract, so rule 5 would block any conventions file that names it.

Attachment is not association. A record is a fact hanging off a concept, not a peer of it.
Flattening it into the generic association type would erase that, so the type is being **formalized
rather than removed**. The seven are:

| Type | Direction and use |
|---|---|
| `relates_to` | loose association, undirected. The reverse folds into the same edge |
| `prerequisite_of` | A comes before B |
| `derives_from` | revision, replacement, lineage |
| `inherits_from` | mirrors document frontmatter inheritance |
| `connects_via` | bridge to concept, role required |
| `about` | episode to concept |
| **`attaches_to`** | **record to concept. Newly formalized** |

Rule 5, the ingest tool schema, and the Relations section of the template all move from six to
seven together. There is no data migration: the edges already exist and already behave this way.

> This is the same failure as the two Episode fields that the store persists but the schema never
> declared. A contract that is narrower than the system does not constrain the system. It only
> misleads the reader. See [[vinc.canon.mcp-contract]].

## 9. How it appears in the app

### 9.1 Stored as a document, but it must not look like one

Leaving it as an ordinary document causes four things at once, each with code behind it.

| What happens | Why it is bad |
|---|---|
| It joins the document list and, having no concept links, sits in the "no topic" pile | a management file lands among the user's real material |
| The document editor offers concept extraction candidates | headings such as "Node kinds" appear as candidate concepts |
| One click merges those headings into the graph as concepts | **the conventions file pollutes the map.** The largest risk |
| Sync makes local and account conventions overwrite each other | the account graph starts asserting the local domain list |

The third is decisive. Extraction is a user click rather than an automatic step, so nothing breaks
today. **But the interface invites the click.** For this document the extraction box should not
appear at all.

### 9.2 It is not drawn on the map

Three reasons:

1. **It is none of the five kinds.** Forcing it in would itself be an example of violating the conventions
2. **It would be an orphan point.** An unconnected point already carries a meaning on this map,
   "a concept that has not found its place yet", and this file would switch that signal on falsely
3. **It would be conventions pretending to be data.** This file is *about* the graph, not part of
   it. Drawing it on the map is drawing the legend inside the map

### 9.3 Where it lives instead

One icon in the top bar opens a sheet with two zones: the conventions editor above, and the
ratification queue below. The icon carries a **badge** with the number of pending proposals.

The first draft put this in settings, which is where frequency alone would argue for. It was
reversed: what is not visible is not created, and what is not created dies of the first failure
mode. **Discoverability beats frequency.**

> **That reversal is conditional.** The top bar follows a rule that every visible handle must do
> something, and it is already crowded. **Without the badge this icon does not earn its place**,
> because a button pressed once a month cannot justify permanent pixels. The badge decision and the
> placement decision stand or fall together.

Both zones sit on one screen because prevention and correction belong together. Split across two
screens, the user meets "I wrote the conventions, so why do proposals still accumulate" in two
different places.

The editor is split into three parts, prose before, the generated block, and prose after, so that
read-only is **structural rather than a CSS imitation**. Regenerate **saves first**, otherwise the
server reads the old prose and the sentence just written appears to vanish.

> **The badge counts only what an assistant actually proposed.** Creating a theme seeds the built-in
> vocabulary as proposals too, so counting everything shows a badge of eleven on first run. Asking
> the user to adjudicate eight things they never proposed is the moment the badge stops being a signal.

> **The ratification zone is desktop only.** The theme vocabulary has no owner dimension, so the
> account proxy deliberately blocks those routes. On the web the conventions zone is visible and
> the approval step says to use the desktop app.

### 9.4 What the map should show is the backlog, not the conventions

A measurement surfaced here. **There was no ratification interface in the app.** The routes existed
on the sidecar, and nothing in the app source called them.

⇒ The principle that "the assistant proposes and **a person ratifies**" was, at that moment, a
promise with **no place for the person to stand**. Proposals only accumulated.

That is why the conventions file and the ratification screen are one pair: prevention and
correction. The badge is the whole surface, and it belongs to the shell rather than to the map,
because the state stays true after you leave the map.

### 9.5 Packs

The conventions document is **not packed**, by default. Someone else's conventions asserting your
domain list makes that document false on arrival. If a real demand appears, it reopens as an
explicit attachment that is not applied.

### 9.6 Copy

- No em dash, including inside the template code block
- **Terminology differs per surface, as observed.** The app says "graph" and the documentation site
  says "ontology". Follow the current term of each surface and do not mix them. Unifying them is a
  separate matter
- The feature is called "conventions" on screen. "Rules" implies enforcement this design refuses,
  and "settings" already means something else

## 10. State

Phases one through three and the surfacing work all shipped on 2026-08-01, with the full test suite
passing at each step and a verified deployment across all four surfaces.

| Decision | State |
|---|---|
| `adr-governance-001` adopt | settled |
| `adr-governance-002` placement | settled |
| `adr-governance-003` per-section authority | settled |
| `adr-governance-004` validator | settled |
| `adr-governance-005` documentation site | revised, promoted to an independent section |
| `adr-governance-007` exclude from search and listing | done |
| `adr-governance-008` ratification surface | done |
| `adr-governance-011` mutability tags | done |
| `content_time` exposure | awaiting measurement |
| four-week metric review | pending |

> `governance-006` does not exist. The number is left as a gap rather than closed by pulling later
> decisions down, because a citation that silently retargets is worse than a hole.
