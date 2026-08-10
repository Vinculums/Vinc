---
doc_id: vinc.research.no-llm-chat
layer: research
domain: vinc/ai
authority: research
title: Whether a conversational surface can work without a language model
supersedes: vinc.d.chat-research
updated: 2026-08-07
inherits_from: [vinc.research.view-redesign, vinc.spec.surface-search, vinc.canon.mcp-contract, vinc.canon.chunk-ssot]
tags: [research, ai, ask, intent, retrieval, korean, no-llm]
---

# Whether a conversational surface can work without a language model

> **Research input, not a settled conclusion.** This answers a question asked on 2026-08-02: can a
> chat-like surface be added to Vinc without a language model. The evidence is a trace of the live
> code, direct queries against the hosted graph, and the prior deliberation that had deferred the
> feature. Adoption happens through a decision, not through this document. The decision that came
> out of it is `adr-ai-073`.
>
> The rejection recorded in [[vinc.research.view-redesign]] parked this feature as a product
> question. [[vinc.spec.surface-search]] is the surface it eventually landed in, and
> [[vinc.canon.mcp-contract]] is where the claim that the intelligence lives outside Vinc is stated.
>
> Measurements below are dated 2026-08-02 unless marked otherwise. Section 9 records what has
> changed since, re-verified against the code on 2026-08-07.

## 1. The finding in four lines

1. **It is possible.** But the missing layer is not one layer, it is three, and the most urgent of
   them is not intent recognition. It is answer material.
2. **The two fields nominated as the answer body are empty.** Edge evidence was present on 1 of 36
   sampled relations. The concept one-liner is at zero percent because the write path never sets
   that column.
3. **The material that does exist is dense, and the read contract does not return it.** Concept
   `props.note` and the summary cards on Record nodes are exactly the sentences a person would want
   quoted, and the node context call projected neither. Episode summaries were the only material
   that was both populated and returned.
4. **So the shortest path was not building a parser.** It was widening the projection of one backend
   function by two columns. Only after that does an intent layer have anything to answer with.

## 2. The premise, re-verified

### 2.1 The contract exists

Both read primitives were already implemented and wired end to end when the question was asked:
`node_context` and `edge_context` in `api/query.py`, their sidecar routes in `api/server.py`, the
hub proxy allowlist in `vinc_mcp/http_server.py`, the account proxy in `account/src/routes/graph.ts`,
and the front-end client in `app/src/api.ts`. All three proxy registrations were in place, so the
web parity trap described in [[vinc.spec.web-system]] did not apply.

The excerpt fallback was designed as well. When the mention back-reference is empty the call degrades
to a title full-text search, and when the extension is unavailable it returns an empty excerpt marked
as an absent arm rather than an error. The discipline that a detail panel still stands up without an
excerpt was already in the code.

**These two primitives are not MCP tools.** The tool list carries neither `vinc_node_context` nor
`vinc_edge_context`, which makes them a graphical and web surface only. That matters because the
earlier rejection of this feature rested on the claim that MCP was already that path. An MCP host
could not, and still cannot, ask for the source text behind a concept.

### 2.2 The contract carries nothing

Having a contract and having the contract return something are different claims. Six kinds of answer
material were measured directly against the hosted graph.

| Material | Returned by the read contract | Actually populated |
|---|---|---|
| Concept one-liner | yes | **0 percent**, the write path has no such column |
| Concept `props.note` | **no** | high, most sampled concepts carry one |
| Record summary card | **no**, title only | high, tagged as fact or pitfall |
| Episode summary | yes | 100 percent, 6 of 6 sampled |
| Chunk excerpt | yes | present, truncated at 240 characters |
| Edge evidence | yes | **2.8 percent**, 1 of 36 |

The pattern is unpleasantly clean. **The read contract returns the two empty fields and withholds
the two full ones.** Episodes satisfy both sides by accident.

So the claim that most of the machinery already existed was true against the contract and false
against the data. Attaching a conversation to it in that state would have produced a system that
honestly repeated "no recorded basis" to most questions.

## 3. The three measurements

### 3.1 Edge evidence, 1 in 36

Two seeds at depth one returned 44 edges. The query builder omits the key entirely when the value is
empty, so the presence of the key is the presence of the data.

