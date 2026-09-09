#!/usr/bin/env python3
"""Part 2 (chapters 1 to 6) and Part 3 (one chapter), plus Part 1 prose pads.

Part 2's register is before-and-after: the same thing run twice under two
conditions, two results side by side, counts and bars, nothing dramatised.
Every number on these pages is one the book itself states, or one the motion
addendum specifies for the drawing. Nothing is invented here.

Part 3 is the one place on the site where motion loops, and it is earned:
the chapter's claim is that the process is circular. Three turns, then it
stops on the first position, and the reader drives it by scrolling.
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
CH = []


def chapter(no, part, key, slot, title, side, beats, pair=None, ring=None,
            stage=None, nxt=None):
    d = dict(n=key, no=no, part=part, file=f"Ch{key}.dc.html", slot=slot,
             title=title, side=side, viewBox="0 0 640 640", beats=beats,
             marks=[], lines=[], anchors=[])
    if pair:
        d["pair"] = pair
    if ring:
        d["ring"] = ring
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


def OPEN(no, part, title, paras, claim=None):
    d = {"kind": "open", "label": f"{no}.0", "eyebrow": f"Part {part} · Chapter {no}",
         "h": title, "paras": paras}
    if claim:
        d["claim"] = claim
    return d


def CLOSE(no, paras):
    return {"kind": "close", "label": f"{no}.close", "paras": paras}


# ============================================================ PART 2, ch 1
chapter(1, 2, 28, "vinc.book.28-performance.en", "Measurement Needs Conditions", "left", [
    OPEN(1, 2, "Measurement Needs Conditions", [
        "Measuring a system does not mean numbers can be collected straight away. First we have to settle what is being compared under what identical conditions.",
        "In a search system this problem is large. With the same model, results change meaning when the query set, the candidate document count, the answer set, the language mix, the embedding method or the hardware changes.",
        "So measurement starts in an order: fixture, then condition check, then run, then measure, then compare.",
    ], "A fixture is the fixed set of inputs an evaluation uses."),
    B("1.1", "The Fixture Is Checked First", [
        "Queries and answers, the document set, the language composition all belong to the fixture.",
        "In VINC's embedding evaluation the fixture itself is inspected before any number is read.",
        "Whether cross-lingual pairs actually exist in the canonical set. Whether a cross-language query is really written in that language. Whether the answers are a subset of the canonical set. Duplicates and counts get checked first too.",
        "Compute a metric without passing that check and the number exists while the evaluation result does not.",
    ], "There is a number. It is not a measurement."),
    B("1.2", "Conditions Are Part of the Value", [
        "Suppose a search model scores 0.67 on a ranking metric. That figure alone cannot say whether it beats another experiment's 0.62.",
        "If the two runs used corpora of different sizes, the difficulty of the search itself was different.",
        "Fewer distractor documents in an evaluation corpus can raise the absolute score. So VINC's embedding evaluation records that a corpus cap can make absolute values higher than in a full-corpus environment, and does not compare absolute values across different measurements.",
        "What can be compared is the difference between models inside the same fixture and the same conditions.",
    ], "A measurement is not a number with its conditions removed. It is a result that includes them."),
    B("1.3", "What Has to Survive the Run", [
        "A good evaluation leaves at least this behind: which fixture, whether the fixture was valid, which model, which precision and quantization, which retrieval structure, which hardware and kernel conditions, which metrics, what came out, and if it failed, why.",
        "Without that, a measurement is a one-off observation.",
        "The purpose is not to produce a number. It is to remember the conditions well enough that the same question can be asked again next time.",
        "A result nobody can re-run is a result nobody can disagree with, which sounds like strength and is not.",
    ]),
    B("1.4", "Two Runs, Side by Side", [
        "The characteristic shape of Part 2 is the same thing run twice under two conditions, with the two results sitting next to each other and the conditions labelled.",
        "Not one number presented as a verdict.",
        "The numbers are not editorialised, because the moment a measurement is described as good or disappointing, the description starts travelling instead of the conditions.",
        "And a measurement that travels without its conditions turns into a claim, which is the thing chapter 8 of Part 0 warned about.",
    ]),
    B("1.5", "Measurement Is Also Memory", [
        "The result of a measurement goes back into the graph as a record: this was true then, under these conditions.",
        "That is why measurement is not an activity outside the system. It produces exactly the kind of memory the earlier parts were built to hold.",
        "Which model was chosen, why it was chosen, which experiment failed, under which condition the result changed: all of it becomes the input to the next decision.",
        "A number without its record is a number that will be re-derived, differently, by whoever needs it next.",
    ], "A measurement kept without its conditions is a number waiting to be misquoted."),
    CLOSE(1, [
        "The next chapter measures the surface an agent actually pays for: not latency, but calls.",
    ]),
], pair={"left": "corpus capped", "right": "full corpus", "lx": 196, "rx": 452,
         "y0": 190, "dy": 88, "note": "same model, different difficulty",
         "noteAt": 3, "rows": [
    {"label": "fixture valid", "kind": "count", "a": 1, "b": 1, "at": 1},
    {"label": "absolute score", "kind": "bar", "a": 0.67, "b": 0.62, "max": 1.0, "fmt": "%", "at": 2},
    {"label": "comparable across runs", "kind": "count", "a": 0, "b": 0, "at": 3},
]}, nxt=("Chapter 2. A Tool's Efficiency Is Measured in Calls", "Ch29.dc.html"))

# ============================================================ PART 2, ch 2
chapter(2, 2, 29, "vinc.book.29-call-efficiency.en", "A Tool's Efficiency Is Measured in Calls", "right", [
    OPEN(2, 2, "A Tool's Efficiency Is Measured in Calls", [
        "The easiest thing to measure about a tool call is its latency.",
        "But from an agent's side, how many milliseconds one call took is not the cost that matters.",
        "How many calls it takes to solve one question is the more direct cost.",
    ], "Two systems with identical per-call speed can differ enormously in total cost."),
    B("2.1", "Two Paths to One Answer", [
        "One path: search, check the node, check the relationships, check the record, inspect again, answer.",
        "Another path: return the needed node, its evidence and its relationships together, then answer.",
        "So VINC's MCP efficiency evaluation treats calls-to-resolution as the core metric rather than transport time.",
        "The addendum for this page draws it literally: seven marks, then two. No chart is needed when the number is small enough to count.",
    ]),
    B("2.2", "Counting Calls Is Not Enough Either", [
        "One call that found nothing is a small number and not an efficient one.",
        "So call efficiency has to be read across several axes at once: whether the task was actually solved, how many calls it took, how many contributed nothing, how large the payload the model had to process was, whether the evidence needed for the answer was obtained, and whether the run recovered from a bad or empty result.",
        "On that reading a fast API is not automatically a good tool.",
        "A good tool returns accurate material in few calls and leaves the agent nothing to go back for.",
    ], "Success, calls, waste, payload, evidence, recovery. One of them alone says nothing."),
    B("2.3", "Where the Bottleneck Actually Was", [
        "In evaluation based on real interaction, single-search responsiveness and hopping from a concept to its neighbours were both practical enough.",
        "The cost showed up in repeated sequential calls, in the round trips.",
        "And as result payloads grew, the bottleneck moved from the transport layer toward the model's context.",
        "So the next thing to optimise is not necessarily latency. Collapsing search, confirm, check relationships, confirm again into one meaningful retrieval has the larger effect.",
    ]),
    B("2.4", "Search and Brief Do Not Compete", [
        "Read this way, a search tool and a material-assembly tool are not two search tools competing.",
        "Search is closer to a primitive that finds candidates. Brief takes a question, searches, resolves the target, and combines that node's context: a composite retrieval.",
        "So what has to be evaluated is not which tool is faster.",
        "It is how the whole call path changes for the same task, which is a different measurement and needs the same fixture discipline as chapter 1.",
    ]),
    B("2.5", "The Number Is Not the Verdict", [
        "A drop from seven calls to two is a result under the conditions it was measured in, not a property of the tool.",
        "Change the task set and the ratio changes. That is why the tasks are fixed and recorded alongside the count.",
        "One finding in a rerun inverted against the baseline, and that inversion is part of the record rather than an embarrassment to be smoothed out.",
        "A measurement that only ever confirms the design is not measuring the design.",
    ], "An inverted finding is a finding."),
    CLOSE(2, [
        "The next chapter compares the models underneath all of this, and what it takes to compare them fairly.",
    ]),
], pair={"left": "before", "right": "after", "lx": 196, "rx": 452,
         "y0": 200, "dy": 96, "note": "same seven tasks, one surface added",
         "noteAt": 3, "rows": [
    {"label": "calls to resolution", "kind": "count", "a": 7, "b": 2, "at": 1},
    {"label": "evidence obtained", "kind": "count", "a": 1, "b": 1, "at": 2},
    {"label": "round trips", "kind": "count", "a": 6, "b": 1, "at": 3},
]}, nxt=("Chapter 3. How Embedding Models Get Compared", "Ch30.dc.html"))

# ============================================================ PART 2, ch 3
chapter(3, 2, 30, "vinc.book.30-embedding-eval.en", "How Embedding Models Get Compared", "left", [
    OPEN(3, 2, "How Embedding Models Get Compared", [
        "Changing an embedding model does not only change vector quality. An embedding model defines the semantic space the search system runs in.",
        "So a model change has to be evaluated across at least four things at once: search quality, compute cost, storage structure, and operating cost.",
        "Pick a model on search quality alone and the unexpected costs arrive later, in production.",
    ], "The model is not a number. It is the space everything else is measured in."),
    B("3.1", "The Same Conditions, Again", [
        "In VINC's embedding evaluation, different models are compared under identical evaluation conditions.",
        "What matters there is the relative difference inside the same fixture, not the absolute score.",
        "Language characteristics matter in particular. In one experiment the Korean ranking metric fell from 0.670 to 0.472 between models.",
        "That result is not a difference between two model names. It shows how well a semantic space fits the search workload it is being asked to serve.",
    ]),
    B("3.2", "Quantization Is Part of the Model", [
        "Running a model at reduced precision does not always give the same performance characteristics.",
        "A quantized model has to be read together with the conditions its execution kernel supports.",
        "One evaluation assumed a situation using a particular CPU instruction extension. If that kernel condition does not hold in the real environment, accumulator behaviour and performance characteristics both change.",
        "So model capability and runtime capability are two different claims, and a model supporting a precision does not mean this system gets that precision's performance.",
    ], "Model capability is not runtime capability."),
    B("3.3", "What the Record Has to Carry", [
        "The result of an embedding evaluation should be left in a form that keeps the model, the dimension, the precision, the quantization, the kernel, the corpus, the query set, the language, the metric, the latency, and the result.",
        "With that, the result can be interpreted again later.",
        "Without it, a benchmark is a leaderboard, and a leaderboard is not a record of system conditions.",
        "A ranking survives; the conditions that produced it do not, and the ranking gets quoted long after those conditions stopped holding.",
    ]),
    B("3.4", "Two Numbers, One Fixture", [
        "So the shape of this page is two columns: the same fixture, two models, the numbers beside each other and the conditions named underneath.",
        "The lower number is not framed as a failure and the higher one is not framed as a win.",
        "Both are what happened under stated conditions, and a reader who wants the verdict has to look at the conditions to get it.",
        "That is slower to read and it is the only reading that survives the next model.",
    ]),
    B("3.5", "Comparison Is Not Selection", [
        "Comparing two models produces a difference. Choosing between them requires everything the difference does not contain.",
        "Storage compatibility, re-embedding cost, index rebuild time, worker support, operating cost. Chapter 4 is about exactly those.",
        "Which means an embedding benchmark is an input to a decision and never the decision.",
        "Treat the benchmark as the decision and the migration cost arrives as a surprise, after the choice has already been made.",
    ], "A benchmark is not a model ranking. It is a record of system conditions."),
    CLOSE(3, [
        "The next chapter is the cost the benchmark left out.",
    ]),
], pair={"left": "model A", "right": "model B", "lx": 196, "rx": 452,
         "y0": 196, "dy": 92, "note": "one fixture, one corpus, one query set",
         "noteAt": 2, "rows": [
    {"label": "Korean ranking metric", "kind": "bar", "a": 0.670, "b": 0.472, "max": 1.0, "fmt": "%", "at": 1},
    {"label": "same fixture", "kind": "count", "a": 1, "b": 1, "at": 2},
    {"label": "kernel condition held", "kind": "count", "a": 1, "b": 0, "at": 3},
]}, nxt=("Chapter 4. The Cost of Changing a Model and Storage Structure", "Ch31.dc.html"))

# ============================================================ PART 2, ch 4
chapter(4, 2, 31, "vinc.book.31-migration.en", "The Cost of Changing a Model and Storage Structure", "right", [
    OPEN(4, 2, "The Cost of Changing a Model and Storage Structure", [
        "The dimension of an embedding vector is not simply a model property. If the store was built to hold vectors of a particular width, that width is a storage contract.",
        "VINC's existing store uses fixed-width vectors.",
        "So adding a model that is not that width does not end as a configuration change.",
    ], "A model with a different vector width cannot go into the existing structure as it is."),
    B("4.1", "Schema Migration Is Not Embedding Migration", [
        "Changing the schema does not convert existing documents' embeddings into the new semantic space.",
        "After the storage structure changes, the embeddings for existing chunks still have to be regenerated.",
        "The path is: existing chunks, new model, re-embed, new vectors, rebuild the index, then verify retrieval.",
        "So the cost of a model change cannot be calculated as the cost of the schema migration alone, which is the calculation that usually gets made.",
    ]),
    B("4.2", "The Measured Rebuild", [
        "In VINC's measurement, reindexing several hundred chunks took about twenty-five seconds.",
        "Embedding generation varied more by model: one model took more than three times as long to re-embed as another.",
        "Scale that proportionally and thousands of chunks are hours, tens of thousands are half a day or more of work.",
        "That figure is an observation from one environment and not an absolute standard for every environment. What it establishes is the shape of the cost, not its value.",
    ], "Changing an embedding model can be a migration workload, not a setting."),
    B("4.3", "Implementation Bugs Are Migration Cost", [
        "A model change touches more than the data structure. The embedding worker's branching logic is affected too.",
        "Problems can surface in something as small as how a prefix distinguishing models is handled.",
        "That kind of problem is invisible from the benchmark numbers, because the benchmark ran on the path that worked.",
        "So a migration evaluation has to include schema compatibility, worker compatibility, the model registry, re-embedding, index rebuild, and retrieval validation.",
    ]),
    B("4.4", "Before and After, Not Better and Worse", [
        "So the honest picture is two columns again: the state before the change and the state after it, with the work between them named.",
        "Neither column is the good one. One is where the system is and one is where it would be, and the space between them is measured in hours.",
        "A model that scores higher and costs half a day of rebuild is a different proposition from one that scores slightly lower and costs nothing.",
        "The benchmark cannot say which is right. It can only stop the question being asked with the cost hidden.",
    ]),
    B("4.5", "Model Choice Is Path Choice", [
        "In the end, choosing a model is not comparing model performance.",
        "It is comparing the cost of changing the whole storage and execution path.",
        "Which is why this chapter sits after the benchmark rather than before it: the benchmark produces the difference, and this produces the price of acting on it.",
        "Both are records. Neither is a decision, and the next chapter is about what happens when a record only looks like one.",
    ], "A number that arrives without its migration cost is half a measurement."),
    CLOSE(4, [
        "The next chapter is the most dangerous case in Part 2: the run that produced normal numbers and measured the wrong thing.",
    ]),
], pair={"left": "before", "right": "after", "lx": 196, "rx": 452,
         "y0": 190, "dy": 88, "note": "the space between the columns is the work",
         "noteAt": 2, "rows": [
    {"label": "schema accepts the width", "kind": "count", "a": 0, "b": 1, "at": 1},
    {"label": "existing vectors reusable", "kind": "count", "a": 0, "b": 0, "at": 2},
    {"label": "reindex, several hundred chunks", "kind": "bar", "a": 0, "b": 25, "max": 40, "fmt": "% s", "at": 3},
]}, nxt=("Chapter 5. Failures That Look Like Success", "Ch32.dc.html"))

# ============================================================ PART 2, ch 5
chapter(5, 2, 32, "vinc.book.32-failure-cases.en", "Failures That Look Like Success", "left", [
    OPEN(5, 2, "Failures That Look Like Success", [
        "The most dangerous failure in system evaluation is not the one that fails visibly.",
        "It is the run that returns perfectly normal numbers while measuring the wrong thing.",
        "Say we are evaluating cross-language search, and the fixture contains no cross-language pairs at all.",
    ], "The benchmark runs. The metric is computed. It measures nothing about cross-language search."),
    B("5.1", "A Number That Is Not a Measurement", [
        "That is a failure that looks like success, and nothing in the output distinguishes it from a real result.",
        "This is why chapter 1 put the fixture check before the run rather than after it.",
        "A metric computed on an invalid fixture is not a weaker measurement. It is a different measurement, of something nobody asked about.",
        "And it is the kind of result that gets quoted, because it looks like every other result.",
    ]),
    B("5.2", "A Degraded Arm Looks Like a Search", [
        "In some VINC evaluations the vector retrieval arm timed out.",
        "If the system then returned only text results, from outside it looks as though search succeeded.",
        "But if what was being evaluated is hybrid retrieval, that is not a success. It is one arm of two.",
        "So the result has to record which retrieval arms actually ran. Only with that can the final result be interpreted, because a degraded search and a complete one look identical from the rows they return.",
    ], "A degraded search and a complete one are indistinguishable from their output."),
    B("5.3", "Empty Is Information", [
        "An empty result does not always mean the system failed. The question may genuinely have asked for something that does not exist.",
        "The larger problem is the opposite: an empty result where the agent picks some other node anyway.",
        "This is where the resolution principle earns its cost. If candidates are insufficient, the empty result is kept as it is, and recovery is attempted through another path if there is one.",
        "That is what reduces false positives, and false positives are the failures that survive review.",
    ]),
    B("5.4", "Measure the Recovery", [
        "So evaluation has to include what happened after the failure.",
        "Whether recovery succeeded, how many extra calls it needed, whether the evidence was obtained in the end.",
        "That is what separates succeeded on the first try from succeeded after five wrong turns, and those are not the same system even when the success rate matches.",
        "Both facts sit side by side in the record, and neither is dramatised.",
    ]),
    B("5.5", "Flatness Is the Point", [
        "A healthy bar and an empty fixture behind it are both true at the same time.",
        "Neither gets animated more than the other, because the whole finding of this chapter is that the failure looked normal.",
        "Draw the failure as an alarm and the reader learns to look for alarms, which is exactly the instinct that missed this class of failure in the first place.",
        "So the drawing stays flat and the two facts stay adjacent, and the reader has to read both.",
    ], "The failure looked normal. That is the finding, and the drawing has to say so quietly."),
    CLOSE(5, [
        "The last chapter of Part 2 puts measurement back before the thing it was always supposed to precede.",
    ]),
], pair={"left": "the metric", "right": "the fixture behind it", "lx": 196, "rx": 452,
         "y0": 196, "dy": 92, "note": "both true at once",
         "noteAt": 2, "rows": [
    {"label": "reported score", "kind": "bar", "a": 0.71, "b": 0, "max": 1.0, "fmt": "%", "at": 1},
    {"label": "cross-language pairs present", "kind": "count", "a": 0, "b": 0, "at": 2},
    {"label": "retrieval arms that ran", "kind": "count", "a": 1, "b": 2, "at": 3},
]}, nxt=("Chapter 6. Measurement Comes Before Decision", "Ch33.dc.html"))

# ============================================================ PART 2, ch 6
chapter(6, 2, 33, "vinc.book.33-measurement-before-decision.en", "Measurement Comes Before Decision", "right", [
    OPEN(6, 2, "Measurement Comes Before Decision", [
        "In a system, important decisions often get made first. This model is better. This schema will do. This layout suits it.",
        "But a decision made before the measurable conditions were settled is easy to overturn later.",
        "VINC's embedding model replacement shows it.",
    ], "A decision should be the result of a measurement, not the thing a measurement is asked to support."),
    B("6.1", "The Questions That Came Too Late", [
        "At first a schema change looked sufficient. Checking the actual storage structure showed a fixed-width constraint, and the new model's dimension could not be accommodated as it was.",
        "What was needed turned out to be a rebuild, not a schema change.",
        "The questions that should have been asked before deciding were: what is the current schema, what is the new model's dimension, can existing vectors be reused, what does re-embedding cost, is an index rebuild needed, does the worker support the new model.",
        "None of those are hard questions. They were simply asked after the decision instead of before it.",
    ]),
    B("6.2", "The Measurement Gate", [
        "So the evaluation flow can carry a gate: question, fixture check, environment check, measure, evidence, decide.",
        "A result that did not pass the gate is not used as grounds for a decision.",
        "This is not procedure for its own sake. It is a device for separating the state of being measurable from the state of being decidable.",
        "The two feel the same from inside, which is why the separation has to be structural rather than remembered.",
    ], "A result that did not pass the gate is not evidence."),
    B("6.3", "The Same Rule for Agents", [
        "The principle applies to an agent's memory retrieval unchanged.",
        "Receiving a search result is not grounds for a conclusion. Candidate, resolution, evidence, context, interpretation, answer.",
        "Search is discovery, resolution is selection, evidence is support, interpretation is judgment.",
        "Mix those steps into one and the system loses the ability to tell what it knew from what it inferred, which is the distinction Part 0 chapter 8 said everything depended on.",
    ]),
    B("6.4", "Part 1 Built It, Part 2 Asked", [
        "Part 1 was about structuring memory: document, chunk, embedding, node, edge, space.",
        "Part 2 asked whether that structure actually works: memory, search, resolution, evidence, work, measurement, decision.",
        "So measurement is not an activity outside the system. Its results become memory again.",
        "Which model was chosen and why, which experiment failed, under what condition the result changed: all of it becomes input to the next decision.",
    ]),
    B("6.5", "The Loop Closes", [
        "Remember, search, interpret, measure, decide, remember again.",
        "When that circuit continues, an agent's memory becomes a structure of verifiable experience rather than a store.",
        "It is also the reason Part 2 exists at all. Without it, Part 1 is a design nobody checked.",
        "And with it, the design has a record of the conditions under which it held, which is the only form in which a design can be handed to whoever comes next.",
    ], "Measurement is not outside the system. Its result is memory too."),
    CLOSE(6, [
        "One chapter remains, and it is the shape all of this has been tracing.",
    ]),
], pair={"left": "decide first", "right": "measure first", "lx": 196, "rx": 452,
         "y0": 190, "dy": 88, "note": "the same work, in two orders",
         "noteAt": 2, "rows": [
    {"label": "conditions known at decision", "kind": "count", "a": 0, "b": 1, "at": 1},
    {"label": "decision survived contact", "kind": "count", "a": 0, "b": 1, "at": 2},
    {"label": "rebuild discovered", "kind": "count", "a": 1, "b": 1, "at": 3},
]}, nxt=("Part 3. Memory Is a Cycle", "Ch35.dc.html"))

# ============================================================ PART 3
chapter(1, 3, 35, "vinc.book.35-cycle.en", "Memory Is a Cycle", "left", [
    OPEN(1, 3, "Memory Is a Cycle", [
        "The first question that comes to mind about a memory system is usually about storage. What goes where, and how do you get it back out.",
        "Having come this far, though, it is clear how narrow that question was.",
        "Documents alone could not be searched, so chunks were needed. Chunks alone could not hold the relationships between meanings, so vectors and a graph were needed.",
    ], "Each chapter solved one problem. String the solutions together and what was there from the start was not a storage problem."),
    B("1.1", "What Each Layer Was For", [
        "The graph alone could not say who should use what, so roles and space were needed.",
        "Search alone gave no grounds to trust its results, so evidence was needed.",
        "And to confirm any of this actually works, measurement was needed.",
        "None of those were additions to a storage system. Each one was the answer to a question the previous layer could not answer on its own.",
    ]),
    B("1.2", "No Single Technology", [
        "What is worth paying attention to in this cycle is not any single technology.",
        "Vectors do not make memory. The graph does not make memory. Neither does the interface, nor the agent.",
        "Memory only becomes a system once every one of these layers holds its own responsibility and still connects into a single flow.",
        "Take one layer out and the flow does not degrade gracefully; it stops being a cycle and becomes a pipeline with a hole in it.",
    ]),
    B("1.3", "The Questions That Have to Survive", [
        "What really needs to survive in this system is not a single answer.",
        "What did it know. When did it know it. Where does it belong. What is it connected to.",
        "Why was that judgment made. Under what conditions was it measured. What turned out wrong. And what got decided as a result.",
        "Eight questions, and every one of them was a chapter.",
    ]),
    B("1.4", "Then It Is Not Data", [
        "Answer those structurally and what you are looking at is no longer just stored data.",
        "It is a system's experience.",
        "Which is the difference the whole book has been drawing: between a store that holds what was said, and a memory that holds what happened and why anyone believed it.",
        "The first can be copied. The second has to be built, and kept.",
    ]),
    B("1.5", "And Record Again", [
        "So the loop runs: record, structure, search, interpret, use, measure, decide, and record again.",
        "It runs three times here and stops on the first position, because a loop that never stops is a decoration rather than an argument.",
        "The site bans looping motion everywhere else. This is the one exception, and it is earned by the claim the chapter makes.",
        "Memory is not the stored past. It is experience structured so it can be used again.",
    ], "Memory is experience structured so that it can be used again."),
    CLOSE(1, [
        "That is the end of the book, and the beginning of the part that is not a book: a graph somebody keeps.",
    ]),
], ring={"cx": 320, "cy": 306, "rad": 178, "at": 5, "steps": [
    "record", "structure", "search", "interpret", "use", "measure", "decide", "record again"]})


for ch in CH:
    p = os.path.join(HERE, "ch", f"ch{ch['n']}.json")
    json.dump(ch, open(p, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=2)
    print("wrote", os.path.basename(p), f"part{ch['part']} ch", ch["no"])

# --- pad every Part 1 beat the volume gate flagged ------------------------
PATCH = {
    "ch22.json": {
        "14.1": "Two edges can look identical on screen and mean two different degrees of certainty.",
        "14.3": "An edge with no grounds is a claim by whoever wrote it, and nothing marks it as one.",
        "14.5": "That combination is what the chapters on time and view are built on top of.",
    },
    "ch23.json": {
        "15.1": "One timestamp collapses the two and the collapse cannot be undone afterwards.",
        "15.2": "Both mistakes produce a perfectly ordinary looking date, which is why neither gets caught.",
    },
    "ch24.json": {
        "16.1": "A mapping that holds is what lets a reader learn the vocabulary once.",
        "16.3": "Same graph, different scope and viewpoint, entirely different screen.",
        "16.4": "Four questions, one graph, and the count of marks never changes between them.",
    },
    "ch25.json": {
        "17.2": "A credential that implies permission for every graph it can reach is not a boundary.",
    },
    "ch27.json": {
        "19.1": "Nearness on screen has at least four possible causes and the picture names none of them.",
    },
}
for f, add in PATCH.items():
    path = os.path.join(HERE, "ch", f)
    d = json.load(open(path, encoding="utf-8"))
    for b in d["beats"]:
        if b.get("label") in add:
            b["paras"].append(add[b["label"]])
    json.dump(d, open(path, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=2)
    print("padded", f)
