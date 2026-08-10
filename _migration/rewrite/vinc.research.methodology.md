---
doc_id: vinc.research.methodology
layer: research
domain: vinc/governance
authority: research
title: A four-axis proposal built on a measured snapshot
supersedes: vinc.plan.methodology-redesign
updated: 2026-08-07
inherits_from: [vinc.canon.graph-conventions]
tags: [research, governance, frontmatter, retrieval, chunking, domains, atlas]
---

# A four-axis proposal built on a measured snapshot

> **This is a proposal, not a plan, and it did not come from a file.** Its origin is a conversation
> artifact of 2026-08-02, an outside research report produced against the live store rather than a
> document authored in a repository. It was filed under planning at the time, which was the wrong
> shelf: nothing here was committed to, and several of its recommendations were superseded on the
> day it arrived. It belongs with the other inputs.
>
> Read it as evidence with a proposal attached. The measured numbers in section 2 are real and dated.
> The recommendations in sections 3 to 5 are arguments, and only the decisions in section 8 carry
> any weight, each of which is recorded separately as an architecture decision node.
>
> The corrections in section 1 were written when the report was loaded, by comparing it against the
> store's own records. The original body was left intact and the corrections were gathered in one
> place, which is why some later sections still read as though the corrected claims held.

## 1. Corrections applied on arrival

Three claims in the report were already out of date when it was written.

1. **Phase zero is not a new recommendation.** The report proposed triggering a re-embedding worker.
   The model switch and the re-embedding trigger were already implemented and committed, described in
   [[vinc.plan.embedding-pipeline]], and after deployment the backlog drains on its own on a
   five-minute cycle. The note reporting a hundred or so documents pending was **normal migration
   progress, not a defect**. Phase zero should therefore read as "confirm completion", not "trigger",
   and the indicator of completion is the search response naming both arms rather than full text
   alone.
2. **The 82 percent Korean truncation figure belongs to the previous model.** Measured against the
   model actually adopted, truncation of a two thousand character Korean chunk is 42.6 percent. The
   report's estimated band of 37 to 83 percent is replaced by that measurement.
3. **The token target and the chunking method were already settled.** A decision of 2026-08-01 fixed
   the target at 500 tokens and the method as **parent and child vectors**: chunks stay at two
   thousand characters and only the vectors are split into pieces under the token budget, with a
   schema version carrying a separate child vector table. So the proposal recorded as
   `adr-governance-009` should be read narrowly, as fixing the token budget of the child unit, not as
   a re-chunking programme.

After those three corrections, the report's genuinely new contribution is four things: the
frontmatter ruling, the domain layering, the conventions restructuring, and search pagination. Its
retrieval architecture section mostly restates plans that already existed.

## 2. What the store actually contained

Every value is a tool return, not an estimate. **Snapshot date 2026-08-02.**

| Item | Measured | Basis |
|---|---|---|
| documents | 103 | document listing |
| development knowledge by domain | `vinc/design` 55, `vinc/timeline` 42, `vinc/architecture` 30, `vinc/governance` 26, `vinc/infra` 26, `vinc/security` 18, `vinc/mcp` 17, `vinc/web` 16 | domain aggregate |
| learning material | about 60 documents on physics, mathematics and art | document listing |
| episodes | 42, all in the timeline domain | domain aggregate |
| concepts | about 149, a lower bound | domain aggregate |
| connected components | 7, one giant component of 139 plus 6 singletons | structural diagnosis |
| orphans | 6, all art concepts naming people or a theme | structural diagnosis |
| communities | 11 by modularity clustering, largest 20 | structural diagnosis |
| merge candidates | 20 pairs by subset and token overlap | merge candidates |
| vector state | every document flagged as needing embedding, search answering from full text only | search response metadata |
| language split | learning material in English, operational documents in Korean, split along domain lines | document fetch |

> **The domain values above are historical.** That eight-domain vocabulary has since been replaced by
> a seven-domain one, in which the timeline domain was removed as a category error, the interface
> domain was absorbed into engineering, and the web domain was dismantled. Comparing these counts
> with current ones is not meaningful.

Three tool behaviours were re-checked against a previous investigation's claimed defects.

