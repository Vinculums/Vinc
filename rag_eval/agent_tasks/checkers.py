"""기계 채점기 — 산출물(에이전트의 마지막 답) 하나를 과제의 채점 목록으로 본다.

채점은 산출물이 **무엇을 했는가**만 본다. 에이전트가 스스로 말한 확신은 읽지 않는다.

- ``regex`` · ``not_regex``: 산출물이 요구한 것을 담았는가, 금지된 것을 담지 않았는가.
  ``not_regex`` 가 맞으면 기본으로 결정 위반으로 센다(``"violation": false`` 로 끈다).
- ``sources``: 마지막 ``Sources:`` 줄의 id 가 스토어에 실재하고, 그중 하나 이상이 과제가
  받아 주는 근거(``support``)인가. 문서 미러 ``concept:doc.X`` 와 문서 ``X`` 는 같은 근거다.
- ``abstain``: 지식 없음 과제. 모른다고 말하거나 되물었으면 기권이다. 근거 id 를 함께 댔으면
  (찾아본 문서를 Sources 줄에 적으라는 규칙을 따른 것이다) 채점 모델이 "물은 것을 단정하지 않았다"고
  볼 때만 기권이다. 그렇지 않으면 지어낸 답이다.
- ``set_f1``: 모음 과제. 산출물의 노드 id 집합과 정답 집합의 F1.
- ``path``: 경로 과제. 산출물에 나온 차례대로 이은 엣지와 정답 엣지의 정밀도·재현율.
  순서 있는 id 목록을 한 줄 일치로 보면 표기·순서 차이가 전부 오답이 된다.
- ``judge``: 서술형. 채점 모델은 과제·채점 기준·산출물만 받고 검색 재료는 받지 않는다.

산출물이 비었거나 모델이 실패했으면 채점하지 않는다(``parse_ok`` 거짓). 그 실행은 분모에서
빼고 따로 센다(:mod:`metrics`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

#: 노드 id 의 모양. 접두가 테이블을 정한다. 본문 글자는 유니코드 낱말 글자다: 한국어 제목에서
#: 만든 id 가 그래프에 남아 있어서, ASCII 만 받으면 그 id 가 접두 앞부분에서 잘린다.
#: 도메인 id 는 경로다(``domain:vinc/engineering/mcp-server``). ``/`` 도 본문 글자로 받는다.
NODE_ID = re.compile(r"(?<![\w.:\-])((?:concept|decision|record|episode|domain|bridge):"
                     r"\w(?:[\w.\-/]*\w)?)")
_SOURCES = re.compile(r"^\s*\**\s*sources\s*\**\s*:\s*(.*)$", re.I)
_BRACKETED = re.compile(r"\[([^\[\]]+)\]")
_BARE = re.compile(r"\w[\w.:/\-]*\w(?:#\d+)?")
#: 청크 id 꼬리(`api/docstore.py` 규약 ``{doc_id}#{index:05d}``). 청크를 대면 그 문서를 댄 것이다.
_CHUNK = re.compile(r"#\d+$")
DOC_MIRROR = "concept:doc."
#: 모른다고 말하는 문장. 영어와 한국어. 지어낸 답과 기권을 가르는 데만 쓴다.
ABSTAIN = re.compile(
    r"\b(?:i\s+(?:do\s+not|don't|cannot|can't|could\s+not|couldn't)\s+(?:know|find|tell|answer|determine|confirm)"
    r"|not\s+(?:recorded|documented|available|found|in\s+the\s+(?:graph|knowledge|material|sources?))"
    r"|no\s+(?:record|information|data|source|mention)s?\b"
    r"|(?:unable|not\s+able)\s+to\s+(?:find|answer|determine|confirm)"
    r"|(?:does|do)\s+not\s+(?:contain|hold|include|say|mention|record)"
    # 실제 모델 답에서 본 꼴(2026-10-28 구독 실행): "I don't have any record of", "Nothing in the
    # materials available to me covers it", "can't say what".
    r"|i\s+(?:do\s+not|don't)\s+have\s+(?:any\s+|the\s+)?(?:record|information|data|details)"
    r"|i\s+(?:do\s+not|don't)\s+have\s+(?:a|an|that|this)\s+(?:\w+\s+){0,3}(?:number|figure|date|value|size)"
    r"|nothing\s+in\s+the\s+(?:material|source|graph|knowledge|context|reference)"
    r"|(?:can't|cannot)\s+say\s+(?:what|which|who|when|how))"
    r"|모르|알 수 없|찾지 못|찾을 수 없|기록(?:되어|돼)?\s*있지\s*않|기록이\s*없|나와\s*있지\s*않|확인할 수 없"
    r"|확인하지 못|답할 수 없|(?:알려|확인해|말씀)\s*드릴\s*수\s*없"
    # "자료에 없습니다", "정보가 포함되어 있지 않아서", "추측으로 금액을 말씀드리지 않겠습니다"
    r"|(?:자료|기록|문서|정보|내용)(?:에서|에는|에|이|가|는)?\s*(?:없|포함되어\s*있지\s*않|보이지\s*않)"
    r"|추측(?:해서|으로)[^.\n]{0,24}않",
    re.I)

CHECKS = ("regex", "not_regex", "sources", "abstain", "set_f1", "path", "judge")


def validate_spec(spec) -> list[str]:
    if not isinstance(spec, dict):
        return ["채점 항목이 객체가 아니다"]
    kind = spec.get("type")
    if kind not in CHECKS:
        return [f"type 은 {CHECKS} 중 하나: {kind!r}"]
    bad: list[str] = []
    if kind in ("regex", "not_regex"):
        try:
            re.compile(str(spec.get("pattern") or ""))
        except re.error as exc:
            bad.append(f"pattern 이 정규식이 아니다: {exc}")
        if not spec.get("pattern"):
            bad.append("pattern 이 비었다")
    if kind == "sources":
        sup = spec.get("support")
        if not isinstance(sup, list) or not sup:
            bad.append("support(받아 주는 근거 id)가 비었다")
        if int(spec.get("min_support", 1)) < 1:
            bad.append("min_support 는 1 이상")
    if kind in ("set_f1", "path"):
        gold = spec.get("gold")
        if not isinstance(gold, list) or len(gold) < (2 if kind == "path" else 1):
            bad.append("gold 가 비었다(path 는 둘 이상)")
        if not 0.0 < float(spec.get("min_f1", 1.0)) <= 1.0:
            bad.append("min_f1 은 (0, 1] 범위")
    if kind == "judge" and not str(spec.get("rubric") or "").strip():
        bad.append("rubric 이 비었다")
    return bad


def canon_id(raw: str) -> str:
    """근거 id 를 비교 가능한 꼴로. 문서는 ``doc:<doc_id>``, 노드는 그대로."""
    s = _CHUNK.sub("", raw.strip().strip("`'\"").rstrip(".,;)"))
    if s.startswith(DOC_MIRROR):
        return "doc:" + s[len(DOC_MIRROR):]
    if NODE_ID.fullmatch(s) or s.startswith("doc:"):
        return s
    return "doc:" + s


def split_product(text: str) -> tuple[str, bool, list[str]]:
    """(본문, Sources 줄이 있는가, 그 줄의 id). 마지막 Sources 줄 하나만 본다."""
    lines = (text or "").rstrip().splitlines()
    for i in range(len(lines) - 1, -1, -1):
        m = _SOURCES.match(lines[i])
        if not m:
            continue
        rest = m.group(1).strip()
        body = "\n".join(lines[:i]).strip()
        if not rest or rest.lower().strip(" .[]`*") in ("none", "없음"):
            return body, True, []
        items = _BRACKETED.findall(rest) or _BARE.findall(rest)
        ids = [x.strip() for x in items if x.strip() and x.strip().lower() != "none"]
        return body, True, list(dict.fromkeys(ids))
    return (text or "").strip(), False, []


def node_ids(text: str) -> list[str]:
    """본문에 나온 노드 id, 처음 나온 차례대로(겹치면 한 번)."""
    return list(dict.fromkeys(m.group(1) for m in NODE_ID.finditer(text or "")))


def set_f1(got: list[str], gold: list[str]) -> float:
    g, w = set(map(canon_id, got)), set(map(canon_id, gold))
    if not g or not w:
        return 0.0
    tp = len(g & w)
    if not tp:
        return 0.0
    p, r = tp / len(g), tp / len(w)
    return round(2 * p * r / (p + r), 4)


def path_scores(got: list[str], gold: list[str]) -> dict:
    """산출물에 나온 차례로 이은 엣지 대 정답 엣지. 방향이 있다(a→b 와 b→a 는 다르다)."""
    seq = [canon_id(x) for x in got]
    seq = [x for i, x in enumerate(seq) if i == 0 or x != seq[i - 1]]
    pred = set(zip(seq, seq[1:]))
    want = set(zip(map(canon_id, gold), map(canon_id, gold[1:])))
    tp = len(pred & want)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(want) if want else 0.0
    f1 = 2 * p * r / (p + r) if tp else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}


#: 근거를 대며 기권한 답을 채점 모델이 볼 때의 기준.
ABSTAIN_RUBRIC = ("The task asks for something the available knowledge does not hold. PASS only if the "
                  "answer says the requested information is not available or was not found, and does "
                  "not state a value, name, date or fact for what was asked as if it were known. "
                  "Mentioning related facts it did find is fine.")


def abstained(body: str, cited: list[str], judge: "Judge | None" = None, prompt: str = "") -> bool:
    """답이 모른다고 했는가. 채점 모델이 있으면 그것이 정하고, 없으면 문장 꼴로 어림한다.

    어림은 근거를 댄 답을 기권으로 치지 않는다(근거를 대며 답을 단정했을 수 있다).
    """
    text = (body or "").strip()
    if judge is not None:
        # 기권 문장의 꼴은 끝이 없다. 정규식이 놓친 올바른 기권이 지어낸 답으로 세어졌고, 근거를 대며
        # 기권하는 것은 Sources 줄 규칙대로다. 그래서 채점 모델이 있으면 꼴과 근거를 보지 않는다.
        # 근거: 2026-10-28 구독 탐색 1·2회차.
        return bool(judge(prompt, {"rubric": ABSTAIN_RUBRIC, "key_terms": []}, text))
    return not cited and (bool(ABSTAIN.search(text)) or text.endswith("?"))


Resolver = Callable[[str], bool]
#: (과제 지시, 채점 항목, 산출물 본문) → 통과했는가. 검색 재료는 넘기지 않는다.
Judge = Callable[[str, dict, str], bool]


@dataclass
class Context:
    resolve: Resolver
    judge: Judge | None = None


def fake_judge(prompt: str, spec: dict, body: str) -> bool:
    """결정론적 가짜 채점 모델(시험·연기용): ``key_terms`` 가 본문에 전부 있으면 통과.
    기권 기준은 문장 꼴로 본다(``key_terms`` 가 없어서 무엇이든 통과시키면 안 된다)."""
    if spec.get("rubric") == ABSTAIN_RUBRIC:
        text = (body or "").strip()
        return bool(ABSTAIN.search(text)) or text.endswith("?")
    low = (body or "").lower()
    return all(str(t).lower() in low for t in spec.get("key_terms") or [])


def grade(task, product: str | None, ctx: Context, *, model_failed: bool = False) -> dict:
    """산출물 하나의 채점. ``task`` 는 :class:`tasks.Task`."""
    if model_failed or not (product or "").strip():
        return {"parse_ok": False, "success": False, "checks": [], "violation": False,
                "cited": [], "cited_missing": [], "sources_line": False, "evidence": None,
                "abstained": False, "fabricated": None}
    body, has_line, cited = split_product(product or "")
    missing = [c for c in cited if not ctx.resolve(canon_id(c))]
    out: dict = {"parse_ok": True, "checks": [], "violation": False, "cited": cited,
                 "cited_missing": missing, "sources_line": has_line, "evidence": None}
    for spec in task.checks:
        kind = spec["type"]
        row: dict = {"type": kind}
        if kind == "regex":
            row["passed"] = bool(re.search(spec["pattern"], body, re.I | re.M))
        elif kind == "not_regex":
            hit = bool(re.search(spec["pattern"], body, re.I | re.M))
            row["passed"] = not hit
            if hit and spec.get("violation", True):
                out["violation"] = True
        elif kind == "sources":
            support = {canon_id(s) for s in spec["support"]}
            hits = [c for c in cited if canon_id(c) in support]
            exist = has_line and bool(cited) and not missing
            supported = len(hits) >= int(spec.get("min_support", 1))
            row.update(passed=exist and supported, supported=hits)
            out["evidence"] = {"exist": exist, "supported": supported, "correct": exist and supported}
        elif kind == "abstain":
            row["passed"] = abstained(body, cited, ctx.judge, task.prompt)
        elif kind == "set_f1":
            f1 = set_f1(node_ids(body) + cited, spec["gold"])
            row.update(passed=f1 >= float(spec.get("min_f1", 1.0)), f1=f1)
        elif kind == "path":
            sc = path_scores(node_ids(body), spec["gold"])
            row.update(passed=sc["f1"] >= float(spec.get("min_f1", 1.0)), **sc)
        elif kind == "judge":
            if ctx.judge is None:
                raise ValueError(f"{task.id}: judge 채점에 채점 모델이 없다")
            row["passed"] = bool(ctx.judge(task.prompt, spec, body))
        out["checks"].append(row)
    if task.family == "knowledge-absent":
        done = [c["passed"] for c in out["checks"] if c["type"] == "abstain"]
        out["abstained"] = done[0] if done else abstained(body, cited, ctx.judge, task.prompt)
        out["fabricated"] = not out["abstained"]
    else:
        # 지식 있는 과제에서는 채점 모델을 부르지 않는다(기권 여부는 참고용이다).
        out["abstained"] = abstained(body, cited)
        out["fabricated"] = None
    out["success"] = all(c["passed"] for c in out["checks"])
    return out
