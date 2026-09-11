#!/usr/bin/env python3
"""One-shot, idempotent: move the 35 chapter datasets from the 4-part, part-local
numbering (9/19/6/1) to the 5-part split with global chapter numbers 1..35.
Verdict and reasons: graph doc vinc.plan.book-content-review-2026-09-10 section 2.

  python -X utf8 restructure.py          apply
  python -X utf8 restructure.py --dry    report only

Slot (file name, `n`) never changes. `part`, `no`, eyebrow, labels, nextLabel and
the in-prose cross references do. Re-running is a no-op.
"""
import json, os, re, sys, glob

HERE = os.path.dirname(os.path.abspath(__file__))
DRY = "--dry" in sys.argv

# slot -> part. Global chapter number is the position in ORDER, 1-based.
ORDER = ["01", "02", "03", "04", "05", "06", "07", "08", "09",
         "10", "11", "12", "13", "14", "15",
         "16", "17", "18", "19", "20", "21", "21b", "22",
         "23", "24", "25", "26", "27",
         "28", "29", "30", "31", "32", "33",
         "35"]
PART_OF = {}
for s in ORDER[0:9]:   PART_OF[s] = 0
for s in ORDER[9:15]:  PART_OF[s] = 1
for s in ORDER[15:23]: PART_OF[s] = 2
for s in ORDER[23:28]: PART_OF[s] = 3
for s in ORDER[28:34]: PART_OF[s] = 4
PART_OF["35"] = 5
assert len(ORDER) == 35 and len(PART_OF) == 35
CHAPTER_OF = {s: i + 1 for i, s in enumerate(ORDER)}
assert CHAPTER_OF["21b"] == 22 and CHAPTER_OF["35"] == 35

PART_TITLE = {
    0: "Why Turn Memory into a System",
    1: "How Memory Is Stored",
    2: "How Memory Is Found and Handed Out",
    3: "Where Memory Lives: Time and Space",
    4: "Was the Design Actually Right",
    5: "Memory Is a Cycle",          # the closing; not a numbered part
}


def part_word(p):
    return "Closing" if p == 5 else f"Part {p}"


def page_id(p):
    return "closing" if p == 5 else f"part-{p}"


# In-prose cross references, per slot: (old, new). Each old string must occur
# exactly once across the chapter's paras/claims/headings, or already be applied.
XREF = {
    "11": [("Chapter 1 divided memory into six kinds of node.",
            "Chapter 10 divided memory into six kinds of node.")],
    "14": [("the ones Chapter 1 named.", "the ones Chapter 10 named.")],
    "15": [("the property the rest of Part 1 depends on.",
            "the property the rest of the book depends on."),
           ("what the rest of Part 1 rests on.", "what the parts that follow rest on.")],
    "17": [("This is measured with real numbers in Part 2.",
            "This is measured with real numbers in Part 4.")],
    "18": [("Chapter 7 already showed that this system",
            "Chapter 16 already showed that this system")],
    "20": [("That question is measured with real numbers in Part 2.",
            "That question is measured with real numbers in Part 4.")],
    "21b": [("Chapter 12 made an agent a node on the graph.",
             "Chapter 21 made an agent a node on the graph.")],
    "22": [("the time that has been deferred since chapter 1.",
            "the time that has been deferred since Chapter 10.")],
    "26": [("The last chapter of Part 1 asks what the density",
            "The last chapter of Part 3 asks what the density")],
    "27": [("Part 1 has built the structure. Part 2 asks whether",
            "Parts 1 to 3 have built the structure. Part 4 asks whether")],
    "28": [("The characteristic shape of Part 2 is the same thing",
            "The characteristic shape of Part 4 is the same thing"),
           ("which is the thing chapter 8 of Part 0 warned about.",
            "which is the thing Chapter 8 warned about.")],
    "29": [("needs the same fixture discipline as chapter 1.",
            "needs the same fixture discipline as Chapter 29.")],
    "30": [("Chapter 4 is about exactly those.", "Chapter 32 is about exactly those.")],
    "31": [("the most dangerous case in Part 2:", "the most dangerous case in Part 4:")],
    "32": [("This is why chapter 1 put the fixture check",
            "This is why Chapter 29 put the fixture check"),
           ("The last chapter of Part 2 puts measurement back",
            "The last chapter of Part 4 puts measurement back")],
    "33": [("the distinction Part 0 chapter 8 said everything depended on.",
            "the distinction Chapter 8 said everything depended on."),
           ("Part 1 was about structuring memory:", "Parts 1 to 3 were about structuring memory:"),
           ("Part 2 asked whether that structure actually works:",
            "Part 4 asked whether that structure actually works:"),
           ("Part 1 answered how. Part 2 answered whether,",
            "Parts 1 to 3 answered how. Part 4 answered whether,"),
           ("It is also the reason Part 2 exists at all. Without it, Part 1 is a design nobody checked.",
            "It is also the reason Part 4 exists at all. Without it, Parts 1 to 3 are a design nobody checked."),
           ("Part 1 Built It, Part 2 Asked", "Parts 1 to 3 Built It, Part 4 Asked")],
}


