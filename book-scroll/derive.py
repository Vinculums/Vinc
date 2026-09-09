#!/usr/bin/env python3
"""Derive the technical chapters' beats from snapshots of their graph documents.

  python -X utf8 derive.py             every slot that has src/<slot>.md
  python -X utf8 derive.py 16 18       only those slots
  python -X utf8 derive.py --part0     Part 0: drop motion data and the four drawing sentences

src/<slot>.md is a verbatim snapshot of vinc_doc_get(<doc id>) on the account graph; the
graph is canon and the snapshot is a build input, the same discipline as
scripts/graph-refs.json. Re-fetch it, never edit it. src/manifest.json names the document
behind each file.

Kept from the existing ch/<slot>.json: n, no, part, file, slot, title, side, viewBox, the
close beat (surface chrome, not document text), and a claim sentence wherever the same
sentence exists in the document. Everything about motion (marks, lines, anchors, figures,
stage, conveyor, hub, pair, ring, ground, focusAt, focusScrub) is dropped: v2 is discarded.

From the document: the intro is the open beat and every `## ` section is one beat. Fenced
code blocks stay on the beat as `diagrams` (never rendered; they are what the new motion has
to draw). A line set as «...» or **...** is the claim. Bullet lists and tables fold into one
paragraph. Em dashes are not allowed on screen, so " — " becomes a sentence break.
"""
import json, os, re, sys, glob, difflib

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
CH = os.path.join(HERE, "ch")
KEEP = ("n", "no", "part", "file", "slot", "title", "side", "viewBox")


def part_word(p):
    return "Closing" if p == 5 else f"Part {p}"


# A long intro (or a document with no `## ` headings at all) is split by hand:
# (index of the intro block that opens the new beat, its heading).
SPLITS = {
    "10": [(3, "Six Kinds, Six Questions"), (7, "Kinds Are Closed, Contents Are Not"),
           (9, "An Edge Is Not a Line"), (13, "Two Clocks and One Space"),
           (16, "The Source Text Is Kept Beside It")],
    "13": [(3, "Kind and Concept Are Different Layers")],
    "14": [(2, "What the Grammar Covers"), (5, "The Name Alone Is Not Enough")],
    "35": [(2, "Each Chapter Solved One Problem"), (4, "No Single Technology"),
           (5, "The Questions That Have to Survive"), (7, "Then It Is Not Data")],
}

# A section too long for one screen is cut into two beats at the paragraph that starts
# with the prefix; the second half gets the new heading.
SUBSPLITS = {
    ("11", "Why Six Kinds"): [("Take a single sentence", "One Sentence, Three Kinds")],
    ("13", "The Type Vocabulary Is Authored, Not Fixed"): [("The vocabulary lives outside the graph", "The Frame Lives Beside the Graph")],
    ("16", "From Candidate to Target"): [("So the retrieval flow runs longer", "The Flow Runs Longer Than Search")],
    ("16", "Different Languages Can Still Be the Same Memory"): [("So the goal of search isn't", "The Entrance to a Memory System")],
    ("32", "A Gate That Never Fires Looks Like a Gate That Always Skips"): [("It was found in September 2026", "Make It Say What Ran")],
    ("33", "A Filter That Manufactured Confidence"): [("The fix was not to weaken the filter", "Keep the Tie")],
}