- **Structural diagnosis works.** Components and communities both executed. The earlier claim that
  graph algorithms were unavailable is **refuted**, on the evidence of this single snapshot.
- **Search is now reproducible.** The same query twice returned byte-identical results, which
  **partly refutes** the earlier claim that result sets varied per call. But a new failure mode
  appeared: output above roughly seventy-two thousand characters is rejected in full, and a
  high-limit query hit that ceiling and returned nothing. Exhaustive enumeration is still impossible,
  but the cause moved from **silent omission to explicit refusal**, which is an improvement in kind.
- **Entities are arriving as concepts.** Six people exist as concepts with zero neighbours. The
  concept-derivation rule promoted individuals and organizations into a table meant for reusable
  ideas.

## 3. Frontmatter: parse it and promote it, do not delete it

The evidence:

- Fetching an operational document returns a body that begins with a metadata block, and that raw
  block **is returned as the first text of the first chunk** by search. So the metadata is physically
  inside the full-text index and inside the chunk text. Once vectors are on, it becomes vector noise
  as well.
- Learning material has no such block and is clean markdown that restores losslessly. The metadata is
  therefore a **localized contaminant confined to documents that came from a repository**.
- The document metadata table already holds identifier, title, content hash and update time, while
  the metadata block holds identifier, title and a last-updated date again. That is a live violation
  of the rule in [[vinc.canon.graph-conventions]] that the store writes what the store knows and a
  person writes only what the store does not, and the two copies have already diverged.

The web precedent is consistent and old. Note-taking applications promote the block into a properties
panel and hide the raw text in reading view. Static site generators parse it at build time, use it as
metadata, and remove it from the rendered body. In both cases the canonical file on disk keeps the
block, and the render layer parses it and lifts it into the interface chrome. Even under a constraint
of zero markdown rendering libraries, parsing a leading block is one regular expression, so the cost
is negligible.

## 4. Retrieval was effectively full text alone

Measured: search reported the full-text arm only, with a note saying so explicitly. The vector leg of
the rank fusion was not attached, so node hits were filled from the title and graph legs. So retrieval
at that moment was reciprocal rank fusion over lexical scoring plus title and graph proximity. The
recall of a deployment on 2026-08-01 switching to the new model with parent and child vectors
confirms this was migration state, not a defect.

Four things from the outside literature bear on it.

- **The consensus on graph-based retrieval augmentation is reserved.** Its value scales with query
  complexity, and a poorly matched architecture spends compute for a negligible gain over simpler
  methods. One vendor's hierarchical community approach reports large accuracy gains on enterprise
  benchmarks, while a 2025 preprint reports the same family underperforming plain retrieval on a
  natural-questions benchmark by a double-digit margin, and doing particularly badly on
  time-sensitive queries. For a corpus this small, running offline and without a language model, the
  case for the heavy variant is weak.
- **The lightweight variants clearly win.** A lazy variant reports indexing cost equal to plain vector
  retrieval and a tiny fraction of the heavy variant, with query cost lower by orders of magnitude.
  Another reports an indexing token count an order of magnitude smaller on a multi-hop benchmark. A
  third reports per-document costs in cents against dollars. The conclusion for Vinc is that the
  deterministic mention bipartite graph and personalized-rank ego expansion it already has should be
  used as a drill-down pattern, not replaced by a heavy pipeline.
- **The fusion constant has a documented origin.** The value of sixty comes from the original 2009
  paper and is the documented default across several search engines and databases. On a small corpus
  the lexical arm carries relatively more weight, so a period of full-text dominance before vectors
  return is not a catastrophe. After vectors return, lowering the constant to strengthen the dense
  signal is supported by precedent.
- **Chunking has to be measured in tokens.** Around five hundred tokens with ten to twenty percent
  overlap is the benchmark-validated default, and character-based chunking is dangerous for
  multilingual corpora. Vinc had already settled on five hundred tokens with parent and child
  vectors.

On how a tool should expose large results, the protocol specification recommends cursor-based
pagination, and practitioner guidance recommends a server-side ceiling, a split between summary and
detail, returning a total count and a has-more flag, and treating an invalid cursor as an explicit
error rather than a silent fallback to the first page. Vinc had the ceiling and neither of the middle
two, which is what pushes an assistant into a retry loop.