def slot_of(path):
    return os.path.basename(path)[2:-5]


def texts(beat):
    """Every prose field of a beat as (container, key) so it can be written back."""
    out = []
    for k in ("h", "claim", "eyebrow"):
        if beat.get(k):
            out.append((beat, k))
    for i in range(len(beat.get("paras", []))):
        out.append((beat["paras"], i))
    return out


def apply_xrefs(slot, ch, log):
    for old, new in XREF.get(slot, []):
        hits = 0
        already = 0
        for b in ch["beats"]:
            for c, k in texts(b):
                hits += c[k].count(old)
                if new in c[k]:
                    already += 1
        if hits == 0 and already:
            continue  # idempotent re-run
        if hits != 1:
            raise SystemExit(f"ch{slot}: expected 1 hit for {old!r}, found {hits}")
        for b in ch["beats"]:
            for c, k in texts(b):
                if old in c[k]:
                    c[k] = c[k].replace(old, new)
                    log.append(f"ch{slot}: xref {old[:40]!r} -> {new[:40]!r}")


REF = re.compile(r"\b(?:Parts?\s+\d+(?:\s+to\s+\d+)?|[Cc]hapter\s+\d+)\b")


def main():
    files = {slot_of(p): p for p in glob.glob(os.path.join(HERE, "ch", "*.json"))}
    assert set(files) == set(ORDER), sorted(set(files) ^ set(ORDER))
    chapters = {s: json.load(open(files[s], encoding="utf-8")) for s in ORDER}
    log = []
    for i, s in enumerate(ORDER):
        ch = chapters[s]
        p, no = PART_OF[s], CHAPTER_OF[s]
        ch["part"], ch["no"] = p, no
        beats = ch["beats"]
        beats[0]["eyebrow"] = f"{part_word(p)} · Chapter {no}"
        for j, b in enumerate(beats):
            b["label"] = f"{no}.close" if b.get("kind") == "close" else f"{no}.{j}"
        last = beats[-1]
        if i + 1 < len(ORDER):
            nxt = ORDER[i + 1]
            np_, nn = PART_OF[nxt], CHAPTER_OF[nxt]
            if np_ != p:
                last["nextLabel"] = f"{part_word(np_)}. {PART_TITLE[np_]}"
            else:
                last["nextLabel"] = f"Chapter {nn}. {chapters[nxt]['title'].rstrip('?')}"
            last["nextHref"] = chapters[nxt]["file"]
        else:
            last.pop("nextLabel", None)
            last.pop("nextHref", None)
        apply_xrefs(s, ch, log)
        log.append(f"ch{s}: part {p} chapter {no} eyebrow {beats[0]['eyebrow']!r} "
                   f"next {last.get('nextLabel')!r}")

    # canvas: one page per part plus the closing; four boards per row inside a page
    cpath = os.path.join(HERE, "canvas.json")
    canvas = json.load(open(cpath, encoding="utf-8"))
    by_file = {a["file"]: a for a in canvas["artboards"]}
    canvas["pages"] = [{"id": page_id(p), "name": f"{part_word(p)} · {PART_TITLE[p]}"}
                       for p in range(6)]
    boards = []
    idx = {}
    for s in ORDER:
        ch = chapters[s]
        p = PART_OF[s]
        a = dict(by_file[ch["file"]])
        k = idx.get(p, 0)
        idx[p] = k + 1
        a.update(x=(k % 4) * 1360, y=(k // 4) * 980, page=page_id(p),
                 title=f"Chapter {CHAPTER_OF[s]}. {ch['title']}")
        boards.append(a)
    canvas["artboards"] = boards

    print("--- references left in prose after the move")
    for s in ORDER:
        for j, b in enumerate(chapters[s]["beats"]):
            for c, k in texts(b):
                if k == "eyebrow":
                    continue
                for m in REF.finditer(c[k]):
                    lo, hi = max(0, m.start() - 30), m.end() + 30
                    print(f"  ch{s}[{j}] {m.group(0)!r}  ...{c[k][lo:hi]}...")
    print("---", len(log), "changes")
    if DRY:
        print("\n".join(log))
        return
    for s in ORDER:
        with open(files[s], "w", encoding="utf-8", newline="\n") as fh:
            json.dump(chapters[s], fh, ensure_ascii=False, indent=1)
            fh.write("\n")
    with open(cpath, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(canvas, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("written")


if __name__ == "__main__":
    main()