| Relation kind | Count | Carrying evidence | In scope for edge context |
|---|---|---|---|
| Concept to concept, four types | 28 | 1 | yes |
| Episode about concept | 8 | 0 | yes |
| Bridge connects | 3 | not applicable | **no** |
| Record attaches to | 5 | not applicable | **no** |
| Total | 44 | 1 | 36 |

Two things surface at once. The population rate on the relations that edge context can address is
2.8 percent, so "why does A lead to B" comes back empty roughly ninety-seven times in a hundred. And
eighteen percent of the sampled edges are outside the whitelist of edge types entirely, which means
the question cannot even be posed about them. Record attachment is the relation that connects a
concept to the facts recorded about it, which makes the highest-value relation the one that sits
outside the contract.

> The sample was two seeds at depth one. A full count is a single aggregate query and should be run
> as a gate before any of this is built, section 8 phase 0. If the true rate turns out to be twenty
> percent rather than three, the conclusion does not change.

### 3.2 The one-liner column has no writer

The node context call returns the concept's own description from a `one_liner` column. Tracing
backwards for whoever fills that column found nobody on the fragment path. The live fragment ingest
sets title, domain, tags, props, creation time and owner, plus date and summary for episodes. There
is no one-liner among them, and the dormant fragment engine's settable set is narrower still.

Fragment ingest is the only write surface an MCP host has. So every concept written by the dogfooding
loop had an empty one-liner, and node context returned an empty description for all of them. Nothing
errored, which is why it stayed hidden. It is the same species of silence as semantic search having
been write-only for months.

### 3.3 The material is in `props.note`

What the concepts actually carry looks like this: a concept for the Atlas surface holds a sentence
naming its three zoom levels and its four viewpoints; a record about an issuer outage holds a
sentence saying that without the variable the whole authorization layer switches off while the health
endpoint keeps answering two hundred.

**Those are the answers.** One or two sentences written by a person, quoted rather than generated,
which is exactly the condition under which a system with no language model can speak at all. Records
tagged as fact or pitfall are answer cards that were already written.

The node context return contract did not include props, and the link list carried records as
identifier, title and domain only, dropping the note that is the whole content. Search already reads
the field, since a query can match inside props. **The material could be found and could not be
read back.**

### 3.4 Semantic search was off on both surfaces

The desktop lacks the embedding stack by design. On 2026-08-02 the hosted side was also answering
with the full-text arm alone, because the responses carried a note that an embedding migration was
in progress. That is a deliberate rule: while old and new vectors would be mixed, answering from
full text alone is more honest than answering from a skewed index.

The bad reading is that a conversation built now runs on keyword matching on both surfaces. The good
reading is that **the asymmetry between desktop and web was zero at that moment**, so a design that
stands up on full-text search alone would improve by itself when the migration finished and would
not break on the desktop. That ordering is the strongest argument for the recommended option.

## 4. Three missing layers, in the order that matters

| Layer | Question | State | Difficulty |
|---|---|---|---|
| A. Answer material | does a read actually return a sentence to quote | **missing**, sections 3.2 and 3.3 | low, a column projection |
| B. Entity resolution | noun phrase in a question to a node identifier | partly present, section 5.3 | medium |
| C. Intent mapping | sentence shape to which primitive to call | **absent** | medium |

Building C first produces a system that understands the question and has nothing to say, which reads
as a verdict against no-model conversation rather than as a missing projection. Building A first
improves the existing detail panel immediately, which means A is recovered whether or not the
conversation is ever adopted.

## 5. Designing the intent layer

### 5.1 Three grades of "no language model"

| Grade | Method | External key | Desktop | Offline |
|---|---|---|---|---|
| G1 deterministic | regular expressions, keywords, operator grammar | none | yes | yes |
| G2 statistical | embedding similarity for intent classification, no generation | none | only if the model is bundled | yes |
| G3 small generative | an on-device small model writes sentences | none | no | not applicable |

**G3 is out of scope.** It keeps the offline-first invariant stated in [[vinc.canon.principles]], but
it breaks the product's own placement: Vinc does not write sentences, the intelligence sits outside
it. The moment generation moves inside, the upstream authoring role comes back with it.

G1 is the base and G2 is an augmentation. Because G2 depends on a desktop bundling decision, the
design has to stand on G1 alone, otherwise the desktop and the web start giving different answers.

### 5.2 Answerable intents, derived from the read primitives

