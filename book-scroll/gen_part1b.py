#!/usr/bin/env python3
"""Part 1, chapters 6 to 12 (document slots 15 to 21), plus two prose patches.

Part 1's register is the conveyor: a payload enters a stage, takes that
stage's form, and leaves. No figures anywhere in Part 1 (addendum section 5).
Chapter 8 gets its own stage because the addendum names a specific motion for
it: every point in the field moves at once when the model changes.
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
CH = []


def chapter(no, slot, title, side, beats, conveyor=None, stage=None, nxt=None,
            viewBox="0 0 640 640"):
    n = int(slot.split(".")[2].split("-")[0])
    d = dict(n=n, no=no, part=1, file=f"Ch{n}.dc.html", slot=slot, title=title,
             side=side, viewBox=viewBox, beats=beats, marks=[], lines=[], anchors=[])
    if conveyor:
        d["conveyor"] = conveyor
    if stage:
        d["stage"] = stage
    if nxt:
        beats[-1]["nextLabel"], beats[-1]["nextHref"] = nxt
    CH.append(d)


def B(label, h, paras, claim=None):
    d = {"label": label, "h": h, "paras": paras}
    if claim:
        d["claim"] = claim
    return d


def OPEN(no, title, paras, claim=None):
    d = {"kind": "open", "label": f"{no}.0", "eyebrow": f"Part 1 · Chapter {no}",
         "h": title, "paras": paras}
    if claim:
        d["claim"] = claim
    return d


def CLOSE(no, paras):
    return {"kind": "close", "label": f"{no}.close", "paras": paras}


# ---------------------------------------------------------------- Chapter 6
chapter(6, "vinc.book.15-document-pipeline.en", "How a Document Becomes Nodes", "right", [
    OPEN(6, "How a Document Becomes Nodes", [
        "The system's real work starts the moment a document arrives.",
        "But converting a document straight into nodes is not the structure VINC chose.",
        "A document has its original wording and its context. The graph has reusable meanings and relationships.",
    ], "So processing is not replacing the source with a graph. It is adding reusable structure while the source stays."),
    B("6.1", "The First Step Is Preservation", [
        "The first step is storing the document.",
        "The whole text is kept and managed in units small enough to process. That unit does not redefine the document's meaning; it exists for search and for retrieval.",
        "The condition that matters is that the original can be reconstructed. After storage the system can search the text through those units and, when it needs to, assemble the whole document again.",
        "So nothing in the structuring step consumes the original sentences. They are still there, and still quotable.",
    ]),
    B("6.2", "The Second Step Is Discovery", [
        "Then the meanings worth reusing are found in the document.",
        "Candidate concepts can be proposed from its structure and its content. Automatic extraction is a starting point for finding meaningful structure, not a verdict.",
        "An extracted candidate is not therefore a new memory. The graph has to be checked first for the same concept.",
        "If it is already there, the document connects to the existing node instead of a new one being made. That check is the difference between a graph that grows and a graph that duplicates.",
    ], "Extraction proposes. It does not decide."),
    B("6.3", "MENTIONS Is the Axis", [
        "In this process the MENTIONS relationship does the load-bearing work.",
        "If a document mentioned a particular concept, the connection between the document and the concept is left behind.",
        "Then the concept exists independently in the graph and can still be followed back to whichever documents support it.",
        "Without that edge the concept floats: it has a title and no evidence, and nothing in the graph says which document put it there.",
    ]),
    B("6.4", "Automatic and Human-Refined Are Different", [
        "Automatic extraction finds candidates. A person can refine the title or the description afterwards.",
        "And once a concept has been cleaned up, a later extraction pass must not overwrite it arbitrarily.",
        "This is about the stability of memory. Every incoming document should add new evidence and new relationships to existing memory rather than manufacturing the same memory again.",
        "A pipeline that re-mints concepts on every run makes the graph noisier with every document it processes.",
    ]),
    B("6.5", "Not Compression", [
        "So going from a document to nodes is not compressing the document into nodes.",
        "The document preserves context. The graph structures meaning.",
        "Because the two are connected, you can go from one concept back to the source text, and from a document into the graph by following the concepts it mentioned.",
        "Neither direction is a summary of the other, and that is the property the rest of Part 1 depends on.",
    ], "The document holds context. The graph holds structure. Both, or neither works."),
    CLOSE(6, [
        "Now the structured memory has to be found during real work. The next chapters are about search.",
    ]),
], conveyor={"y": 300, "x0": 108, "x1": 532, "stations": [
    {"name": "arrives", "shape": "hex", "count": 1, "r": 17},
    {"name": "stored whole", "shape": "hex", "count": 1, "r": 17, "note": "reconstructable"},
    {"name": "chunks", "shape": "hex", "count": 4, "r": 9},
    {"name": "candidates", "shape": "circle", "count": 4, "r": 9, "note": "proposals, not verdicts"},
    {"name": "existing node?", "shape": "circle", "count": 2, "r": 12},
    {"name": "MENTIONS", "shape": "circle", "count": 2, "r": 12, "note": "the concept can be walked home"},
]}, nxt=("Chapter 7. How Memory Gets Found", "Ch16.dc.html"))

# ---------------------------------------------------------------- Chapter 7
chapter(7, "vinc.book.16-search.en", "How Memory Gets Found", "left", [
    OPEN(7, "How Memory Gets Found", [
        "Memory being stored somewhere is not enough to make it usable for the task in front of you.",
        "When a question comes in, the system first has to find relevant memory.",
        "But a search result, by itself, is not an answer.",
    ], "Search narrows down where memory might be. Something else has to decide what it is."),
    B("7.1", "Search Is Not One Method", [
        "Full-text search is strong at finding an exact word or expression, confirming that a specific phrase actually appears somewhere.",
        "Vector search is strong at finding candidates close in meaning even when the wording differs. A question phrased differently from the source can still pull in the documents and nodes sitting in the same semantic neighbourhood.",
        "The two do not compete. Full-text finds matching wording; vector finds semantic proximity.",
        "But results from either are still only candidates, and treating a candidate as a result is where a search system starts lying to the caller.",
    ]),
    B("7.2", "From Candidate to Target", [
        "If several concepts with similar titles show up in a result set, picking whichever scored highest is not automatically safe. Different memories can share similar wording.",
        "Candidates can arrive through more than one path: found directly in a text result, or surfaced through a node or record search.",
        "They get pooled under a common standard, and then the system has to identify the actual graph node.",
        "The core principle here is that when it is ambiguous, the system does not choose.",
    ], "When two candidates fit equally and nothing separates them, holding off is the answer."),
    B("7.3", "A Memory-Identity Problem", [
        "This is not a search-quality problem. It is a memory-identity problem.",
        "Pick the wrong node once and everything the system explores from there proceeds on the wrong memory as its foundation, and every later step looks perfectly well-formed.",
        "Conversely, once a node is clearly resolved it becomes an entry point. From it you can follow connected records, episodes, decisions and concepts, and expand into the surrounding structure.",
        "If you need to, you can go all the way back to the original text of a connected document.",
    ]),
    B("7.4", "The Flow Runs Longer Than Search", [
        "So retrieval is longer than a query. A question produces candidates, the candidates are integrated, one node is resolved, and only then do related nodes, documents and evidence come into view.",
        "Resolution is not the step that turns a search result into an answer.",
        "It is the step that identifies, among several candidates, which memory the current question is actually pointing at.",
        "A high similarity score on a sentence does not make that sentence the answer to the question that was asked.",
    ]),
    B("7.5", "Language Is Not Identity", [
        "The same concept can be expressed in more than one language, and a question in one language can point at a node written in another.",
        "Because a Korean question and an English document can be connected semantically, vector search helps surface candidates across that gap.",
        "But two languages differing is never enough by itself to confirm the two are the same memory. Final identification still needs the node's structure and its relationships.",
        "So language difference widens the candidate set at the search step, and is settled at the resolution step.",
    ], "At this point search stops being a database function and becomes the entrance to a memory system."),
    CLOSE(7, [
        "Before resolution can be examined properly, one layer underneath it has to be: how meaning becomes a space that can be searched at all.",
    ]),
], conveyor={"y": 296, "x0": 108, "x1": 532, "stations": [
    {"name": "question", "shape": "hex", "count": 1, "r": 15},
    {"name": "full text", "shape": "circle", "count": 3, "r": 9, "note": "matching wording"},
    {"name": "vector", "shape": "circle", "count": 5, "r": 9, "scatter": True, "note": "semantic proximity"},
    {"name": "pooled", "shape": "circle", "count": 5, "r": 9},
    {"name": "resolve", "shape": "circle", "count": 1, "r": 15, "note": "ambiguous means no choice"},
    {"name": "entry point", "shape": "ring", "count": 1, "r": 16},
]}, nxt=("Chapter 8. How Meaning Becomes Space", "Ch17.dc.html"))

# ---------------------------------------------------------------- Chapter 8
chapter(8, "vinc.book.17-embedding.en", "How Meaning Becomes Space", "right", [
    OPEN(8, "How Meaning Becomes Space", [
        "In a memory system the vector is the least visible component. The graph has nodes and relationships; documents have text a person can read. A vector is an array of numbers.",
        "In a search system that array does one job: it places a passage inside a space of meanings.",
        "The flow is simple. A document becomes chunks, each chunk is embedded, and the vectors are stored.",
    ], "What matters is not the vector. It is which space the vector was placed in."),
    B("8.1", "Chunks Come First", [
        "Embedding does not happen in a vacuum. There has to be text to search first.",
        "The whole document is not turned into one vector. It is split, and each piece becomes an independent search target.",
        "So the size and the boundaries of a chunk affect search quality directly. Too large and different content is blended into one vector. Too small and the context is lost.",
        "Which means the embedding model alone does not determine search quality. What was made into one chunk matters as much.",
    ]),
    B("8.2", "A Fixed Number of Dimensions", [
        "The model turns text into a fixed-width array of numbers. If its output dimension is 768, one chunk is represented by 768 reals.",
        "At search time the question is embedded the same way, and the similarity between the question's vector and the stored vectors is computed.",
        "So vector search is unlike string search: the strings do not have to match if the two are close in the space of meanings.",
        "The catch is that closeness in that space is a property of the model, not of the world.",
    ]),
    B("8.3", "The Dimension Is Part of the Store", [
        "Here a real technical constraint appears. If the store expects 768-wide vectors and a new model emits a different width, changing the model does not finish the job.",
        "The storage structure has to be compatible with the model's output space too.",
        "Model, dimension and store are bound together as one contract. So swapping an embedding model is not a configuration change.",
        "If the existing data lives in the previous model's space, that data has to be converted into the space the new search runs in.",
    ], "Changing the store does not move old vectors into the new space by itself."),
    B("8.4", "A Vector Is Not the Memory", [
        "A successful vector search does not complete a memory.",
        "A vector does not stand in for the source text, and it does not stand in for the concepts and relationships in the graph.",
        "One search result can hold at least three different kinds of information: the source text, the vector, and the related graph nodes. The vector is the one that is for finding.",
        "The text supplies evidence, the graph supplies meaning and relationships, and the vector finds semantically near candidates. Equate the vector with memory and the role of the search system is misread.",
    ]),
    B("8.5", "The Cost of Changing Models", [
        "Change the chunking and results change with the same model. Change the model and the space changes under the same chunks. Change which candidate gets selected and the final result changes again with the same vectors.",
        "So embedding is one stage in a retrieval pipeline, not the retrieval system.",
        "And the cost of replacing a model is not only throughput. Existing chunks have to be re-embedded, the new vectors stored, and the index rebuilt. The more data there is, the larger that cost.",
        "This is measured with real numbers in Part 2. Here the point is only that it is a migration, not a setting.",
    ], "A vector is not the final form of memory. It is a coordinate for finding it."),
    CLOSE(8, [
        "The next chapter returns to the step this one keeps pointing at: how a search result gets confirmed as a memory.",
    ]),
], stage=[
    "  // Part 1 register, with the one motion the addendum names for this",
    "  // chapter: when the model changes, every point in the field moves at",
    "  // once. That single motion carries the whole cost-of-re-embedding",
    "  // argument, so it is scrubbed rather than played and it reverses.",
    "  FIELD = 34;",
    "  pt(i, shifted) {",
    "    const a = i * 137.508, rad = Math.sqrt((i + 1) / this.FIELD) * 168;",
    "    const q = pol(320, 300, rad, a);",
    "    if (!shifted) return q;",
    "    // the same points in a different space: a different model, so a",
    "    // different arrangement, not a jittered version of this one",
    "    const b = i * 97.3 + 40, r2 = Math.sqrt(((i * 7) % this.FIELD + 1) / this.FIELD) * 168;",
    "    return pol(320, 300, r2, b);",
    "  }",
    "  stage(s, p) {",
    "    const E = React.createElement, kids = [];",
    "    // beats 0 to 1 build the field out of chunks; beat 5 moves all of it",
    "    const grown = s >= 1 ? 1 : smooth(clamp((p - 0.15) / 0.7, 0, 1));",
    "    const moved = s >= 5 ? (s > 5 ? 1 : smooth(clamp(p * 1.2, 0, 1))) : 0;",
    "    const n = Math.max(1, Math.round(grown * this.FIELD));",
    "    for (let i = 0; i < n; i++) {",
    "      const A = this.pt(i, false), B = this.pt(i, true);",
    "      const x = lerp(A[0], B[0], moved), y = lerp(A[1], B[1], moved);",
    "      kids.push(E('path', { key: 'p' + i, d: blob(x, y, 5, K), fill: INK }));",
    "    }",
    "    // the document the field came out of, kept at the left as context",
    "    kids.push(E('polygon', { key: 'doc',",
    "      points: shapePoints('hex', 74, 300, 16), fill: PAPER, stroke: EDGE,",
    "      strokeWidth: 1.25, strokeLinejoin: 'round' }));",
    "    kids.push(E('text', { key: 'dt', x: 74, y: 336, textAnchor: 'middle', fontSize: 12.5,",
    "      fontStyle: 'italic', fill: EDGE }, 'one document'));",
    "    kids.push(E('text', { key: 'ft', x: 320, y: 520, textAnchor: 'middle', fontSize: 13,",
    "      fontStyle: 'italic', fill: INK },",
    "      moved > 0.02 ? (moved > 0.98 ? 'a different model, a different space'",
    "                                   : 'every point moves at once')",
    "                   : (n < this.FIELD ? n + ' of ' + this.FIELD + ' chunks embedded'",
    "                                     : 'near points sit together')));",
    "    if (s >= 3) {",
    "      kids.push(E('text', { key: 'dim', x: 320, y: 548, textAnchor: 'middle', fontSize: 12.5,",
    "        fill: META, style: { fontVariantNumeric: 'tabular-nums' } },",
    "        moved > 0.5 ? 'the store has to accept this width too' : '768 reals per chunk'));",
    "    }",
    "    return E('g', { key: 'field' }, kids);",
    "  }",
], nxt=("Chapter 9. How Search Results Get Confirmed as Memory", "Ch18.dc.html"))

# ---------------------------------------------------------------- Chapter 9
chapter(9, "vinc.book.18-retrieval.en", "How Search Results Get Confirmed as Memory", "left", [
    OPEN(9, "How Search Results Get Confirmed as Memory", [
        "Search is the entrance to a memory system. But a search result does not become memory by arriving.",
        "One question can produce several candidates.",
        "The problem is not finding many. It is deciding which one should actually be interpreted.",
    ], "Search asks what could be related. Resolution asks which of them this points at."),
    B("9.1", "Candidates Arrive From Several Paths", [
        "Search candidates are not produced by one database.",
        "A text result can be a candidate. A vector result can be a candidate. A node in the graph can be found directly. A record or a decision can be connected to the question as well.",
        "What matters is that these candidates are different shapes. A text result holds a document fragment, a graph search holds a concept, a record is a separate object again.",
        "Pooling them means putting different shapes under one standard before anything is chosen.",
    ]),
    B("9.2", "Candidate and Resolved Are Different States", [
        "Chapter 7 already showed that this system does not choose in the face of ambiguity. The question here is how that principle is actually kept.",
        "If two candidates look equally plausible, having a rule that picks one of them is not what justifies the pick.",
        "The absence of grounds for choosing is itself a result. Given that one wrongly chosen node drags every later traversal in the wrong direction, not choosing is a normal response for the system.",
        "Read resolution as a similarity threshold and this structure is lost.",
    ], "Not choosing is a normal answer, not a failed one."),
    B("9.3", "Then the Material Comes Out", [
        "Search is the stage that produces candidates. Resolution is the stage that confirms whether a candidate can be identified at all.",
        "With that split, search can look broadly while resolution behaves conservatively.",
        "Once one node is resolved, the material around it can be assembled: related concepts, related records, related decisions, and the document excerpts behind them.",
        "The search result is not a list of text. It is the starting point for the next traversal.",
    ]),
    B("9.4", "A Record Is Not a Concept", [
        "A concept is a unit of meaning the system works with. A record is an object preserving a particular fact or measurement.",
        "Finding a concept does not mean the related records can be treated as the same thing. And a record being found does not make it the concept either.",
        "After resolution these different objects have to be read together, with the difference between them intact.",
        "An episode is different again: it records what happened in time order, so it is material for recalling when something occurred rather than a target for resolution.",
    ]),
    B("9.5", "Search Failure Is a State", [
        "There are cases with no result, with too few candidates, or with candidates that cannot be told apart.",
        "In those cases the system does not manufacture a memory.",
        "No candidates means no resolution. Ambiguous candidates means no resolution. This is not a failed search; it is an important system state.",
        "One of the most dangerous outcomes in a memory system is resolving onto something with no grounds and having every later step treat it as fact.",
    ], "The structure needs the ability to find and the ability not to choose."),
    CLOSE(9, [
        "The next chapter asks what the resolved node is actually turned into before an agent sees it.",
    ]),
], conveyor={"y": 300, "x0": 108, "x1": 532, "stations": [
    {"name": "question", "shape": "hex", "count": 1, "r": 15},
    {"name": "candidates", "shape": "circle", "count": 5, "r": 9, "scatter": True},
    {"name": "pooled", "shape": "circle", "count": 5, "r": 9, "note": "different shapes, one standard"},
    {"name": "resolve", "shape": "circle", "count": 1, "r": 16, "note": "or refuse to"},
    {"name": "neighbours", "shape": "diamond", "count": 3, "r": 10},
    {"name": "evidence", "shape": "hex", "count": 3, "r": 10, "note": "back to the source"},
]}, nxt=("Chapter 10. How Memory Becomes Material", "Ch19.dc.html"))

# ---------------------------------------------------------------- Chapter 10
chapter(10, "vinc.book.19-brief.en", "How Memory Becomes Material", "right", [
    OPEN(10, "How Memory Becomes Material", [
        "Finding one memory does not let an agent produce an answer.",
        "What an agent needs is the set of memories connected to the question, and those memories can carry different kinds of evidence and relationship.",
        "That selected, connected set can be called answer material.",
    ], "The question is not what is related. It is what has to be read together to understand this."),
    B("10.1", "Brief Is Not Another Name for Search", [
        "A tool like this is not a search that returns a few more rows.",
        "Its job is to connect a question to one node and assemble the material needed to understand that node.",
        "So it folds several retrieval steps into one surface: search, resolution, and the node's context.",
        "Returning a single search result is not enough for that. The relationships around the resolved node, the connected records, and the necessary document excerpts all have to be considered together.",
    ]),
    B("10.2", "The Centre Is the Resolved Node", [
        "The centre of the material is not the question. It is the resolved node.",
        "That is what makes the material traceable: why a sentence was included, which record it relates to, which document it came from.",
        "A graph supplies relationships. A document supplies the text that acts as evidence.",
        "With only the graph, the relationships are known and the detailed grounds are missing. With only documents, the text is there and the concepts it belongs to have to be found again.",
    ]),
    B("10.3", "Not a Sentence", [
        "What comes back is a stack of excerpts, not a composed sentence.",
        "That is deliberate. A sentence would be a new interpretation of the memory, and it would be impossible to tell afterwards which part was recorded and which part was written just now.",
        "MENTIONS is the axis that makes the stack navigable: from a node to a document, and from that document back to the related concepts.",
        "So assembling material is the work of connecting graph and document, not the work of writing prose about them.",
    ], "A sentence is not what comes back, and that is the point."),
    B("10.4", "More Is Not Better", [
        "Pull in every connection and the agent's judgment gets harder, not easier.",
        "So the result needs a budget. Large results are not returned as they are; the surface reduces them.",
        "What matters is not cutting data arbitrarily but keeping, inside a limited result, the structure the question needs in order to be solved.",
        "This is not a display optimisation. An agent's context is limited too, so the size and the shape of the material affect call cost and judgment quality directly.",
    ]),
    B("10.5", "Insufficient Is an Answer", [
        "The purpose is not to always produce something. There may be no node corresponding to the question, too few relationships, or not enough supporting documents.",
        "In that case the insufficiency itself is the result.",
        "Hiding that state and producing a plausible answer is the opposite of what a memory system is for.",
        "Answer material is a middle layer: not the final answer and not raw data. With it, an agent does not have to search every document and traverse the graph from scratch on every question.",
    ], "Without that layer, the agent has to understand the store's internals itself."),
    CLOSE(10, [
        "The next chapter follows the material out through the boundary where an external agent actually reaches it.",
    ]),
], conveyor={"y": 300, "x0": 108, "x1": 532, "stations": [
    {"name": "question", "shape": "hex", "count": 1, "r": 15},
    {"name": "resolved", "shape": "circle", "count": 1, "r": 16},
    {"name": "relationships", "shape": "diamond", "count": 3, "r": 10},
    {"name": "records", "shape": "hex", "count": 3, "r": 10},
    {"name": "excerpts", "shape": "hex", "count": 4, "r": 9, "note": "a stack, not a sentence"},
    {"name": "budgeted", "shape": "hex", "count": 3, "r": 9, "note": "reduced, with the structure kept"},
]}, nxt=("Chapter 11. How Memory Leaves the System", "Ch20.dc.html"))

# ---------------------------------------------------------------- Chapter 11
chapter(11, "vinc.book.20-mcp.en", "How Memory Leaves the System", "left", [
    OPEN(11, "How Memory Leaves the System", [
        "However well a memory system works internally, its real use is limited if an agent cannot reach it.",
        "VINC connects that boundary with MCP.",
        "The MCP layer does not judge the meaning of memory. It is the boundary that passes an outside request into an internal capability.",
    ], "A door is not the room. Keeping the two apart is what lets either change."),
    B("11.1", "A Thin Bridge", [
        "The implementation is kept as thin as it can be. What this layer handles is protocol, request validation, authentication, which space to act in, passing the tool call through, and limiting the size of the result.",
        "What it deliberately does not do matters as much. Judging meaning, structuring concepts, deciding duplication, settling what a memory means: none of that happens here.",
        "Those responsibilities stay with the search, graph and document layers inside.",
        "The reason is simple. Once the boundary starts taking on judgment, one door becomes a second implementation of the graph's contract.",
    ], "A door and the graph's rules should change at different speeds."),
    B("11.2", "Two Channels, One Relay", [
        "There is a local channel and a remote channel. The channel differs and the relay behind it does not.",
        "So initialise, ping, list the tools, call a tool: all of these use a shared processing structure whichever way in was used.",
        "The point of that arrangement is to separate the channel from the logic of memory.",
        "From outside VINC can look like a set of tools. Inside, this boundary does not define the memory system, and changing the boundary does not require the graph, document and retrieval structures to change with it.",
    ]),
    B("11.3", "A Tool Has a Safety Class", [
        "Not every tool carries the same risk. Reading and writing are different. Deleting a document and searching are different.",
        "So tools are graded from their annotations and their registration, and safety is managed against that grade.",
        "The principle here is to decide toward blocking when unsure. Where a tool's safety characteristics cannot be determined confidently, the more dangerous permission is not granted automatically.",
        "A default that fails open is a default that will eventually be wrong quietly.",
    ]),
    B("11.4", "Authentication and Space Together", [
        "Authentication alone is not enough. Which graph space the call acts in has to be settled as well.",
        "With a team address, the address itself says which graph is being entered, while the credential proves the right to enter it.",
        "An authenticated request that does not belong to the space must not reach that data. Every request checks authentication and space selection before the tool actually runs.",
        "That is what keeps the access rule steady even when the way in changes.",
    ]),
    B("11.5", "Calls Are Part of the Design", [
        "Results grow as connected memory grows, and the result ends up in the agent's context. Without a limit, cost and throughput suffer no matter how accurate search became.",
        "So the surface has a result budget, and when a result had to shrink, the fact that it shrank is itself signalled.",
        "And performance here cannot be judged by channel latency. What matters from an agent's side is how many calls it took to reach the answer, how many were wasted, whether the result was the right size, and whether the evidence came with it.",
        "That question is measured with real numbers in Part 2.",
    ], "MCP's job is to make memory reachable safely and consistently. Not to decide what it means."),
    CLOSE(11, [
        "The next chapters turn to who is reaching through that boundary, and what a role has to carry to be one.",
    ]),
], conveyor={"y": 300, "x0": 108, "x1": 532, "stations": [
    {"name": "agent", "shape": "ring", "count": 1, "r": 15},
    {"name": "channel", "shape": "circle", "count": 1, "r": 12, "note": "local or remote"},
    {"name": "auth + space", "shape": "diamond", "count": 1, "r": 13, "note": "both, before the tool runs"},
    {"name": "grade", "shape": "circle", "count": 1, "r": 12, "note": "unsure means blocked"},
    {"name": "tool", "shape": "circle", "count": 1, "r": 14},
    {"name": "budgeted result", "shape": "hex", "count": 3, "r": 9, "note": "and it says it shrank"},
]}, nxt=("Chapter 12. A Role Is a Node", "Ch21.dc.html"))

# ---------------------------------------------------------------- Chapter 12
chapter(12, "vinc.book.21-agents.en", "A Role Is a Node", "right", [
    OPEN(12, "A Role Is a Node", [
        "Look at an agent as a program only and it becomes hard to say which memory it should be using.",
        "Agents on the same graph can have different purposes. One handles search, one handles evaluation, one analyses documents.",
        "VINC expresses that difference as an agent role, and a role can be a node the graph keeps referring to rather than a setting.",
    ], "A role that lives in a config file is a role nothing else can point at."),
    B("12.1", "An Agent as a Concept", [
        "An agent can exist in the graph in the same way other concepts do, with a role, a domain, related decisions, related concepts and a prompt surface attached.",
        "With that structure the agent's role can be traversed like any other concept in the graph.",
        "Expressing an agent as a node also separates its definition from the process that executes it.",
        "The definition persists; the process is whatever is running this week.",
    ]),
    B("12.2", "A Role Describes a Boundary", [
        "A role is not a naming feature.",
        "If a particular agent handles search, what kind of information it should look for, which domain it works in, and which prompt it may use can all be expressed as connections.",
        "So an agent's behaviour does not have to be crammed into one system prompt.",
        "The identity, the purpose, the domain, the constraints and the related decisions each become something a reader can find, rather than a paragraph someone has to remember to update.",
    ]),
    B("12.3", "Agent and Prompt Are Not the Same", [
        "MCP can expose the list of roles and their contracts through a prompt surface. Agent and prompt should not be read as the same thing.",
        "An agent says who performs which role. A prompt says what instructions are given to perform it.",
        "With that split, the prompt an agent uses can be replaced, or the prompt surface widened, and the agent's identity in the graph stays what it was.",
        "Which is what makes a role survive a rewrite of its instructions.",
    ]),
    B("12.4", "The Graph Is Not a Scheduler", [
        "Expressing an agent as a node does not mean the graph directs its execution.",
        "The graph can express that agent A uses concept X, relates to decision Y, follows prompt Z.",
        "When to run, how many times, and which worker takes the job belong to a separate execution environment.",
        "That separation matters. Use the graph as a scheduler and the semantic graph and the execution state end up tangled in one structure, where neither can be changed without disturbing the other.",
    ], "The graph holds meaning and relationships. The runner holds execution."),
    B("12.5", "A Roster the Graph Can Answer", [
        "Model roles as concept nodes and the system can build the agent roster from the graph directly.",
        "Each agent carries its own domain and relationships, which is stronger than a plain list: an agent can be traversed the way any other graph element is.",
        "Which agent belongs to which domain, which concepts it connects to, which decisions it refers to, all from one graph.",
        "So the reason to make a role a node is not to make agents more complicated. It is to let the system describe its own execution structure.",
    ], "The thing that uses memory becomes something the memory system can read."),
    CLOSE(12, [
        "The next chapter asks what that existence actually has to keep: how a role becomes a contract.",
    ]),
], conveyor={"y": 300, "x0": 108, "x1": 532, "stations": [
    {"name": "a program", "shape": "circle", "count": 1, "r": 13},
    {"name": "a role node", "shape": "ring", "count": 1, "r": 16, "note": "referable, traversable"},
    {"name": "domain", "shape": "circle", "count": 1, "r": 12},
    {"name": "prompt", "shape": "hex", "count": 1, "r": 12, "note": "replaceable, identity kept"},
    {"name": "the runner", "shape": "diamond", "count": 1, "r": 13, "note": "when, how often, which worker"},
    {"name": "roster", "shape": "ring", "count": 4, "r": 10, "scatter": True},
]}, nxt=("Chapter 13. How a Role Becomes a Contract", "Ch21b.dc.html"))


for ch in CH:
    p = os.path.join(HERE, "ch", f"ch{ch['n']}.json")
    json.dump(ch, open(p, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=2)
    print("wrote", os.path.basename(p), "part1 ch", ch["no"])

# --- prose patches for the two beats the volume gate flagged in batch a ---
PATCH = {
    "ch/ch11.json": {"2.4": "A vocabulary nobody can hold in mind is a vocabulary that gets used inconsistently."},
    "ch/ch13.json": {"4.4": "Most phrases in a document are not worth a node, and deciding that is the work."},
}
for f, add in PATCH.items():
    d = json.load(open(os.path.join(HERE, f), encoding="utf-8"))
    for b in d["beats"]:
        if b.get("label") in add:
            b["paras"].append(add[b["label"]])
    json.dump(d, open(os.path.join(HERE, f), "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=2)
    print("patched", f)