# The claim: the one sentence a beat sets apart, the sentence the drawing has to carry.
# Keyed by the beat's heading ("open" for the opening beat); the value is the start of a
# sentence that exists in the beat, after clean(). A «...» or **...** line in the document
# wins over this table; a beat listed here whose sentence is not found is reported.
CLAIMS = {
    "10": {"open": "The core distinction is a set of Node kinds",
           "Six Kinds, Six Questions": "These kinds exist because not every memory",
           "Kinds Are Closed, Contents Are Not": "So VINC's Node model keeps the set of kinds closed",
           "An Edge Is Not a Line": "Recording that A is a prerequisite of B",
           "Two Clocks and One Space": "When an event actually happened and when that memory entered",
           "The Source Text Is Kept Beside It": "The graph is not a replacement copy of the document"},
    "11": {"A Node Is a Contract, Not a Label": "It's closer to a contract that decides in advance",
           "Why Six Kinds": "Each kind answers a different question",
           "One Sentence, Three Kinds": "Split them by kind, and you can go straight",
           "An Edge Carries Direction and Meaning Together": "That's why an Edge has both a name and a direction",
           "Allowed Pairings, and Pairings That Aren't": "A graph where everything connects to everything",
           "Why Split Node and Edge": "An Edge decides in what capacity those things meet"},
    "12": {"open": "If anything, the sturdier the graph gets",
           "The Same Sentence Isn't Always the Same Memory": "So the test for \"is this the same memory\"",
           "Document and Chunk Are Different Layers": "Stitch the Chunks back together",
           "Discovering Meaning Is Not the Same as Erasing the Original": "A Concept isn't a summary that replaces the document",
           "Finding an Existing Concept Matters More Than Making a New One": "So processing a new document always starts with a question",
           "What Splitting the Responsibility Buys You": "If the document is the original copy of a memory"},
    "13": {"open": "Being important concepts doesn't mean",
           "Kind and Concept Are Different Layers": "So a single Node kind (Concept) can express",
           "The Problem with an Open Concept Space": "So the concept system has to live on a different layer",
           "Concept Shows Where the Line Sits": "It's judging whether a given expression is worth referencing",
           "The Type Vocabulary Is Authored, Not Fixed": "The system proposes; it never quietly confirms",
           "The Frame Lives Beside the Graph": "A shared type name, like a similar title",
           "Storing a Name Isn't the Same as Understanding a Meaning": "The concept system is therefore broader than the graph"},
    "14": {"open": "A system has to know explicitly what kind of Node",
           "What the Grammar Covers": "Grammar here doesn't just mean a data format",
           "The Name Alone Is Not Enough": "Connecting A to B with prerequisite_of is a different structure",
           "Errors Don't Get Silently Erased": "It's to keep the system's picture of what it knows",
           "Fragment and Graph Are Not the Same Thing": "System grammar, then, is the boundary"},
    "15": {"open": "So processing a document isn't about replacing the original text",
           "Step One Is Preservation": "One condition matters here: the original has to be fully recoverable",
           "The Next Step Is Discovery": "But an extracted candidate isn't automatically a new memory",
           "Automatic Extraction Isn't the Same as Human-Refined Meaning": "When a new document arrives, the system shouldn't remake",
           "What \"Document to Node\" Actually Means": "Going from document to Node, in the end"},
    "16": {"open": "But a search result, by itself, is not an answer",
           "Search Isn't One Method": "Full-text finds matching wording; vector finds semantic proximity",
           "Two Lists Become One": "So the lists are merged by rank rather than by score",
           "A Third Reader, Only When the Two Disagree": "VINC runs it only when the two arms disagree",
           "From Candidate to Target": "The core principle here: when it's ambiguous, don't choose",
           "The Flow Runs Longer Than Search": "Search narrows down where memory might be",
           "Different Languages Can Still Be the Same Memory": "So a question in one language against a title in another",
           "The Entrance to a Memory System": "At this point search stops being a plain database function"},
    "17": {"open": "What matters here isn't the vector itself",
           "Chunks Have to Exist First": "So retrieval quality isn't decided by the embedding model alone",
           "Embedding Is a Transformation into Semantic Space": "That's what makes vector search different from plain string matching",
           "Vector Dimension Is Part of the Storage Contract": "The embedding model, the vector dimension, and the vector store",
           "A Vector Is Not Memory Itself": "Treating a vector as identical to memory",
           "Retrieval Quality Isn't Decided by the Model Alone": "Embedding, then, is one stage of the retrieval pipeline",
           "The Cost of Changing a Model": "This isn't a model-selection problem",
           "The Boundary Between Vectors and Meaning": "A vector isn't the final form memory takes"},
    "18": {"open": "It's deciding which one should actually be interpreted",
           "Candidates Arrive Through Different Paths": "What matters is that these candidates aren't all the same shape",
           "Candidate and Resolution Are Different States": "If two candidates look equally plausible",
           "The Pool Is the Ceiling": "A ranking change, however clever, reorders the pool",
           "Material Gets Pulled From the Resolved Node": "A search result stops being just a list of text",
           "A Record Is a Different Kind of Object Than a Concept": "After resolution, these different objects still have to be read",
           "Episodes Are Candidates Too, and Name a Different Thing": "A Concept answers what something means; an Episode answers",
           "Language Differences Are Also a Post-Search Problem": "A title in another language that never enters the pool",
           "Search Failure Is Also a Valid State": "So VINC's retrieval structure needs both the ability to find"},
    "19": {"open": "That selected, connected set of memory can be called answer material",
           "Brief Isn't Just Another Name for Search": "Brief asks something slightly different",
           "The Resolved Node Sits at the Center of the Material": "Material construction is centered not on the question itself",
           "Graph and Document Provide Different Information": "So building material means connecting the graph and the document",
           "More Material Is Not Always Better": "Pull in every possible connection while building material",
           "When Material Is Insufficient, Say So": "In that case, insufficiency itself becomes the result",
           "Answer Material Is an Intermediate Layer": "It turns memory into material an Agent can actually use"},
    "20": {"open": "The MCP layer doesn't decide the meaning of memory here",
           "A Thin Bridge": "Changes to the door and changes to the graph's rules",
           "Two Channels, One Dispatcher": "The core idea here is keeping the channel separate",
           "MCP Is VINC's Boundary, Not VINC Itself": "Change this boundary, and the underlying structure",
           "Tools Carry a Safety Boundary, Not Just a Function": "The important principle here: when in doubt, default to blocking",
           "Authentication and Space Are Managed Together": "Even an authenticated request shouldn't be able to reach data",
           "Results Need a Boundary Too": "When a result has to be narrowed so far",
           "Call Count Is Also Part of the Design": "So MCP's performance can't be judged by channel response time alone",
           "What This Boundary Doesn't Do": "MCP's role, in the end, is simple"},
    "21": {"open": "And a Role isn't just a configuration value",
           "Representing an Agent as a Concept": "Representing an Agent as a Graph Node separates its definition",
           "A Role Describes the Boundary of Behavior": "This way, an Agent's behavior doesn't have to get crammed",
           "Separating Agent From Prompt": "Agent and Prompt shouldn't be treated as the same thing here",
           "The Graph Is Not the Scheduler Directing Agents": "Start using the Graph as a scheduler",
           "An Agent Is Both a User of Memory and Part of It": "That turns an Agent from a one-off execution object",
           "A Role Can Outlive Its Implementation": "The Graph expresses meaning that persists",
           "Roles Together Form a Roster": "Model Agent Roles as Concept Nodes, and the system can build",
           "Why Turn Roles Into Nodes": "The role that uses memory becomes a structure"},
    "21b": {"open": "A name only points at a role",
            "Mission Comes First": "One has authority to change something",
            "Reading Order Is Part of the Contract Too": "Pin the reading order down as part of the role",
            "A Door That Has to Be Passed": "Without this door, \"I did the work\"",
            "Several Roles Chained Together Become a Flow": "It captures the fact that a later stage depends on an earlier one",
            "Three Actions Keep the Contract Honest": "Keep these three separate, and the graph stays the source of truth",
            "A Role Has to Be Visible From Outside Too": "When the role changes in the graph, the contract visible outside",
            "A Role Needs a Fence": "Give an Agent unrestricted access to every piece of material",
            "The Graph Is a Contract, Not a Conductor": "What matters is that each role's responsibility"},
    "22": {"open": "So relationships need evidence too",
           "Edge and Evidence Are Different Information": "A relationship existing and a relationship being verified",
           "Evidence Can Attach to a Sentence": "With evidence in place, the graph moves past",
           "A Relationship Has Provenance Too": "At that point, it should be possible to trace backward",
           "Unsupported Relationships Need Different Handling": "So a relationship's existence has to be kept separate from its evidence",
           "Confidence Is Not Evidence": "Merge the two, and support gets tangled with judgment",
           "Automatically Generated Relationships Still Need Verification": "So generation and verification need to stay separate stages",
           "Provenance Is a Navigation Path": "With this path, an Agent's answer can be reviewed after the fact",
           "Evidence Becomes Part of the Graph": "Because a relationship itself can carry evidence"},
    "23": {"open": "VINC keeps them on three axes",
           "World Time": "That value can differ from when the Record was actually stored",
           "Graph Time": "Neither can substitute for the other",
           "Mixing the Two Clocks Causes Errors": "A Record being recently created doesn't mean the event happened",
           "Validity Is the Third Axis": "What hides a claim is one thing only",
           "Currency Is Derived, Never Stored": "Historical is not false",
           "Decisions Carry Validity When They Can Be Replaced": "So operational and policy Decisions carry a validity interval",
           "Supersession": "Both remain: a timeline can show the succession",
           "Episodes Have Yet Another Temporal Structure": "Which is why forcing the same time fields onto every Node kind",
           "Causal Time Is a Different Problem Entirely": "It means A has to come first for B",
           "A Causal Cone Spatializes Time": "It's time and causal relationships expressed as two different structures",
           "Time Is Not a Viewpoint": "Time isn't a matter of swapping camera or renderer"},
    "24": {"open": "So visualizing a Graph needs its own grammar",
           "Node Kinds Can Map to Shapes": "Shapes can change, but the same Node Type has to keep meaning the same thing",
           "Edges Have Kinds Too": "Draw every relationship with the same line",
           "Direction Is Information": "An arrow, or the direction of a line, isn't decoration",
           "Bridges Are Different From Ordinary Edges": "This matters especially where the graph crosses different Domains",
           "A View Is More Than Coordinates": "So the same Graph can look completely different",
           "Scope and Viewpoint Stay Separate": "So changing the Viewpoint doesn't move the Graph Scope",
           "There Can Be Several Viewpoints": "It's the same Graph, examined through different questions",
           "Defining a Grammar and Honoring It Are Different Problems": "The renderer has to actually implement the same contract",
           "View Is the Layer Between Data and Interpretation": "Which relationship gets emphasized, and which information fades"},
    "25": {"open": "The key idea is that Space isn't some separate",
           "Personal Graphs and Team Graphs": "A different Owner means a different memory boundary",
           "Space Becomes Part of a Call": "Using a Team Graph requires specifying the right Space explicitly",
           "An Address Can Determine Space Too": "In that case, the URL address decides which Graph you're entering",
           "Space Is Not Just a Screen Setting": "So Space isn't a UI selection",
           "Owner Isolation": "A search under Owner A should never surface a Node from Owner B's graph",
           "Team Roles Are Separate From Space": "So authentication, Space, and Role shouldn't get collapsed",
           "Space and Sync": "The same Owner's Graph can converge across environments",
           "Why a \"Who Am I\" Surface Is Needed": "Call a tool without knowing what Space even exists",
           "Balancing Defaults Against Explicitness": "What matters isn't the default existing",
           "Graph Space Is a Memory Boundary": "In the end, Space decides how far a Graph should be treated"},
    "26": {"open": "It's the order relationship: A comes before B",
           "Chronological Order and Causal Order Are Different": "So the Graph has to store ordinary temporal information and causal relationships separately",
           "Causal Relationships Are Read as a Partial Order": "The goal here isn't lining up the whole graph along one straight line",
           "Causal Order Comes Before Coordinates": "Coordinates don't create order",
           "A Causal Cone Is Built From Relationships": "So causal structure and the world's time axis can relate to each other",
           "No Metric Gets Invented": "Computing coordinates between graph Nodes doesn't mean a physical distance",
           "Separate Causal Structure From Layout": "A Layout can visualize Causal Order, but the Layout itself must never invent",
           "Causal Relationships Affect Search Too": "This is more powerful than a plain one-hop search",
           "Causal Space Differs From Semantic Space": "Two semantically close Nodes aren't guaranteed to be causally connected",
           "Several Meanings Coexist in Space": "Space is one projection for reading what the Graph means"},
    "27": {"open": "So density and layout need to be understood as separate things",
           "Position Isn't the Same as Meaning": "So on-screen distance alone shouldn't be read as a semantic relationship",
           "Density Is a Property of a Collection": "But density itself doesn't explain why",
           "Topic and Domain Produce Natural Density": "This is different information from search-result semantic similarity",
           "Gaussian Layout Started From This Problem": "This approach had the potential to express density and clustering together",
           "But Gaussian Was Not Adopted as the Default Layout": "These two don't need to run through the same algorithm",
           "Density Can Be Its Own Visual Layer": "This keeps a Node's structure separate from its spatial concentration",
           "Density Doesn't Explain Its Own Cause": "So Density is a clue for interpretation, not the interpretation itself",
           "Layout Has to Be a Function": "The same input should produce, as closely as possible, the same layout",
           "Layout and Evidence Need Separating Too": "Likewise, a dense region doesn't mean that topic matters more",
           "Space Is, in the End, the Result of Several Layers": "Hold to that, and space stops distorting"},
    "28": {"Reproducible Measurement": "It's to hold onto the conditions so the same question can be asked again"},
    "29": {"open": "So VINC's MCP efficiency evaluation treats calls-to-resolution as its core metric",
           "What Counts as Resolution": "A good tool returns accurate material in few calls",
           "Search and Brief Don't Compete": "It's how the entire call path needed to solve the same task changes"},
    "30": {"open": "An embedding model defines the retrieval system's entire semantic space",
           "Compare Under the Same Conditions": "What matters more than absolute scores is the relative difference",
           "Quantization Is Part of the Model": "A quantized model has to be evaluated alongside what the execution kernel actually supports",
           "Record Retrieval Quality Together With Runtime Conditions": "So an embedding benchmark shouldn't be a model leaderboard"},
    "31": {"open": "VINC's existing store uses a fixed vector width",
           "Changing the Schema and Regenerating Data Are Different Things": "Changing the DDL alone doesn't transform",
           "Implementation Bugs Are Part of the Migration Cost Too": "In the end, choosing a model isn't a comparison of model performance"},
    "32": {"open": "This is a failure that looks like success",
           "A Failed Vector Arm Produces the Same Problem": "So a result needs to record, alongside the plain search result",
           "A Gate That Never Fires Looks Like a Gate That Always Skips": "A gate that never fires prints a result identical to no reranking",
           "Make It Say What Ran": "The lesson is the same as the vector arm's",
           "Recovery Gets Measured": "Only then can \"succeeded on the first try\" be told apart"},
    "33": {"open": "But a decision made before its measurable conditions are sorted out",
           "The Measurement Gate": "A result that doesn't pass the Gate doesn't get used as grounds",
           "The Same Principle Applies to an Agent": "Collapse these stages into one, and the system loses the ability",
           "A Filter That Manufactured Confidence": "That is a filter, and a filter is supposed to change what is visible",
           "Keep the Tie": "Anything that removes candidates before a tie is broken"},
    "35": {"open": "Having come this far, though, it's clear how narrow that question was",
           "No Single Technology": "Memory only becomes a system once every one of these layers",
           "The Questions That Have to Survive": "And what really needs to survive in this system isn't a single answer"},
}