The intents were not imagined. They were read backwards out of the functions that already exist,
so each row is a question shape some function can already answer.

| # | Intent | Question shape | Call | Material | Worked on 2026-08-02 |
|---|---|---|---|---|---|
| I1 | definition | what is X | node context | props note, then excerpt | after layer A |
| I2 | basis | where is X written | node context excerpts | chunk excerpt and document title | yes |
| I3 | relation | how are X and Y related | shortest path | chain of identifiers and titles | yes |
| I4 | reason | why does X lead to Y | edge context | evidence | **no**, 2.8 percent |
| I5 | neighbours | what is linked to X | node context links | relation list | yes |
| I6 | facts | what to watch out for with X | record attachments | record card | after layer A |
| I7 | history | when did X change | episode about | episode summary | yes |
| I8 | period | what happened last week | date range and recall | titles and summaries | yes |
| I9 | inventory | what subjects exist | domains | domains and counts | yes |
| I10 | diagnosis | what connections are missing | suggest ties and diagnose | candidate list | yes |
| I11 | exploration | everything else | search | current search results | yes |

Eight of the eleven worked unchanged. Two opened with the layer A fix. **The one that does not open
is I4**, and it is the one nominated as the answer body in the original question.

I11 doubles as the safety net. When intent classification fails the system does not invent, it hands
back search results. That converts "I did not understand" into "here is what I found".

### 5.3 Entity resolution is search, not new code

There is no reason to build layer B. The search function already does it. It always populates node
hits, falling back to a scan of titles, summaries and props even when the chunk index is empty, and
it fuses the mention neighbours of the top chunks through reciprocal rank fusion. Ranking a sentence
into candidate node identifiers is already implemented, already tuned, and already runs on both
surfaces.

So entity resolution is the first node hit of a search over the question text, and it costs no new
code. The reuse doubles as an honesty device: when several candidates tie, do not choose, ask. A
system with no model has no way to paper over ambiguity, and that is an opportunity to ask an
explicit question rather than a defect.

Intent classification stays equally thin. Rather than parsing the sentence, it looks only for signal
words: conjunction words plus a relation noun for I3, a why word for I4, a when word for I7, a
period expression for I8, a list word for I9, a gap word for I10, and everything else falls to I11.
**A failed match is the normal path.** The grammar does not need to be complete, which is precisely
how this avoids the coverage race that kills rule-based chatbots.

### 5.4 The Korean particle problem

Attaching Korean questions to the graph requires stripping grammatical particles. A full search of
the code base found no morphological handling at all outside third-party packages, and both retrieval
arms leak because of it.

- The full-text index is created without a stemmer or tokenizer argument, so it splits on whitespace
  and Korean enters as whole eojeol. A noun carrying a particle does not match the bare noun.
- The node fallback path tokenizes on whitespace and does substring containment. **Only one direction
  survives**: a query token has to be a substring of the stored string, so a bare noun matches a
  longer stored phrase, while the same noun carrying a particle matches nothing.

One particle therefore kills both arms at once. The vector arm would be robust to this, and it was
absent from the desktop and switched off on the web.

**A dictionary is enough, and a morphological analyzer is not needed.** Strip candidate suffixes off
the query token and widen to multiple candidates, keeping the original: a token carrying a particle
expands into itself plus its stem. Thirty to forty high-frequency particles cover practical use.
Because this is expansion rather than deletion, a false strip is harmless: the original stays in the
candidate set, so matching never gets worse. It is a pure function of roughly thirty lines with no
backend dependency, so the desktop and the web behave identically.

**This is not chat-only work.** Typing a noun with a particle into the existing search box returned
nothing. It is the second investment that is recovered regardless of whether the conversation ships.

## 6. Three implementation options

### Option 1, repair the material and add a thin router, recommended

The order is answer material, then particle expansion, then the router, and each step pays for
itself alone.

| Step | Work | Backend | App |
|---|---|---|---|
| A | project props and record notes into node context | two functions, local | one line in the detail panel |
| B | particle suffix expansion | pure function in the query layer | none |
| C | intent router and answer assembly | none | new ask module |

No new dependencies of any kind, no new backend routes, no new proxy registrations, because the
existing node context contract carries everything. The surface belongs inside Search as a second
mode rather than as a fourth top-level surface. The offline invariant holds and the desktop and web
answers stay identical, because only G1 is used.

### Option 2, add the two MCP tools, a separate matter