## 5. Separating domains into regions

Measured: the diagnosis already returns eleven communities with a dominant domain for each. **The
clustering backend needed for regional separation exists and works.** But the domain field is a free
string, and the learning material carried an empty one, which is what produced the six orphan
concepts. Domain fragmentation was not yet a problem and was about to become one.

Precedent, in descending order of directness:

- **Group-in-a-box** (Chaturvedi and others, 2014) lays a treemap substrate out with one rectangle per
  community sized by membership, and runs a separate force layout inside each rectangle. When
  inter-community relations are dense, a meta-layout arranges the rectangles themselves.
- **Map metaphors** (Kobourov and colleagues) draw a graph as a modified Voronoi map so that clusters
  read as countries. The important caution is that when layout and clustering are optimized
  separately the regions fragment, so they have to be optimized together.
- A hexagonal science map study reports that a map metaphor improves recall accuracy over plain
  node-link drawing.
- Commercially, one note-taking graph view colours by group, one network tool links by type, one text
  tool detects topical clusters by betweenness and reports gaps, and two general graph tools run
  modularity clustering and then partition regions.
- **Separating regions under a monochrome constraint with a single accent colour** means using
  boundaries, whitespace, a slight difference in background tone, persistent labels and density,
  rather than colour. The five-step ink density grammar already defined in
  [[vinc.spec.atlas-visual-language]] supplies that vocabulary.

## 6. Verdicts on the four prior claims

- **Tags need a three-way split: confirmed.** In the live conventions document one tag marks node and
  relation kinds that the schema fixes and cannot be changed, while another marks concept sizing and
  identifier prefixes that are exactly what should change. The meanings are inverted. They should
  become three categories: fixed by the schema, decided by this graph, and varying by subject.
  Moderate weight, low cost.
- **Scope mismatch: confirmed.** One store holds eight development domains and three learning
  subjects, while the generated section of the conventions document enumerates only the development
  domains and the learning material floats with an empty domain. The generated section measured 58
  lines against a 60-line budget, leaving two. **The measurement supports the hybrid of the three
  options**: pin "one graph, one subject" as a product recommendation, and move granularity,
  identifier and relation interpretation into the per-subject vocabulary block described in
  [[vinc.canon.vocabulary-model]], the unit fixed by `adr-engineering-011`. A pure multi-file approach
  multiplies a budget that is already full, and a pure migration leaves the human prose with nowhere
  to live. High weight, high cost.
- **Granularity does not separate concepts from entities: confirmed.** Six people exist as orphan
  concepts with no neighbours. A test asking whether a two-sentence definition can be written passes
  every entity, so reusable ideas and things in the world ended up in one table. Moderate weight, low
  cost.
- **The five node kinds are biased toward a learning journal: confirmed.** The bridge kind is only
  meaningful in a structure where concepts never link across domains directly, and the dogfooding
  graph has almost no such structure. Meanwhile learning-material people flow into the concept table
  rather than the open layer, so the labels no longer match the shape of the material. Moderate
  weight, low cost.

## 7. Proposed order, with stopping conditions

**Phase 0, prerequisite, high weight, low cost: confirm the re-embedding migration completed.** The
model switch is deployed and the worker fills a batch every few minutes. Nothing in retrieval or in
the embedding-driven layout means anything until it finishes. **Stop if** the search response does
not start naming both arms within the expected window, in which case treat it as a worker wiring
defect and investigate immediately, checking specifically whether the worker has disappeared as it
did once before.

**Phase 1, high weight, medium cost: fix the child vector token budget.** Hold child pieces under the
token budget with automatic branching by language and ten to twenty percent overlap. **Stop if**
measured truncation on Korean documents after the switch exceeds five percent, which would indicate a
token counter defect.