# A paragraph at the end of the document that hands over to the next chapter belongs
# to the close beat, where the surface already keeps its transition and the next link.
TRANSITION = re.compile(r"\b(next chapter|following chapters|chapters look at|from here on|"
                        r"what's left is|what remains|one chapter remains)\b", re.I)

# Part 0 is not derived (its chapters were written long by the author and were not padded);
# only the drawing sentences leave. (slot, beat, field, old, new)
PART0_EDITS = [
    ("05", 5, "paras", " Same diagram, three strengths, and the strength is carried in how much ink the relationship is drawn with.", ""),
    ("06", 6, "paras", ", and the drawing should stay a diagram rather than become a physics figure.", "."),
    ("06", 8, "paras", "So changing the view must not change the memory. ", ""),
    ("06", 8, "claim", "No mark enters and none leaves. Only the arrangement changes.", "Changing the view must not change the memory."),
    ("09", 3, "paras", "Fill one in and the mark stops being the same substance: it looks complete and it is no longer evidence of anything.", "Fill one in and it looks complete, and it is no longer evidence of anything."),
]

CLAIM_RE = re.compile(r"^\s*(?:«(.+?)»|\*\*(.+?)\*\*)\s*$")


def clean(s):
    s = s.replace("**", "").replace("`", "")
    s = re.sub(r"\s+—\s+([^—.!?]+?)\s+—\s+", r" (\1) ", s)   # a parenthetical pair
    s = re.sub(r"\s+—\s+(and|but|or|so|which|not|because)\b", r", \1", s)  # a joining dash
    s = re.sub(r"\s*—\s*", ": ", s)                          # a single dash breaks the clause
    return re.sub(r"\s+", " ", s).strip()