The goal is a conversation inside the app, not shipping data out to an external model, so this is
not what the research was asked for. It is kept because it fixes the same gap from the other side:
adding the node and edge context tools would let an MCP host fetch the source text behind a concept,
which it currently cannot. It would also make the earlier rejection reason in
[[vinc.research.view-redesign]] true, since that rejection assumed MCP was already this path.

### Option 3, bundle embeddings on the desktop and route intents by similarity

Ship a quantized small embedding model with the desktop so both the vector arm and similarity-based
intent classification turn on.

| Comparison | Option 1 | Option 2 | Option 3 |
|---|---|---|---|
| Application size increase | none | none | about 135 MB |
| Desktop and web give the same answer | yes | yes | yes, at a cost |
| Offline invariant | held | held | held |
| Opens I4 | no | no | **no** |

**I4 is a data problem, not a code problem.** No option opens it. The only thing that opens it is
changing the ingest convention so that edges carry evidence, which is decision question 4 below.

## 7. What a conversation has to be here

### 7.1 One turn, end to end, all deterministic

A question arrives. Particle expansion widens its tokens. Search resolves the first node hit into an
identifier. Signal words classify the intent. Node context fetches the material. Nothing in that
chain is a judgement.

The answer card quotes the note verbatim, puts the chunk excerpt underneath with its document title
and section, and closes with counts of linked concepts, attached facts and history entries. Then the
things that can come next appear as chips. Those chips are not invented, they are the actual counts
that node context returned, which means **the system knows what it is able to say next and says so**.

### 7.2 Why this is not a search results page

| | Search | Conversation without a model |
|---|---|---|
| Returns | a flat ranked list | one answer plus the next moves |
| Next move | none, clicking leaves | chips derived from graph structure |
| History | the last few queries | the whole path taken |

The last row carries the argument. **The conversation log is a trace of graph traversal.** The trail
that Atlas already keeps is obtained in conversational form. So the reason for choosing a
conversational shape is not fashion: the answer is one thing rather than a list, the next move falls
out of the structure automatically, and the path is worth keeping. All three are true of this data
model.

### 7.3 Multi-turn without pronoun resolution

Rule-based dialogue usually dies here, because "that one" and "the concept from before" are
judgements. There is no need to resolve them.

The first device is that chips carry identifiers. A person does not type "and that one", they press a
chip, and the chip has the concept identifier on it, so ambiguity is structurally zero. The second
device is a focus stack: if someone does type, and the intent words match while the entity does not,
apply the intent to the node from the previous answer. That is a stack pointer, not an inference, and
it is the same mechanism the Atlas trail uses. The only real risk is the entity resolving to the
**wrong** node, so a minimum match confidence gates it and anything below falls back to focus.

### 7.4 Ambiguity is asked about, not papered over

With several candidates, do not choose. Offer them as chips and ask which one. Not pretending to know
is the trust basis of this surface, and it is the thing a model-backed chatbot structurally cannot do.

### 7.5 The input field has to teach its own grammar

**Rule-based dialogue loses the coverage race.** Give someone a blank cursor and they type "summarize
my data", get nothing, and do not come back. So the field must not look like a general chat box.
Three devices show the covered space: the opening screen lists only question shapes that work,
the field names the detected intent while the person types so they learn the grammar by writing, and
an unclassified sentence falls to exploration and returns search results. There is no failure state
on screen.

### 7.6 Calling it chat sets the wrong expectation

The behaviour is conversational, but the name is a separate decision. The product's story is that the
intelligence is outside, and a surface that looks like a general assistant collides with that head on.
Naming it as asking rather than chatting sets an expectation the surface can meet. A two-mode framing,
find and ask, sets the lowest expectation of all.

### 7.7 Cost

Roughly thirty lines for particle expansion, three for the props projection since the normalization
helper already exists, five for the record note projection, sixty for the intent router, a hundred
and fifty for card assembly and sixty for chips and the focus stack. About three hundred new lines in
the app and two local backend edits, with no new package dependencies and no new routes.

## 8. What is explicitly out

