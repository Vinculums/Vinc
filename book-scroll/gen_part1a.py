#!/usr/bin/env python3
"""Part 1, chapters 1 to 5 (document slots 10 to 14).

Compact spec in, ch/*.json out. Part 1's register is the conveyor: a payload
enters a stage, takes that stage's form, and leaves. Nothing accumulates, and
there are no figures anywhere in Part 1 (addendum section 5).
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))

CH = []


def chapter(no, slot, title, side, beats, conveyor=None, marks=None, lines=None,
            nxt=None, viewBox="0 0 640 640"):
    CH.append(dict(n=int(slot.split(".")[2].split("-")[0]), no=no, part=1,
                   file=f"Ch{slot.split('.')[2].split('-')[0]}.dc.html",
                   slot=slot, title=title, side=side, viewBox=viewBox,
                   beats=beats, conveyor=conveyor, marks=marks or [],
                   lines=lines or [], anchors=[]))
    if nxt:
        CH[-1]["beats"][-1]["nextLabel"] = nxt[0]
        CH[-1]["beats"][-1]["nextHref"] = nxt[1]


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


# ---------------------------------------------------------------- Chapter 1
chapter(1, "vinc.book.10-architecture.en", "What VINC Stores", "left", [
    OPEN(1, "What VINC Stores", [
        "To turn memory into a system, we first have to define the form memory is stored in.",
        "VINC's physical model does not store everything in one free-form structure. It separates the roles a node can have, and expresses the relationships between nodes as a graph.",
        "The central distinction is the kind of node: Concept, Episode, Record, Decision, Bridge, Entity.",
    ], "The reason to separate kinds is that these memories are not used the same way."),
    B("1.1", "Six Kinds, Six Questions", [
        "A Concept represents a meaning the system can keep referring to. An Episode represents a particular event or experience. A Record represents something observed or confirmed. A Decision represents a judgment or a choice made about something.",
        "A Bridge is the unit that connects separate areas or structures. An Entity is a structure for identifying a particular thing.",
        "Adopting a fact about a technology and running a test on a Tuesday are both memory, and they are not the same kind of memory.",
        "Store one Concept and one Episode in the same shape and later, in search and in relationship traversal, the two become hard to tell apart.",
    ]),
    B("1.2", "Kinds Are Closed, Contents Are Not", [
        "So the node model keeps the set of kinds closed and defines each role precisely.",
        "The contents are a different matter. Each node carries the properties its own role needs, and connects to other nodes through relationships.",
        "That combination is what lets the structure stay stable while the meanings inside it keep growing. A new subject does not require a new kind of node.",
        "It requires a new node of an existing kind, filed under the right topic, with the right relationships attached.",
    ]),
    B("1.3", "An Edge Is Not a Line", [
        "The edges of the graph are not simply connection information either.",
        "About says what a piece of memory is a memory of. Prerequisite-of expresses a precondition. Derives-from expresses a derivation. Inherits-from expresses a succession.",
        "Direction matters as much as the name. Recording that A is a prerequisite of B and recording that B is a prerequisite of A are different claims about the world.",
        "So the graph has to preserve the kind and the direction of a relationship alongside the nodes themselves.",
    ], "A relationship carries a claim, and a claim can be right or wrong."),
    B("1.4", "Two Clocks and One Space", [
        "Time is part of the structure. When the event a memory describes actually happened and when that memory entered the system are different questions.",
        "This book calls the first world time and the second graph time. The distinction is taken up properly later, and it matters here because a single timestamp cannot hold both.",
        "Which space a graph belongs to matters as well. A personal space and a team space determine the reach of a memory: what is searched with it, and who may change it.",
    ]),
    B("1.5", "The Source Text Is Kept Beside It", [
        "One memory is therefore not made of one value. A node defines what a thing is, an edge defines a relationship, time defines the context, and space defines the reach.",
        "And the source text is preserved separately from all of that.",
        "When a document is structured, the original wording is not thrown away. The graph is not a copy that replaces the document; it is the structure of meanings and relationships that can be used again from it.",
        "None of this exists to make storage complicated. It exists so the system can be precise about what kind of memory it is holding.",
    ], "The graph does not replace the document. It makes the document usable more than once."),
    CLOSE(1, [
        "Once the physical model is settled, the next problem is how that model and the source text connect to each other.",
        "VINC does not merge documents and the graph into one thing. It keeps each in its own role and lets you follow one to the other.",
    ]),
], conveyor={"y": 300, "x0": 110, "x1": 530, "stations": [
    {"name": "source", "shape": "hex", "count": 1, "r": 15, "note": "one document"},
    {"name": "kind", "shape": "circle", "count": 1, "r": 13, "note": "a role, not a label"},
    {"name": "properties", "shape": "circle", "count": 3, "r": 9},
    {"name": "edges", "shape": "diamond", "count": 3, "r": 10, "note": "kind and direction"},
    {"name": "time", "shape": "circle", "count": 3, "r": 9, "note": "two clocks"},
    {"name": "space", "shape": "circle", "count": 3, "r": 9, "scatter": True},
]}, nxt=("Chapter 2. What Are Nodes and Edges", "Ch11.dc.html"))

# ---------------------------------------------------------------- Chapter 2
chapter(2, "vinc.book.11-memory-model.en", "What Are Nodes and Edges", "right", [
    OPEN(2, "What Are Nodes and Edges", [
        "Chapter 1 divided memory into six kinds of node. Here we look at what that division actually does, and why an edge between nodes is not just a line.",
        "Putting the name Concept on a node and that node actually being treated as a concept are two different things.",
        "If a node is a Concept, it gets referred to repeatedly, becomes a candidate in search, and can relate to other concepts.",
    ], "The kind of a node is not decoration. It is a contract about how the system will handle it."),
    B("2.1", "Naming Is Not Typing", [
        "If a node is an Episode, it means an event recalled in time order, and it is not usually the thing search resolves onto.",
        "Write Concept and treat it like an Episode, or the reverse, and search and traversal come apart from each other.",
        "The failure is quiet. Nothing errors; the node is simply never found by the queries that should have found it, and is found by ones that should not.",
        "So the kind is a decision about handling, made once, at the point the memory is written.",
    ]),
    B("2.2", "One Sentence, Three Kinds", [
        "Each kind answers a different question. A Concept answers what this means. An Episode answers when something happened. A Record answers what was observed or confirmed. A Decision answers what was chosen.",
        "A Bridge answers where two separate areas meet. An Entity answers what this is called, for a thing that needs identifying before it has become a proper concept.",
        "Take one sentence: we decided to adopt a new vector database. At least three kinds of information are folded into it.",
        "The database as a concept, the choice as a decision, and the circumstances of the moment as an episode. Store them as one lump of text and the question of why that technology was chosen becomes unanswerable.",
    ], "Separate them and the question can be answered from the part that holds the answer."),
    B("2.3", "An Edge Carries Direction and Meaning", [
        "Knowing that two nodes are connected is not enough information.",
        "If A and B are connected, is A the cause of B, is A a condition that must hold before B, or are the two merely related? Those are entirely different statements.",
        "So an edge has a name and a direction. A prerequisite-of B is a concrete claim: A has to hold before B does. Reverse it and it says something else.",
        "The relationships used most are deliberately few: relates-to for loose association, prerequisite-of for a precondition, derives-from, inherits-from, and about.",
    ]),
    B("2.4", "Why the Vocabulary Stays Small", [
        "The set is not kept small out of tidiness. Every additional relationship name is another thing a reader, an assistant and an agent all have to agree on the meaning of.",
        "The cost of that agreement is paid again on every new node written and every query asked.",
        "And not every kind can be joined by every relationship. Relates-to between two concepts is natural; between two decisions it usually is not, because decisions relate through lineage rather than through vague association.",
        "One decision superseded another. That is the shape a decision-to-decision relationship normally has.",
    ]),
    B("2.5", "A Graph That Says Nothing", [
        "Without those constraints a graph can look dense and say nothing.",
        "A graph where everything relates-to everything is not different, in practice, from a graph that makes no distinctions at all.",
        "So nodes settle what exists, and edges settle the terms on which those things meet.",
        "Keeping the two separate is what lets a later question be precise: not everything related to this concept, but what had to come before it, and what was derived from it.",
    ], "Everything connected to everything is the same picture as nothing connected at all."),
    CLOSE(2, [
        "The next chapter follows these nodes and edges back to the source document, and asks how the graph holds on to the meaning that came out of a document without replacing the document itself.",
    ]),
], conveyor={"y": 320, "x0": 120, "x1": 520, "stations": [
    {"name": "a sentence", "shape": "hex", "count": 1, "r": 16, "note": "one lump of text"},
    {"name": "kinds", "shape": "circle", "count": 3, "r": 11, "note": "concept · decision · episode"},
    {"name": "named", "shape": "circle", "count": 3, "r": 11},
    {"name": "directed", "shape": "diamond", "count": 3, "r": 11, "note": "A before B is not B before A"},
    {"name": "constrained", "shape": "circle", "count": 3, "r": 11},
    {"name": "answerable", "shape": "circle", "count": 1, "r": 15, "note": "why was it chosen"},
]}, nxt=("Chapter 3. How Documents and the Graph Connect", "Ch12.dc.html"))

# ---------------------------------------------------------------- Chapter 3
chapter(3, "vinc.book.12-canonical-graph.en", "How Documents and the Graph Connect", "left", [
    OPEN(3, "How Documents and the Graph Connect", [
        "Structuring memory into nodes and edges does not make the original document unnecessary.",
        "If anything, the stronger the graph gets, the more the question matters: where did this node's claim come from?",
        "Suppose the sentence we are adopting a new vector database appears in three different documents.",
    ], "Three appearances do not have to mean three memories."),
    B("3.1", "Same Sentence, Different Memory", [
        "The three documents may be mentioning the same project and the same decision from three points in time.",
        "But merging two decisions from different projects because their wording is similar erases the difference between the choices those projects actually made.",
        "So the test for whether this is the same memory is not surface similarity of the text.",
        "The test lives on the graph side. The document is where wording and context are preserved; the graph is where identifiers settle whether two expressions point at the same thing.",
    ]),
    B("3.2", "Document and Chunk Are Different Layers", [
        "When a document is stored, the whole text does not become one node. It is split into smaller pieces.",
        "That piece is not a new unit of meaning. It is a unit of processing, for searching and for fetching back.",
        "One condition matters here: the split has to be reversible. Joining the pieces back together has to give the original exactly.",
        "If a sentence gets cut or the order shifts during storage, there is no longer any way to confirm what the evidence actually said, and the store has quietly stopped being evidence.",
    ], "A quietly truncated document is indistinguishable from a short one."),
    B("3.3", "Finding Meaning, Not Erasing Text", [
        "After the document is stored, the concepts worth reusing are found inside it.",
        "If the document turns out to be about a particular vector database, that database becomes a concept, or connects to one that already exists. The connection is called MENTIONS.",
        "The source text does not disappear in the process. A concept is not a summary that stands in for the document; it is closer to a handle that points at a meaning the document can supply again.",
        "So opening one concept should let you walk back to the documents that support it.",
    ]),
    B("3.4", "Reuse Matters More Than Creation", [
        "If every concept discovered in every incoming document becomes a new node, the number of nodes pointing at one concept keeps growing.",
        "The graph gets bigger and less useful. When information about one concept is scattered across ten nodes, there is no way to know which one to read to see the whole picture.",
        "So processing a new document always starts with a question: is this concept already in the graph? If it is, connect the document to the existing node rather than making another.",
        "That judgment does not follow automatically from how similar the wording is, because similar wording can point at different projects and different moments.",
    ], "Search before write, or the same thing exists twice."),
    B("3.5", "Splitting the Responsibility", [
        "The reason to divide the work between document and graph is simple.",
        "When a document changes, the document is corrected. When a meaning in the graph changes, nodes and relationships are corrected.",
        "Bundle the two together and every document edit forces a pass over every concept derived from it, or every concept refinement reaches back into the source text.",
        "Kept apart, each can move on its own. The source stays exactly preserved, and the graph above it can keep being reorganised and reconnected.",
    ]),
    CLOSE(3, [
        "The next chapter asks how far to go in making these handles, and why the concept system is managed separately from the graph's own structure.",
    ]),
], conveyor={"y": 300, "x0": 110, "x1": 530, "stations": [
    {"name": "document", "shape": "hex", "count": 1, "r": 17, "note": "wording and context"},
    {"name": "chunks", "shape": "hex", "count": 4, "r": 9, "note": "a processing unit, reversible"},
    {"name": "candidates", "shape": "circle", "count": 4, "r": 9},
    {"name": "search first", "shape": "circle", "count": 2, "r": 11, "note": "does this already exist"},
    {"name": "MENTIONS", "shape": "circle", "count": 2, "r": 11},
    {"name": "back to source", "shape": "hex", "count": 1, "r": 17, "note": "the handle points home"},
]}, nxt=("Chapter 4. How the Concept System Is Managed", "Ch13.dc.html"))

# ---------------------------------------------------------------- Chapter 4
chapter(4, "vinc.book.13-ontology.en", "How the Concept System Is Managed", "right", [
    OPEN(4, "How the Concept System Is Managed", [
        "A graph needs kinds of node. It does not need every concept we use to become a kind of node.",
        "Miss that difference and the schema grows without end.",
        "Suppose the concepts in use are search quality, cost efficiency, Korean-language performance, reproducibility.",
    ], "Being important is not a reason to become a new kind of node."),
    B("4.1", "Kind and Concept Are Different Layers", [
        "A node kind names a role the system has to handle structurally differently. A concept names a meaning the user works with inside the system.",
        "So one node kind, Concept, can carry a very large number of different concepts.",
        "That is where the extensibility comes from. When a new technology or a new research subject appears, it can be expressed inside the existing Concept structure without changing the database schema.",
        "A schema change is a migration. A new concept is a write.",
    ]),
    B("4.2", "An Open Space of Meanings", [
        "The same word can carry different meanings, and different names can point at the same thing. String comparison alone cannot settle whether two concepts are the same.",
        "Embedding, vector representation, semantic vector do not always mean the same thing. In one context they point at one concept; in another they mark out different technical scopes.",
        "So the concept system has to live on a different layer from the graph schema.",
        "The schema defines the structure the system must understand. The concept system extends the space of meanings the system deals with.",
    ]),
    B("4.3", "The Two Layers Talk to Each Other", [
        "They are not independent. If a new kind of information keeps appearing and does not fit any existing node kind, that is a reason to look at the schema again.",
        "And if the existing schema is sufficient, there is no reason to build a new structure for a new meaning.",
        "Concept shows this boundary clearly. It is a node kind the system recognises structurally, while the titles of concepts are not restricted to a fixed list.",
        "So the structure of the graph stays comparatively stable while the meanings inside it keep expanding.",
    ], "Stable structure, growing meaning. The two are not the same axis."),
    B("4.4", "Extraction Needs a Boundary Too", [
        "Making concepts also needs a limit. Turn every noun and every phrase in a document into a concept and the graph grows fast, while meaningful memory and incidental wording become hard to tell apart.",
        "Restrict it too tightly and new meanings cannot be expressed at all.",
        "So extraction is not string collection. It is a judgment about which expressions are worth referring to independently in later work.",
        "The test is whether anything will ever point at this again.",
    ]),
    B("4.5", "A Title Is Not Understanding", [
        "A string becoming a node title does not mean the system has understood the concept.",
        "The actual meaning has to be read together with the document's context, the relationships attached, the evidence, and the time.",
        "So the concept system is wider than the graph. The graph provides a structure for storing and connecting concepts; it does not have to fix the whole of a human system of meanings into a schema.",
        "Because that boundary is held, the structure can stay stable while the meaning keeps extending.",
    ]),
    CLOSE(4, [
        "Structure and concepts are still not enough for a system to exchange memory reliably. The next chapter looks at the grammar that makes the exchange possible.",
    ]),
], conveyor={"y": 310, "x0": 120, "x1": 520, "stations": [
    {"name": "a phrase", "shape": "circle", "count": 1, "r": 14},
    {"name": "worth reusing?", "shape": "circle", "count": 1, "r": 14, "note": "will anything point at this again"},
    {"name": "Concept", "shape": "circle", "count": 1, "r": 16, "note": "one kind, many meanings"},
    {"name": "context", "shape": "hex", "count": 3, "r": 9},
    {"name": "relationships", "shape": "diamond", "count": 3, "r": 10},
    {"name": "meaning", "shape": "circle", "count": 3, "r": 10, "scatter": True, "note": "not the title alone"},
]}, nxt=("Chapter 5. What Grammar the System Remembers By", "Ch14.dc.html"))

# ---------------------------------------------------------------- Chapter 5
chapter(5, "vinc.book.14-graph-md.en", "What Grammar the System Remembers By", "left", [
    OPEN(5, "What Grammar the System Remembers By", [
        "Structure and concepts alone do not let a system pass memory back and forth reliably.",
        "A person can read around an error from context. A system has to be told explicitly which node this is, which fields are required, and which relationships are permitted.",
        "That is what the system's grammar is for.",
    ], "Grammar here is not a data format. It is what makes an exchange mean the same thing on both ends."),
    B("5.1", "What the Grammar Covers", [
        "It covers which kinds of node exist, what structure each kind has, which edges exist, what direction an edge has, and between which kinds that relationship is allowed.",
        "The important kinds are the ones Chapter 1 named. A Concept is a unit of meaning referred to repeatedly. An Episode is an event or an experience. A Record is an observation or a confirmed fact. A Decision is a choice or a judgment about something.",
        "Relationships have a fixed grammar too. Relates-to can express general association, while prerequisite-of is directed. Derives-from expresses derivation and inherits-from expresses succession. About says what a memory is a memory of.",
    ]),
    B("5.2", "The Name Alone Is Not Enough", [
        "So getting the relationship name right is not sufficient.",
        "Connecting prerequisite-of from A to B and connecting the same relationship from B to A produce different structures, and only one of them is the claim that was meant.",
        "Whether that relationship is allowed between those two kinds of node matters as well.",
        "A grammar that accepts anything is not a grammar; it is a suggestion, and the reader on the other end has no way to know which reading was intended.",
    ]),
    B("5.3", "Errors Are Not Quietly Removed", [
        "The grammar must not silently delete what it does not understand.",
        "Inventing a value for a missing required field, ignoring an unknown field without a word, or storing a malformed relationship with no marker all create problems later, in search and in interpretation.",
        "So the purpose of validation is not to reject data.",
        "It is to keep clear what the system knows and what it does not. A field that was dropped without a warning is worse than a field that was refused, because the refusal is visible and the drop is not.",
    ], "What the system does not know has to stay visibly unknown."),
    B("5.4", "Reading and Writing Share the Grammar", [
        "The same principle holds on the read side.",
        "If the write path uses a particular node and edge grammar, the read path has to understand the same structure.",
        "When the writing system and the reading system use different grammars, they can look at one graph and come away with two different memories, and nothing in either result says so.",
        "That is the failure this layer exists to prevent, and it is why the grammar is written down rather than carried in the code of whichever client wrote last.",
    ]),
    B("5.5", "Fragment Is Not the Graph", [
        "A Fragment is the unit used to carry this structure between systems. The graph is where that memory persists.",
        "Keeping the two apart matters. A Fragment is not the memory itself; it is a representation for moving memory across a boundary.",
        "And grammar does not stand in for meaning either. It defines how a system should read and write. The meaning is built from the contents of nodes, their relationships, and the documents and evidence behind them.",
        "Because the grammar exists, the system can grow: a new agent or a new client that understands the same structure can connect to the same memory.",
    ], "The grammar is the boundary between memory and whatever is running today."),
    CLOSE(5, [
        "The next chapter follows the whole path a document takes on the way to becoming nodes.",
    ]),
], conveyor={"y": 300, "x0": 110, "x1": 530, "stations": [
    {"name": "payload", "shape": "hex", "count": 1, "r": 15, "note": "a Fragment, not the graph"},
    {"name": "kinds checked", "shape": "circle", "count": 2, "r": 11},
    {"name": "direction checked", "shape": "diamond", "count": 2, "r": 11, "note": "A to B is not B to A"},
    {"name": "unknown fields", "shape": "hex", "count": 2, "r": 10, "note": "refused, never dropped"},
    {"name": "merged", "shape": "circle", "count": 2, "r": 12},
    {"name": "readable back", "shape": "circle", "count": 2, "r": 12, "note": "same grammar both ways"},
]}, nxt=("Chapter 6. How a Document Becomes Nodes", "Ch15.dc.html"))


for ch in CH:
    p = os.path.join(HERE, "ch", os.path.basename(ch["file"]).replace(".dc.html", "").lower() + ".json")
    p = os.path.join(HERE, "ch", f"ch{ch['n']}.json")
    json.dump(ch, open(p, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=2)
    print("wrote", os.path.basename(p), "part1 ch", ch["no"])