def parse(md):
    """-> [[heading or None, [(kind, payload), ...]], ...]"""
    lines = md.splitlines()
    if lines and lines[0].strip() == "---":
        i = 1
        while i < len(lines) and lines[i].strip() != "---":
            i += 1
        lines = lines[i + 1:]
    sections, cur, i = [], [None, []], 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            cur[1].append(("code", "\n".join(lines[i + 1:j])))
            i = j + 1
            continue
        if ln.startswith("# "):
            i += 1
            continue
        if ln.startswith("## "):
            sections.append(cur)
            cur = [ln[3:].strip(), []]
            i += 1
            continue
        if not ln.strip():
            i += 1
            continue
        j, buf = i, []
        while j < len(lines) and lines[j].strip() and not lines[j].startswith("```") and not lines[j].startswith("#"):
            buf.append(lines[j])
            j += 1
        if all(b.lstrip().startswith(("- ", "* ")) for b in buf):
            cur[1].append(("list", [re.sub(r"^\s*[-*]\s+", "", b) for b in buf]))
        elif all(b.strip().startswith("|") for b in buf):
            rows = [[c.strip() for c in b.strip().strip("|").split("|")] for b in buf]
            rows = [r for r in rows if not all(set(c) <= set("-: ") for c in r)]
            cur[1].append(("table", rows))
        else:
            cur[1].append(("para", " ".join(b.lstrip("> ").strip() for b in buf)))
        i = j
    sections.append(cur)
    return sections