| Element | Ruling | Reason |
|---|---|---|
| Summarizing or restating | **permanently rejected** | quoting stored sentences is the entire condition for working without a model, and joining two excerpts is already generation |
| Synthesizing across several nodes | rejected | synthesis is judgement, list the sources separately and let the reader read |
| Similarity-based entity merging | **permanently rejected** | exact match only, already settled in [[vinc.spec.mcp-server]] |
| Free-form generated replies | **permanently rejected** | keeps the offline invariant, breaks the product placement |
| Guessing when it cannot answer | rejected | degrade to search results or ask back, never fill an empty answer |
| 240-character excerpts as the answer body | conditional | the current cut lands mid-word, quoting requires a sentence-boundary cut |

## 9. Phases, and what has since shipped

The plan was: phase 0 measure evidence and one-liner population as a gate, phase 1 project props and
record notes, phase 2 particle expansion, phase 3 sentence-boundary excerpts, phase 4 the router,
phase 5 the two MCP tools, phase 6 an ingest convention change that opens I4, phase 7 the optional
desktop embedding bundle. Phase 0 either confirms or refutes this document's central claim. Phases 1
to 3 improve the detail panel and search whether or not a conversation ships, so none of it is sunk.

**State on 2026-08-07, re-verified against the code.**

- **Phase 1 shipped, partly.** Node context projects props. **Record notes are still not projected**:
  the attachment axis returns record identifier, title and domain only, and the note is still
  dropped. So I6 has not opened.
- **Phase 2 shipped.** The query layer now strips one Korean particle from the end of a token when
  the remaining stem is at least two characters, dictionary-free and deterministic, at the node
  fallback tokenizer.
- **Phase 4 shipped as a surface.** An ask module of roughly 390 lines exists in the app, with a
  question and answer log, ask-back chips carrying identifiers, and a not-found path that names the
  other mode instead of dead-ending. A later revision swapped the positions of answer and basis: the
  canonical excerpt was promoted to the body with its source named, and the basis role moved to the
  related nodes, on the argument that a graph is a stronger basis than a paragraph.
- **Phase 5 has not shipped.** The tool list still has nineteen tools and neither context primitive
  is among them.
- **Section 3.2 has narrowed but not closed.** The document-derived concept path does write the
  one-liner column, extracting the first non-empty line after a heading. **The fragment path still
  does not**, so concepts written through MCP still have an empty one-liner while concepts derived
  from documents have one. That is worse than uniform absence, because the field is now
  inconsistently populated depending on which door the concept came through. Decision question 5
  below is still open and is now more urgent, not less.
- **I4 remains closed**, since nothing has changed about edge evidence.

## 10. Decision questions

The feature had been deferred as a product direction question, so a decision had to come first. Five
things needed deciding.

1. **Is there an in-app conversational surface at all**, and if so, does it live in its own surface
   or as a second mode inside Search, and is it called chat or asking. The name sets the expectation.
2. **Is the grade fixed at G1.** Fixing it makes the desktop and web answers permanently identical.
3. **Is the desktop embedding bundle paid for.** This is the same question left open in
   [[vinc.plan.embedding-pipeline]].
4. **Is edge evidence required by the ingest convention.** It is the only way to open I4, and it is
   one line in [[vinc.canon.graph-conventions]]. If it is not required, I4 should be declared
   permanently unsupported and the interface should stop inviting that question shape.
5. **Is the one-liner column revived or retired.** A column that reads back but has no writer is the
   worst of the three states. Either the fragment contract gains it, or the column goes and
   `props.note` becomes the single place.

> Question 5 is a live defect independent of the conversation.

## 11. Still unmeasured

- **Population of edge evidence across the whole graph.** The sample was two seeds and 44 edges.
- **Korean query performance.** An evaluation set of sixty bilingual queries has been run before.
  Running it either side of the particle change would put a number on section 5.4.
- **Whether the full-text index accepts a stemmer argument**, and whether any value of it is useful
  for Korean. If one exists it is more fundamental than the particle dictionary.

## Provenance

| Source | Relation |
|---|---|
| `Vinculums/Vinc` live code | `api/query.py`, `api/fragment.py`, `api/server.py`, `vinc_mcp/`, `app/src/` were traced for every claim about the contract |
| hosted account graph | the population rates in section 3 came from direct queries on 2026-08-02 |
| [[vinc.research.view-redesign]] | deferred this feature and stated the rejection reason this document corrects |
| [[vinc.canon.mcp-contract]] | the placement that keeps generation outside Vinc |
| [[vinc.canon.chunk-ssot]] | why the quotable text is a chunk and not a copy |