**Phase 2, moderate weight, low cost: parse the frontmatter and promote it to chrome.** Leave it in
the canonical chunk so restoration stays lossless and the content hash is unchanged, but split the
leading block out in the chunking pipeline, keep it as separate metadata, and exclude it from
full-text indexing, mention extraction and concept derivation. The reader shows it folded or as
property chips. **Stop if** the content hash changes, which would mean the canonical text has been
damaged; the block has to be re-inserted on restore so that verification still passes.

**Phase 3, high weight, high cost: make the conventions document subject-aware.** Revise the tags into
three categories, move granularity, identifiers and relation interpretation into the subject
vocabulary block, state "one graph, one subject" as a product recommendation, and add a rule
separating entities from concepts so that the concept table holds only reusable ideas. **Stop if**
the line budget cannot be held, in which case absorb the overflow into collapsible subject sections
rather than splitting into multiple files.

**Phase 4, high weight, high cost: the domain-separated Atlas.** Use communities as the coarsest
regions and the domain as the axis above them, giving continents at the coarsest level, clusters
below, nodes below that. In two dimensions this is group-in-a-box; in three dimensions it is a domain
attraction term added to the force model. Regions are distinguished by boundary, background tone and
persistent labels rather than colour. Cross-domain edges cross region boundaries as low-density
hairlines, reusing the existing density grammar. The data model adds domain and community identifier
fields to node context and search, and the domain partition aggregate reuses the existing plan.
**Stop when** the count of domains passes the thresholds: three to five is where a coarse map is the
right answer, from six a coupling matrix should be exposed by default, and beyond a card ceiling the
tail rolls up into a remainder entry. Where communities and domains disagree, the domain wins as the
higher axis.

## 8. What was proposed as decisions

Numbering was chosen so as not to collide with the governance decisions already recorded.

| Decision | Content | Basis |
|---|---|---|
| `adr-governance-009` | fix the child vector token budget at five hundred tokens or fewer, with automatic branching by language and ten to twenty percent overlap | store snapshot plus the benchmark standard, narrowed by correction 3 |
| `adr-governance-010` | parse the frontmatter and promote it to chrome, exclude it from indexing, mention extraction and concept derivation, and leave the canonical chunk and its verification untouched | store snapshot showing raw metadata as the first chunk text, plus web precedent |
| `adr-governance-011` | three-way tag split, migration of granularity, identifiers and relation interpretation into the subject vocabulary, "one graph, one subject" as a product recommendation, and an entity versus concept rule | store snapshot showing inverted tags, a full line budget, empty domains and orphaned entities |
| `adr-governance-012` | a domain-separated coarsest Atlas level built on community clustering, using group-in-a-box with domain attraction and monochrome boundaries | store snapshot showing eleven existing communities, plus the layout precedents |
| `adr-governance-013` | confirm the re-embedding migration and add a state badge so a user is told when retrieval is running on full text alone | store snapshot plus the operational history of a worker disappearing |
| `adr-governance-014` | cursor pagination for search with a total count and a has-more flag, replacing the hard character refusal with a summary and detail split | store snapshot showing a refused response, plus the protocol specification and practitioner guidance |

## 9. Limits

- Every number in section 2 comes from a **single store snapshot** on 2026-08-02 and is not
  corroborated by anything outside. Only the arguments in sections 3 to 5 rest on external precedent.
- The domain aggregate counts only rows with a non-null domain, so learning concepts carrying an empty
  domain may be missing from the concept total. The real count is higher than the figure given, which
  is a lower bound.
- The improvements observed in the diagnosis and search tools were confirmed **in this snapshot only**
  and can regress. This project has a documented history of exactly that.

## Provenance

| Source | Relation |
|---|---|
| conversation artifact, 2026-08-02 | the origin of this report. There is no corresponding repository file, and the graph is canonical for it |
| live store, 2026-08-02 | every figure in section 2 |
| [[vinc.plan.embedding-pipeline]] | supplies corrections 1 and 2, and owns the embedding work this report treats as a prerequisite |
| [[vinc.canon.graph-conventions]] | the rule that section 3 shows being violated, and the document section 7 proposes restructuring |
| [[vinc.canon.vocabulary-model]] | where the per-subject vocabulary this report wants to migrate into is defined |
| [[vinc.spec.atlas-visual-language]] | supplies the monochrome region vocabulary section 5 relies on |
