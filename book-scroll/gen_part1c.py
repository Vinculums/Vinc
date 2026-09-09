#!/usr/bin/env python3
"""Part 1, chapters 13 to 19 (slots 21b, 22, 23, 24, 25, 26, 27).

Chapters 15 and 18 deliberately reuse the two clocks and the cone from
Part 0 chapter 6, in the pipeline register rather than the narrative one,
so a reader recognises them as a return rather than a new idea
(addendum section 6, Part 1 sampled).
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
CH = []


def chapter(no, key, slot, title, side, beats, conveyor=None, stage=None, nxt=None):
    d = dict(n=key, no=no, part=1, file=f"Ch{key}.dc.html", slot=slot, title=title,
             side=side, viewBox="0 0 640 640", beats=beats, marks=[], lines=[], anchors=[])
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


# --------------------------------------------------------------- Chapter 13
chapter(13, "21b", "vinc.book.21b-role-contract.en", "How a Role Becomes a Contract", "left", [
    OPEN(13, "How a Role Becomes a Contract", [
        "Chapter 12 made an agent a node on the graph. A search agent and an evaluation agent each hold an identity, and that identity is separate from the process running it.",
        "But identity alone is not enough.",
        "Calling an agent the search one and settling what it actually has to read and must not do are different problems.",
    ], "A name points at a role. It does not say what the role demands."),
    B("13.1", "Mission Comes First", [
        "A role node needs a mission explaining what that role has to do.",
        "Two agents reading the same repository behave completely differently if their purposes differ. One implements and changes contracts. One confirms whether that change satisfies the requirements.",
        "That difference is not a matter of writing the instruction sentence differently. One side has permission to change something and the other must not change anything and only check.",
        "With the distinction recorded as a mission, it becomes possible afterwards to work out why an agent behaved as it did, or why it did not.",
    ]),
    B("13.2", "Reading Order Is Part of It", [
        "Having access to a lot of material does not mean the material may be read in any order.",
        "Some roles have a document that has to be confirmed first, then a particular relationship to inspect, then a condition to verify last.",
        "Pin the reading order down as part of the role and agents can hold different traversal paths over the same graph without either being wrong.",
        "An order that lives only in a person's head is an order the next agent will not follow.",
    ]),
    B("13.3", "A Gate to Pass", [
        "Finishing work is not the same as the work being done.",
        "Each role can have a condition to pass before the next stage begins, and either it passes or it stays where it is and gets corrected.",
        "Without that gate, a report saying the work happened and the fact that the work finished are indistinguishable.",
        "And an agent that reports completion is exactly as convincing when it is wrong as when it is right, which is why the gate is not optional.",
    ], "A report of completion is not a completion."),
    B("13.4", "Several Roles Make a Flow", [
        "Real work rarely ends inside one role. It moves through research, design, implementation, review, delivery.",
        "If each stage has a responsible role, the order itself can be expressed as relationships in the graph.",
        "The arrow is not simply whose turn is next. It holds the fact that the later stage depends on the earlier one.",
        "Writing that flow into the graph does not mean the graph enforces the order. The graph explains who is responsible for what and how the responsibility carries; the runner decides when and how often.",
    ]),
    B("13.5", "Read, Render, Compare", [
        "There is a distance between a role existing in the graph and the execution environment moving the way that role says. Closing it takes three motions.",
        "Read the role from the graph. Render it into the form the runner can use. Then check that what was rendered still says the same thing as the graph's original.",
        "Keep those separate and the graph is the standard while the generated file is a derived artifact.",
        "A role also needs a fence: open every document to an agent without limit and the boundary defined at the start stops meaning anything, and the role itself goes blurry.",
    ], "The graph keeps the contract. The runner acts on it. A person or a check says it may proceed."),
    CLOSE(13, [
        "What remains is the relationships those roles create, and the evidence that attaches to each one.",
    ]),
], conveyor={"y": 300, "x0": 108, "x1": 532, "stations": [
    {"name": "a name", "shape": "circle", "count": 1, "r": 13},
    {"name": "mission", "shape": "circle", "count": 1, "r": 15, "note": "change, or only confirm"},
    {"name": "reading order", "shape": "hex", "count": 3, "r": 9, "note": "first, then, last"},
    {"name": "gate", "shape": "diamond", "count": 1, "r": 14, "note": "pass, or stay"},
    {"name": "rendered", "shape": "circle", "count": 1, "r": 13},
    {"name": "compared back", "shape": "ring", "count": 1, "r": 15, "note": "still the same contract"},
]}, nxt=("Chapter 14. How Evidence Attaches to a Relationship", "Ch22.dc.html"))

# --------------------------------------------------------------- Chapter 14
chapter(14, 22, "vinc.book.22-provenance.en", "How Evidence Attaches to a Relationship", "right", [
    OPEN(14, "How Evidence Attaches to a Relationship", [
        "As a graph grows, accurate nodes stop being enough. The fact that a relationship exists between two nodes has to be trustworthy as well.",
        "One line saying that A attaches to B gives no way to know why the relationship is there.",
        "So a relationship needs grounds too.",
    ], "A relationship existing and a relationship being evidenced are not the same state."),
    B("14.1", "Edge and Evidence Are Different Information", [
        "The relationship itself is structure. Evidence is the material that supports it.",
        "So a relationship can be thought of in at least two layers: the relation, and the evidence for the relation.",
        "With evidence present, the graph can go past saying these are connected and point at which sentence supports the connection.",
        "Without it, the edge is an assertion by whoever wrote it, and there is no way to review that assertion later.",
    ]),
    B("14.2", "Relationships Have Origins Too", [
        "If a concept was extracted from a document, a connection between the document and the concept comes into being.",
        "If that concept then connects to a record, another relationship appears.",
        "At that point it has to be possible to walk the chain backwards: from the answer material to the record, to the concept, to the document, to the source text.",
        "That backwards walk is what provenance is. It is not a list of sources; it is a path that can be traversed.",
    ]),
    B("14.3", "Not Every Relationship Is Equally Certain", [
        "Some relationships have direct grounds in the source text. Some are generated by the structure of the system. Some are simply not verified yet.",
        "So the existence of a relationship and the grounds for it have to be separable.",
        "That separation matters when reading the graph, because an unverified edge and a well-supported one look identical on screen otherwise.",
        "Evidence explains why the connection was made. Confidence expresses how much trust is placed in it.",
    ]),
    B("14.4", "Evidence and Confidence Are Not One Field", [
        "Merge the two and the grounds and the judgment about the grounds get mixed together.",
        "So in the relationship data they stay separate fields, and a reader can ask either question independently: what is the evidence, and how much weight was put on it.",
        "Extraction is convenient and automatic generation does not make a relationship a fact. A long document title or an ambiguous phrase can connect to the wrong concept without anything looking broken.",
        "So the generation step and the verification step are kept apart: a source produces a candidate relationship, and only a checked one becomes usable.",
    ], "Automatic generation is a proposal. It is not a finding."),
    B("14.5", "Evidence Becomes Part of the Graph", [
        "This structure matters more the more the graph is automated.",
        "With provenance in place, an agent's answer can be reviewed. Without it, a result can only be believed or discarded.",
        "So evidence does not live only in documents. Because a relationship itself can carry grounds, the graph extends from a list of connections into a knowledge structure with support underneath it.",
        "Node, edge, evidence, confidence. That combination is what later chapters on time and view build on.",
    ]),
    CLOSE(14, [
        "The next chapter takes up the time that has been deferred since chapter 1.",
    ]),
], conveyor={"y": 300, "x0": 108, "x1": 532, "stations": [
    {"name": "two nodes", "shape": "circle", "count": 2, "r": 12},
    {"name": "an edge", "shape": "diamond", "count": 1, "r": 13, "note": "structure"},
    {"name": "evidence", "shape": "hex", "count": 1, "r": 12, "note": "which sentence supports it"},
    {"name": "confidence", "shape": "circle", "count": 1, "r": 11, "note": "a separate field"},
    {"name": "checked", "shape": "circle", "count": 1, "r": 13},
    {"name": "traversable back", "shape": "hex", "count": 3, "r": 9, "note": "material to source"},
]}, nxt=("Chapter 15. What Time Exists in Memory", "Ch23.dc.html"))

# --------------------------------------------------------------- Chapter 15
chapter(15, 23, "vinc.book.23-temporal.en", "What Time Exists in Memory", "left", [
    OPEN(15, "What Time Exists in Memory", [
        "Time in memory is not one thing.",
        "When the event a record describes happened, and when that record entered the graph, are different questions.",
        "VINC splits them into world time and graph time.",
    ], "The two cannot substitute for one another, and one timestamp cannot hold both."),
    B("15.1", "World Time and Graph Time", [
        "World time is the time of the thing the memory points at. If a concept or a record describes an event or a fact, its content time expresses when that thing occurred.",
        "That value can differ from the moment the record was stored.",
        "Graph time is when the object was created and changed inside the graph: created, updated, revision, sequence.",
        "World time answers what happened. Graph time answers when the graph learned it, or changed its mind about it.",
    ]),
    B("15.2", "Mixing the Clocks Produces Errors", [
        "Suppose an event on the twentieth of August was recorded on the first of September.",
        "A record being recently created in the graph does not mean the event happened recently.",
        "And a record describing an old event being edited recently does not mean the event happened again.",
        "Both mistakes are easy to make and neither announces itself, because the surviving value looks like a perfectly ordinary date.",
    ], "A recent write is not a recent event."),
    B("15.3", "A Claim Can Have a Validity Window", [
        "If content time says when something occurred, a validity range says from when until when the claim holds.",
        "Prices and policies were true at a particular time and may no longer be true now, so the time of the event and the window of the claim have to be told apart.",
        "Decisions cannot be treated the same way either. A decision does not carry a content time; its important time is the one tracked in the graph.",
        "That is because a decision represents a judgment made inside the system rather than an object describing an external event.",
    ]),
    B("15.4", "Kinds Have Different Clocks", [
        "So the time model differs by kind of object, and that has to be admitted rather than smoothed over.",
        "An episode is for recalling what happened in time order, so its core is position on a timeline.",
        "Apply the graph's creation time to an episode as if it were the event's time and the meaning gets confused.",
        "Forcing one identical set of time fields onto every node is therefore not the correct design, however much tidier it looks in a schema.",
    ]),
    B("15.5", "Causal Time Is Another Problem", [
        "Two events being arranged one after the other does not make one the cause of the other. A happened before B and A caused B are different claims.",
        "Prerequisite-of, derives-from and inherits-from carry a structural meaning stronger than a date comparison.",
        "A prerequisite-of B does not say A's date is earlier. It says A precedes B in establishing or understanding it.",
        "And time is not a viewpoint. It is handled as a state axis shared across views, because time restricts which data is in view rather than changing the camera.",
    ], "Order is not causation, and the graph has to be able to say which one it means."),
    CLOSE(15, [
        "The next chapter turns to what nodes and edges look like once they are on a screen.",
    ]),
], stage=[
    "  // The two clocks return here, in the pipeline register rather than the",
    "  // narrative one, so a reader recognises them from Part 0 chapter 6",
    "  // instead of meeting them as a new idea.",
    "  stage(s, p) {",
    "    const E = React.createElement, kids = [];",
    "    const X0 = 150, Y0 = 470, W = 300;",
    "    kids.push(E('line', { key: 'ax', x1: X0, y1: Y0, x2: X0 + W, y2: Y0, stroke: EDGE, strokeWidth: 1 }));",
    "    kids.push(E('line', { key: 'ay', x1: X0, y1: Y0, x2: X0, y2: Y0 - W, stroke: EDGE, strokeWidth: 1 }));",
    "    kids.push(E('text', { key: 'lx', x: X0 + W / 2, y: Y0 + 26, textAnchor: 'middle', fontSize: 12.5, fill: META }, 'world time'));",
    "    kids.push(E('text', { key: 'ly', x: X0 - 12, y: Y0 - W + 4, textAnchor: 'end', fontSize: 12.5, fill: META }, 'graph time'));",
    "    kids.push(E('line', { key: 'diag', x1: X0, y1: Y0, x2: X0 + W, y2: Y0 - W, stroke: FADE, strokeWidth: 1 }));",
    "    // the off-diagonal record: an old event written down recently",
    "    if (s >= 1) {",
    "      const t = s > 1 ? 1 : smooth(clamp(p * 1.2, 0, 1));",
    "      const x = X0 + 70, y = lerp(Y0 - 70, Y0 - 240, t);",
    "      kids.push(E('path', { key: 'r1', d: blob(x, y, 10, K), fill: INK }));",
    "      kids.push(E('line', { key: 'g1', x1: x, y1: Y0 - 70, x2: x, y2: y, stroke: EDGE, strokeWidth: 1, strokeLinecap: 'round' }));",
    "      kids.push(E('text', { key: 't1', x: x + 16, y: y - 6, fontSize: 12.5, fontStyle: 'italic', fill: INK }, 'recorded long after it happened'));",
    "    }",
    "    // the on-diagonal record: the two times equal",
    "    if (s >= 3) {",
    "      kids.push(E('path', { key: 'r2', d: blob(X0 + 220, Y0 - 220, 10, K), fill: INK }));",
    "      kids.push(E('text', { key: 't2', x: X0 + 236, y: Y0 - 226, fontSize: 12.5, fontStyle: 'italic', fill: INK }, 'both the same'));",
    "    }",
    "    // beat 5, order against causation: same two marks, two readings",
    "    if (s >= 5) {",
    "      const a = { p: [190, 150], r: 12 }, b = { p: [430, 150], r: 12 };",
    "      const g = linkPath(a, b, -1, true);",
    "      kids.push(E('path', { key: 'ca', d: blob(a.p[0], a.p[1], a.r, K), fill: INK }));",
    "      kids.push(E('path', { key: 'cb', d: blob(b.p[0], b.p[1], b.r, K), fill: INK }));",
    "      kids.push(E('path', { key: 'cl', d: g.d, fill: 'none', stroke: INK, strokeWidth: 1,",
    "        strokeLinecap: 'round', strokeLinejoin: 'round', pathLength: 1, strokeDasharray: 1,",
    "        style: { strokeDashoffset: 0 } }));",
    "      kids.push(E('text', { key: 'ct', x: 310, y: 106, textAnchor: 'middle', fontSize: 12.5,",
    "        fontStyle: 'italic', fill: INK }, 'prerequisite of, not merely earlier'));",
    "    }",
    "    return E('g', { key: 'clocks' }, kids);",
    "  }",
], nxt=("Chapter 16. How Nodes and Edges Become Visible", "Ch24.dc.html"))

# --------------------------------------------------------------- Chapter 16
chapter(16, 24, "vinc.book.24-view.en", "How Nodes and Edges Become Visible", "right", [
    OPEN(16, "How Nodes and Edges Become Visible", [
        "A graph is a data structure, and a user does not read database tables. A user reads the shape of a node and the line of an edge.",
        "So visualising a graph needs a grammar of its own.",
        "Node type maps to shape. Edge kind maps to a line language.",
    ], "The mapping matters more than the particular shapes chosen."),
    B("16.1", "Type Carries Into Shape", [
        "Concept, record, decision, episode and bridge mean different things, and telling them apart on screen needs an explicit mapping between type and shape.",
        "What matters is not which shape is which. It is that the mapping exists and holds.",
        "Change the shapes and the same type still has to keep the same meaning.",
        "Draw every relationship with the same line and the structural differences in the graph disappear, so relationship kinds get different visual treatment too.",
    ]),
    B("16.2", "Direction Is Information", [
        "A line from A to B and a line between A and B say different things.",
        "An arrowhead, or the direction of a line, is not decoration. It is part of the graph's meaning, and a diagram that drops it has dropped a claim.",
        "A bridge is different again from a plain node-to-node connection. Drawing it with its own treatment lets a user tell a direct relationship from a special one that joins separate structures.",
        "That distinction matters most where different domains meet, which is exactly where a reader is least able to guess.",
    ]),
    B("16.3", "A View Is More Than Coordinates", [
        "Placing a graph on screen needs coordinates, but a view is not an object that stores coordinates.",
        "Its axes are scope, viewpoint, node shape, edge kind, coordinates, density, time, focus and interaction.",
        "So the same graph produces completely different screens depending on which scope and which viewpoint were chosen.",
        "Scope decides where you are looking. Viewpoint decides how. Scope is kept in the path and the address; viewpoint is a separate setting.",
    ], "Changing the viewpoint does not move the scope you are looking at."),
    B("16.4", "Several Viewpoints, One Graph", [
        "In the current design 3D, 2D, timeline and weave each do real work, and each answers a different question.",
        "3D asks how the centre and its surroundings look. 2D asks how the same structure reads on a plane. Timeline asks how things arrange along time. Weave asks how relationships and topics bundle together.",
        "These are the same graph read against different questions, not four graphs.",
        "Which is why the count of marks does not change when the viewpoint does.",
    ]),
    B("16.5", "Declaring a Grammar Is Not Keeping It", [
        "Defining a visual grammar and having the renderer preserve it are separate problems.",
        "A shape vocabulary can be written down and the screen can still show every kind of node collapsed into roughly the same blob.",
        "That risk points at the principle: a visual language is not finished by being declared in a document. The renderer has to implement the same contract.",
        "A view is not decoration. Which relationships get emphasised and which get pushed into the background changes how a user reads the graph.",
    ]),
    CLOSE(16, [
        "The next chapter asks where that graph lives, and who it belongs to.",
    ]),
], conveyor={"y": 300, "x0": 108, "x1": 532, "stations": [
    {"name": "types", "shape": "circle", "count": 4, "r": 10},
    {"name": "shapes", "shape": "diamond", "count": 4, "r": 10, "note": "an explicit mapping"},
    {"name": "direction", "shape": "diamond", "count": 2, "r": 11, "note": "part of the meaning"},
    {"name": "scope", "shape": "circle", "count": 1, "r": 14, "note": "where"},
    {"name": "viewpoint", "shape": "ring", "count": 1, "r": 15, "note": "how"},
    {"name": "renderer", "shape": "circle", "count": 4, "r": 10, "scatter": True, "note": "or the grammar collapses"},
]}, nxt=("Chapter 17. How Graph Space Gets Created", "Ch25.dc.html"))

# --------------------------------------------------------------- Chapter 17
chapter(17, 25, "vinc.book.25-space.en", "How Graph Space Gets Created", "left", [
    OPEN(17, "How Graph Space Gets Created", [
        "A graph does not hold only nodes and edges. Who owns it, what range of data it holds, and who may reach it are part of the graph too.",
        "VINC expresses that boundary as a space.",
        "The point is that a space is less a complicated graph object than a boundary determined by ownership.",
    ], "Space is not a screen setting. It is part of the data model and the permission model."),
    B("17.1", "Personal and Team", [
        "There can be a personal graph and a team graph.",
        "What matters is that the two are not different folders in one store. When the owner differs, the boundary of memory differs.",
        "And when a tool is called, which space it runs in has to be unambiguous.",
        "The argument can be passed explicitly; omitted, it defaults to the personal graph. That default exists for convenience, which means a write meant for a team can land somewhere else if nobody says otherwise.",
    ]),
    B("17.2", "An Address Can Decide It Too", [
        "At a team endpoint, the address itself indicates the space.",
        "Then the URL decides which graph is being entered and the credential proves the right to enter it.",
        "Address answers which graph. Credential answers whether this caller may go in. Keeping those two questions apart is what makes the boundary auditable.",
        "Conflate them and a valid credential starts to look like permission for every graph it could reach.",
    ]),
    B("17.3", "Owner Isolation", [
        "If graphs from different owners mix, the reliability of the whole memory system collapses.",
        "So search and traversal have to run inside the same owner range. A search by one owner must not contain another owner's nodes.",
        "That is not only a security problem. It is a search-accuracy problem: a foreign node in a result set is a wrong answer even when nothing was leaked.",
        "Space also affects the boundary of synchronisation. Graphs of the same owner can converge across environments; graphs of different owners must never be merged into one sync range.",
    ], "A result that mixes owners is wrong before it is unsafe."),
    B("17.4", "Membership Is Not Role", [
        "Being able to reach a team graph does not mean being able to do everything in it.",
        "A team has roles: viewer, editor, admin. That role decides what may be done after entry, separately from whether entry is allowed.",
        "So authentication, space and role must not be collapsed into one check.",
        "Three questions: who is this, which graph, and what may they do there. Answering two of them and guessing the third is how a write ends up somewhere nobody intended.",
    ]),
    B("17.5", "Knowing Which Graph You Are In", [
        "As the space structure grows, a caller has to be able to find out which graph it is reaching.",
        "The surface that answers that reports the identity, the spaces available on this connection, and the role in each.",
        "It is not a convenience. Call a tool without knowing which spaces exist and a read or a write can land on the wrong graph.",
        "So a space decides how far the graph counts as one memory: owner, nodes, relations, documents, search and permissions.",
    ]),
    CLOSE(17, [
        "The next chapter returns to causal order, and asks how it becomes a space.",
    ]),
], conveyor={"y": 300, "x0": 108, "x1": 532, "stations": [
    {"name": "a call", "shape": "circle", "count": 1, "r": 13},
    {"name": "identity", "shape": "circle", "count": 1, "r": 12, "note": "who is this"},
    {"name": "space", "shape": "ring", "count": 1, "r": 15, "note": "which graph"},
    {"name": "role", "shape": "diamond", "count": 1, "r": 13, "note": "what may they do"},
    {"name": "owner range", "shape": "circle", "count": 3, "r": 10},
    {"name": "one memory", "shape": "circle", "count": 3, "r": 10, "note": "search, docs, permissions"},
]}, nxt=("Chapter 18. How Causal Relationships Become Space", "Ch26.dc.html"))

# --------------------------------------------------------------- Chapter 18
chapter(18, 26, "vinc.book.26-sync.en", "How Causal Relationships Become Space", "right", [
    OPEN(18, "How Causal Relationships Become Space", [
        "An edge is not a connecting line. Some relationships express only that two nodes are connected; some express that one node has to exist before the other structurally.",
        "Use that difference and the graph's causal structure can be read spatially.",
        "In A prerequisite-of B, what matters is not the coordinates of A and B. It is the order between them.",
    ], "Relationships make order. Order makes coordinates. Not the other way round."),
    B("18.1", "Time Order Is Not Causal Order", [
        "Two events recorded at ten and eleven o'clock do not establish that the first caused the second.",
        "A happened before B and A is required for B are different propositions.",
        "Two decisions made on the same day do not make one a premise of the other. Two decisions months apart, where the later one took the earlier as its basis, are related by prerequisite rather than by date.",
        "So ordinary time information and causal relationships have to be stored separately in the graph.",
    ]),
    B("18.2", "Causal Order Is a Partial Order", [
        "Prerequisite-of, derives-from and inherits-from can be read as the relationships that create causal order.",
        "The goal is not to sort the whole graph into one line. Some nodes have an order between them and some have no direct order at all. That is a partial order.",
        "A can precede B and C, and B and C can both precede D, without the structure saying anything about which of B and C came first.",
        "Pretending otherwise would invent an order the data does not contain.",
    ]),
    B("18.3", "The Cone, Read Again", [
        "From a given node, two directions can be considered. Upstream holds what precedes it in establishing it. Downstream holds what derives from it or follows as a structural consequence.",
        "That region can be read as a causal cone, and it is the same picture Part 0 used as a borrowed lens.",
        "It should not be read as the simple before-and-after regions of a time axis. A came before B and A is a precondition of B are different relationships, and the cone follows the second.",
        "So causal structure and world time can be related without being the same thing.",
    ], "The same picture returns, and it is a return rather than a new idea."),
    B("18.4", "No Metric Is Invented", [
        "Computing coordinates between nodes does not create a physical distance between them.",
        "Distance is not semantic truth and geometric distance is not causal strength. Coordinates can be placement values for a view; relationships express the graph's meaning.",
        "So spatialisation needs two layers. Graph semantics produce a causal order, the order produces layout constraints, and constraints produce coordinates.",
        "Layout can visualise causal order. Layout must not create new causal order, and that principle gets more important the more elaborate the visualisation becomes.",
    ]),
    B("18.5", "Two Spaces, Complementary", [
        "Selecting a node lets its upstream and downstream be traversed, which is stronger than a one-hop neighbourhood: what had to precede this, and what came out of it, read together.",
        "Embedding finds sentences and nodes that are semantically near. A causal graph finds what structurally precedes or derives.",
        "Semantic space is similarity of meaning. Causal space is structural order. Two nodes near in meaning are not guaranteed to be causally connected, and two causally connected nodes need not be near in an embedding.",
        "So one coordinate system does not explain every meaning, and the screen is a projection of several structures at once.",
    ]),
    CLOSE(18, [
        "The last chapter of Part 1 asks what the density of that projection actually shows.",
    ]),
], stage=[
    "  // The cone returns here, given the same treatment as Part 0 chapter 6",
    "  // so the reader recognises it. Hairlines, no shading: the book names it",
    "  // as a borrowed lens rather than an implementation.",
    "  APEX = [320, 300];",
    "  stage(s, p) {",
    "    const E = React.createElement, kids = [], A = this.APEX;",
    "    const open = s >= 3 ? (s > 3 ? 1 : smooth(clamp(p * 1.15, 0, 1))) : 0;",
    "    // beats 0 to 2: the partial order, as an order and not yet as a space",
    "    if (s <= 2) {",
    "      const N = { a: [320, 130], b: [200, 270], c: [440, 270], d: [320, 410] };",
    "      const put = (k, q) => kids.push(E('path', { key: k, d: blob(q[0], q[1], 12, K), fill: INK }));",
    "      Object.entries(N).forEach(([k, q]) => put(k, q));",
    "      [['a', 'b'], ['a', 'c'], ['b', 'd'], ['c', 'd']].forEach(([x, y], i) => {",
    "        const g = linkPath({ p: N[x], r: 12 }, { p: N[y], r: 12 }, i % 2 ? 1 : -1, true);",
    "        kids.push(E('path', { key: 'e' + i, d: g.d, fill: 'none', stroke: s >= 1 ? INK : EDGE,",
    "          strokeWidth: 1, strokeLinecap: 'round', strokeLinejoin: 'round',",
    "          pathLength: 1, strokeDasharray: 1, style: { strokeDashoffset: 0 } }));",
    "      });",
    "      kids.push(E('text', { key: 'po', x: 320, y: 470, textAnchor: 'middle', fontSize: 13,",
    "        fontStyle: 'italic', fill: INK },",
    "        s >= 2 ? 'B and C have no order between them' : 'order, not coordinates'));",
    "    }",
    "    // beats 3 onward: the cone",
    "    if (s >= 3) {",
    "      const spread = 150 * open;",
    "      kids.push(E('line', { key: 'ax', x1: 96, y1: A[1], x2: 544, y2: A[1], stroke: EDGE, strokeWidth: 1 }));",
    "      kids.push(E('path', { key: 'up', d: `M${A[0] - spread},${A[1] - 150} L${A[0]},${A[1]} L${A[0] + spread},${A[1] - 150}`,",
    "        fill: 'none', stroke: INK, strokeWidth: 1, strokeLinecap: 'round', strokeLinejoin: 'round' }));",
    "      kids.push(E('path', { key: 'dn', d: `M${A[0] - spread},${A[1] + 150} L${A[0]},${A[1]} L${A[0] + spread},${A[1] + 150}`,",
    "        fill: 'none', stroke: INK, strokeWidth: 1, strokeLinecap: 'round', strokeLinejoin: 'round' }));",
    "      kids.push(E('path', { key: 'apex', d: blob(A[0], A[1], 8, K), fill: INK }));",
    "      kids.push(E('text', { key: 'ut', x: A[0], y: A[1] - 168, textAnchor: 'middle', fontSize: 12.5,",
    "        fontStyle: 'italic', fill: INK, style: { opacity: open > 0.6 ? 1 : 0,",
    "          transition: `opacity ${T_MARK}ms ${ENTER}` } }, 'upstream · what had to precede'));",
    "      kids.push(E('text', { key: 'dt', x: A[0], y: A[1] + 186, textAnchor: 'middle', fontSize: 12.5,",
    "        fontStyle: 'italic', fill: INK, style: { opacity: open > 0.6 ? 1 : 0,",
    "          transition: `opacity ${T_MARK}ms ${ENTER}` } }, 'downstream · what derived'));",
    "      if (s >= 5) {",
    "        kids.push(E('text', { key: 'sp', x: A[0], y: A[1] + 232, textAnchor: 'middle',",
    "          fontSize: 12.5, fill: META }, 'semantic nearness is a different space'));",
    "      }",
    "    }",
    "    return E('g', { key: 'cone' }, kids);",
    "  }",
], nxt=("Chapter 19. What Density and Layout Show", "Ch27.dc.html"))

# --------------------------------------------------------------- Chapter 19
chapter(19, 27, "vinc.book.27-infrastructure.en", "What Density and Layout Show", "left", [
    OPEN(19, "What Density and Layout Show", [
        "Put a graph on screen and more than position becomes visible. How much has gathered in one area becomes visible too.",
        "Which raises the question: what does it mean that many nodes are clustered here?",
        "There is more than one answer. It could be the size of a topic, an area where relationships concentrated, or simply a result the layout algorithm produced.",
    ], "So density and layout have to be understood separately."),
    B("19.1", "Position Is Not Meaning", [
        "Node A being near node B does not mean the two meanings are necessarily close.",
        "That nearness can arise from semantic similarity, from topic grouping, from graph connectivity, or from layout optimisation.",
        "So a meaning relationship must not be inferred from screen distance alone.",
        "Density is closer to a property of a set: how much structure has gathered inside a region. Which is a description, not an explanation.",
    ]),
    B("19.2", "Topics Make Natural Density", [
        "Domain separates nodes by subject, so arranging nodes of the same topic into one region makes a density by topic appear.",
        "That is different information from the semantic similarity of a search result. It is a spatial organisation showing topic structure.",
        "A study looked at whether node distribution by topic could be modelled as a Gaussian: treat each topic as a distribution, place nodes around it, and clusters form naturally.",
        "The approach could express density and clustering together rather than placing bare points.",
    ]),
    B("19.3", "It Was Not Adopted as the Layout", [
        "The result was that the Gaussian layout was not adopted as the default.",
        "The reason is that merging the role of layout with the role of density can blur the structure the view was trying to keep.",
        "Layout answers where a node goes. Density answers where structure is concentrated. The two do not have to be handled by one algorithm.",
        "The more useful direction from the study was to use the distribution as a density layer rather than as a placement algorithm.",
    ], "One algorithm doing two jobs does neither of them legibly."),
    B("19.4", "Density Does Not Explain Itself", [
        "Base coordinates come from the existing layout, and a distribution model expresses the density around them. Node structure and spatial concentration stay separable.",
        "Suppose an area is dense. That alone cannot decide whether it is one topic, many relationships, a concentration of search results, or an artifact of the algorithm.",
        "So density is a clue for interpretation and not the interpretation. Giving it meaning requires checking which nodes and edges make up the region.",
        "Nodes of one subject bunched to one side does not prove that subject was discussed more. They may simply have been added together recently and not yet spread.",
    ]),
    B("19.5", "Layout Should Be a Function", [
        "Rather than storing coordinates as arbitrary state, they should be treated as a function computed from graph state, scope, viewpoint and layout state.",
        "The same input should produce, as far as possible, the same arrangement, so a view can be reopened or rendered elsewhere and the spatial structure reproduced.",
        "Proximity does not create evidence for a relationship, and a dense area is not a more important topic.",
        "Layout shows structure, density shows concentration, and relationships decide meaning. Hold that and the space becomes an interface for exploring the graph without distorting it.",
    ], "Nearness is not similarity, and density is not importance."),
    CLOSE(19, [
        "Part 1 has built the structure. Part 2 asks whether any of it actually worked, and what it takes to measure that honestly.",
    ]),
], conveyor={"y": 300, "x0": 108, "x1": 532, "stations": [
    {"name": "graph state", "shape": "circle", "count": 3, "r": 10},
    {"name": "topic", "shape": "circle", "count": 5, "r": 9, "scatter": True},
    {"name": "layout", "shape": "circle", "count": 5, "r": 9, "note": "where a node goes"},
    {"name": "density", "shape": "circle", "count": 5, "r": 9, "scatter": True, "note": "where structure gathers"},
    {"name": "a function", "shape": "circle", "count": 5, "r": 9, "note": "same input, same arrangement"},
    {"name": "interpretation", "shape": "hex", "count": 3, "r": 10, "note": "check the nodes, not the picture"},
]}, nxt=("Part 2. Was the Design Actually Right", "Ch28.dc.html"))


for ch in CH:
    p = os.path.join(HERE, "ch", f"ch{ch['n']}.json")
    json.dump(ch, open(p, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=2)
    print("wrote", os.path.basename(p), "part1 ch", ch["no"])

# --- pad every beat the volume gate flagged, in one pass -------------------
PATCH = {
    "ch11.json": {"2.5": "A graph that draws every possible line has drawn no distinction at all."},
    "ch15.json": {
        "6.3": "An edge nobody wrote is an edge nobody can follow back.",
        "6.4": "Stability is the property that makes a second pass safe to run at all.",
        "6.5": "Neither side is a summary of the other, and that is what the rest of Part 1 rests on.",
    },
    "ch18.json": {
        "9.3": "Broad search and conservative resolution are not in tension; they are the same design.",
        "9.5": "An empty result that says it is empty is more useful than a full one that guessed.",
    },
    "ch19.json": {"10.1": "Search answers what is related. This answers what has to be read together."},
    "ch20.json": {"11.3": "A permission granted because nothing objected is a permission nobody chose."},
    "ch21.json": {"12.1": "A definition in a config file is a definition nothing else in the system can cite."},
}
for f, add in PATCH.items():
    path = os.path.join(HERE, "ch", f)
    d = json.load(open(path, encoding="utf-8"))
    for b in d["beats"]:
        if b.get("label") in add:
            b["paras"].append(add[b["label"]])
    json.dump(d, open(path, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=2)
    print("padded", f)
