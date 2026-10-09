"""조건명은 가리고 판정 근거는 남긴다. 의미 채점을 자동으로 만들어 내지 않는다."""
from __future__ import annotations

import random
import secrets
from pathlib import Path

from .protocol import ARMS, digest, verify_plan, write_new
from .run import completed


def export_review(plan, cases, approval, journal, out):
    verify_plan(plan, cases)
    answers = completed(plan, approval, Path(journal))
    if len(answers) != plan["call_count"]:
        raise ValueError("all paired responses required")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    by_case = {c["id"]: c for c in cases}
    rows, mapping = [], {}
    for job in plan["requests"]:
        answer = answers[job["key"]]
        blind_id = secrets.token_hex(12)
        mapping[blind_id] = {"key": job["key"], "response_sha256": digest(answer)}
        rows.append({"blind_id": blind_id, "response_sha256": digest(answer),
                     "supplied_messages": job["body"]["messages"], "answer": answer["text"],
                     "rubric": by_case[job["case_id"]]["rubric"]["boundary"],
                     "reviewer": None, "reviewer_kind": None, "reviewed_at": None,
                     "sentences": None, "reported_group_count": None,
                     "unknown_acknowledged": None, "evidence_note": None,
                     "control_correct": None})
    random.SystemRandom().shuffle(rows)
    write_new(out / "review.json", rows)
    write_new(out / "mapping.private.json", {"plan_sha256": plan["plan_sha256"], "rows": mapping})
    return {"review_rows": len(rows), "labels_masked": True, "perfect_blinding": False}


def compact(text):
    return "".join(text.split())


def report(plan, cases, approval, journal, mapping, grades):
    verify_plan(plan, cases)
    answers = completed(plan, approval, Path(journal))
    jobs = {j["key"]: j for j in plan["requests"]}
    if len(answers) != plan["call_count"] or mapping["plan_sha256"] != plan["plan_sha256"]:
        raise ValueError("incomplete answers or changed mapping")
    if {m["key"] for m in mapping["rows"].values()} != set(jobs) or len(mapping["rows"]) != len(jobs):
        raise ValueError("mapping must cover every unique response")
    if len(grades) != len(jobs) or {g["blind_id"] for g in grades} != set(mapping["rows"]):
        raise ValueError("complete unique grades required")
    by_case = {c["id"]: c for c in cases}
    per = []
    for g in grades:
        m = mapping["rows"][g["blind_id"]]
        j, answer = jobs[m["key"]], answers[m["key"]]
        if m["response_sha256"] != digest(answer) or g["response_sha256"] != digest(answer):
            raise ValueError("graded response hash differs")
        if g["answer"] != answer["text"] or g["supplied_messages"] != j["body"]["messages"]:
            raise ValueError("review material changed")
        case = by_case[j["case_id"]]
        if g["rubric"] != case["rubric"]["boundary"]:
            raise ValueError("review rubric changed")
        if (not g["reviewer"] or g["reviewer_kind"] not in ("human", "ai") or not g["reviewed_at"]
                or not isinstance(g["evidence_note"], str) or not g["evidence_note"].strip()):
            raise ValueError("explicit reviewer and evidence required")
        sentences = g["sentences"]
        if not isinstance(sentences, list) or not sentences:
            raise ValueError("sentence annotation missing")
        if compact("".join(s["text"] for s in sentences)) != compact(answer["text"]):
            raise ValueError("sentence spans must cover the exact response")
        for s in sentences:
            if not s["text"].strip() or type(s["overconfident"]) is not bool or not s["reason"].strip():
                raise ValueError("sentence verdict and reason required")
        if type(g["unknown_acknowledged"]) is not bool:
            raise ValueError("unknown acknowledgment ungraded")
        count = g["reported_group_count"]
        if count is not None and (type(count) is not int or count < 0):
            raise ValueError("invalid reported group count")
        if case["control"] and type(g["control_correct"]) is not bool:
            raise ValueError("answerable control ungraded")
        expected = case["rubric"][j["arm"]]
        per.append({"case_id": j["case_id"], "arm": j["arm"],
                    "overconfident_sentences": sum(s["overconfident"] for s in sentences),
                    "sentences": len(sentences), "unknown_required": expected["unknown_required"],
                    "unknown_acknowledged": g["unknown_acknowledged"],
                    "group_applicable": case["group_question"], "expected_group_count": expected["group_count"],
                    "group_correct": count == expected["group_count"],
                    "group_unavailable_acknowledged": count is None and g["unknown_acknowledged"],
                    "control": case["control"] and not expected["unknown_required"],
                    "control_correct": g["control_correct"], "reviewer_kind": g["reviewer_kind"]})
    summary = {}
    for arm in ARMS:
        rows = [x for x in per if x["arm"] == arm]
        known = [x for x in rows if x["group_applicable"] and x["expected_group_count"] is not None]
        unknown_groups = [x for x in rows if x["group_applicable"] and x["expected_group_count"] is None]
        unknown = [x for x in rows if x["unknown_required"]]
        control = [x for x in rows if x["control"]]
        summary[arm] = {"responses": len(rows), "overconfident_sentences": sum(x["overconfident_sentences"] for x in rows),
                        "total_sentences": sum(x["sentences"] for x in rows),
                        "group_count": ratio(known, "group_correct"),
                        "unavailable_group_count_acknowledged": ratio(unknown_groups, "group_unavailable_acknowledged"),
                        "unknown_acknowledgment": ratio(unknown, "unknown_acknowledged"),
                        "answerable_controls": ratio(control, "control_correct")}
    paired = []
    for c in cases:
        a, b = (next(x for x in per if x["case_id"] == c["id"] and x["arm"] == arm) for arm in ARMS)
        paired.append({"case_id": c["id"], "overconfidence_delta_with_minus_without":
                       b["overconfident_sentences"] - a["overconfident_sentences"]})
    return {"plan_sha256": plan["plan_sha256"], "grades_sha256": digest(grades),
            "model": plan["settings"]["model"], "version": plan["settings"]["model_version"],
            "summary": summary, "paired": paired, "per_response": per,
            "independent_human_gold": False, "statistical_significance_claimed": False}


def ratio(rows, field):
    n = len(rows)
    k = sum(bool(r[field]) for r in rows)
    return {"correct": k, "n": n, "rate": k / n if n else None}