def beat_from(blocks, h=None):
    b = {"paras": []}
    if h:
        b["h"] = h
    for kind, payload in blocks:
        if kind == "code":
            b.setdefault("diagrams", []).append(payload)
        elif kind == "list":
            items = [clean(x) for x in payload]
            if all(re.search(r"[.?!]$", x) for x in items):      # whole sentences or questions
                b["paras"].append(" ".join(x[0].upper() + x[1:] for x in items))
            else:                                               # fragments
                b["paras"].append("; ".join(x.rstrip(".") for x in items) + ".")
        elif kind == "table":
            head, rows = payload[0], payload[1:]
            b["paras"].append(" ".join(f"{clean(r[0])}: {clean(r[1]).rstrip('.')}." for r in rows if len(r) >= 2))
        else:
            m = CLAIM_RE.match(payload)
            if m and "claim" not in b:
                b["claim"] = clean(m.group(1) or m.group(2))
            else:
                b["paras"].append(clean(payload))
    return b


def sentences(p):
    # a sentence may end inside a closing quote:  ... relevant." Brief asks ...
    return [s for s in re.split(r'(?<=[.?!])\s+|(?<=[.?!]["”])\s+', p) if s]


def similar(a, b):
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def carry_claims(old_beats, new_beats):
    """Where an old claim sentence exists in the document, set it apart again."""
    claims = [b["claim"] for b in old_beats if b.get("claim")]
    for nb in new_beats:
        if nb.get("claim") or not claims:
            continue
        best = None
        for pi, p in enumerate(nb["paras"]):
            for s in sentences(p):
                for c in claims:
                    r = similar(s, c)
                    if r >= 0.6 and (best is None or r > best[0]):
                        best = (r, pi, s)
        if best:
            _, pi, s = best
            nb["claim"] = s
            rest = " ".join(x for x in sentences(nb["paras"][pi]) if x != s)
            if rest:
                nb["paras"][pi] = rest
            else:
                del nb["paras"][pi]


def derive(slot):
    path = os.path.join(CH, f"ch{slot}.json")
    old = json.load(open(path, encoding="utf-8"))
    md = open(os.path.join(SRC, f"{slot}.md"), encoding="utf-8").read()
    secs = parse(md)
    if slot in SPLITS:                              # hand split of the intro
        blocks, cuts = secs[0][1], SPLITS[slot]
        head = [[None, blocks[:cuts[0][0]]]]
        for k, (start, h) in enumerate(cuts):
            end = cuts[k + 1][0] if k + 1 < len(cuts) else len(blocks)
            head.append([h, blocks[start:end]])
        secs = head + secs[1:]
    intro, body = secs[0], secs[1:]
    if not any(k == "para" for k, _ in intro[1]) and body:   # document opens on a heading
        intro = [None, intro[1] + body[0][1]]
        body = body[1:]
    split_body = []
    for h, blocks in body:                                   # cut over-long sections in two
        cuts = SUBSPLITS.get((slot, h), [])
        start, cur_h = 0, h
        for prefix, new_h in cuts:
            at = next((i for i, (k, p) in enumerate(blocks)
                       if k == "para" and clean(p).startswith(prefix)), None)
            if at is None:
                print(f"  WARN ch{slot}: subsplit prefix not found in {h!r}: {prefix!r}")
                continue
            split_body.append([cur_h, blocks[start:at]])
            start, cur_h = at, new_h
        split_body.append([cur_h, blocks[start:]])
    body = split_body

    no, part = old["no"], old["part"]
    close = next((b for b in old["beats"] if b.get("kind") == "close"), None)
    beats = [dict(kind="open", label=f"{no}.0", eyebrow=f"{part_word(part)} · Chapter {no}",
                  h=old["title"], **beat_from(intro[1]))]
    for h, blocks in body:
        beats.append(beat_from(blocks, h))
    # the document's own hand-over paragraphs become the close beat's text
    if close:
        moved = []
        while beats[-1]["paras"]:
            p = beats[-1]["paras"][-1]
            if TRANSITION.search(p) or any(similar(p, q) >= 0.6 for q in close.get("paras", [])):
                moved.insert(0, beats[-1]["paras"].pop())
            else:
                break
        if not beats[-1]["paras"] and "diagrams" not in beats[-1]:
            beats.pop()
        if moved:
            close["paras"] = moved
    for b in beats:                                          # the chosen claim, set apart
        key = "open" if b.get("kind") == "open" else b.get("h")
        prefix = CLAIMS.get(slot, {}).get(key)
        if not prefix or b.get("claim"):
            continue
        hit = next(((pi, s) for pi, p in enumerate(b["paras"]) for s in sentences(p)
                    if s.startswith(prefix)), None)
        if hit is None:
            print(f"  WARN ch{slot}: claim not found in {key!r}: {prefix!r}")
            continue
        pi, s = hit
        b["claim"] = s
        rest = " ".join(x for x in sentences(b["paras"][pi]) if x != s)
        if rest:
            b["paras"][pi] = rest
        else:
            del b["paras"][pi]
    carry_claims(old["beats"], beats)
    for j, b in enumerate(beats):
        b["label"] = f"{no}.{j}"
        if "h" not in b and j:
            b["h"] = ""
    if close:
        close["label"] = f"{no}.close"
        beats.append(close)
    out = {k: old[k] for k in KEEP if k in old}
    out["beats"] = beats
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    words = sum(len(re.findall(r"\S+", p)) for b in beats for p in b["paras"])
    print(f"ch{slot}: {len(beats)} beats, {words}w, claims {sum(1 for b in beats if b.get('claim'))}, "
          f"diagrams {sum(len(b.get('diagrams', [])) for b in beats)}")


def part0():
    for slot in ["01", "02", "03", "04", "05", "06", "07", "08", "09"]:
        path = os.path.join(CH, f"ch{slot}.json")
        ch = json.load(open(path, encoding="utf-8"))
        for s, bi, field, old, new in PART0_EDITS:
            if s != slot:
                continue
            b = ch["beats"][bi]
            if field == "claim":
                if b.get("claim") == new:
                    continue
                assert b.get("claim") == old, (slot, bi, b.get("claim"))
                b["claim"] = new
            else:
                hits = [i for i, p in enumerate(b["paras"]) if old in p]
                if not hits:
                    continue  # already applied
                assert len(hits) == 1, (slot, bi, old, hits)
                b["paras"][hits[0]] = b["paras"][hits[0]].replace(old, new).strip()
        out = {k: ch[k] for k in KEEP if k in ch}
        out["beats"] = ch["beats"]
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        print(f"ch{slot}: motion dropped, {len(out['beats'])} beats")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--part0" in sys.argv:
        part0()
    slots = args or sorted(os.path.basename(p)[:-3] for p in glob.glob(os.path.join(SRC, "*.md")))
    if not ("--part0" in sys.argv and not args):
        for s in slots:
            derive(s)
