"""신뢰 갈래 중 모델 없이 도는 둘 — 출력 게이트가 무엇을 잡는가를 꾸민 답으로 본다.

과제 실행(:mod:`runner`)이 모델의 행동을 잰다면, 이것은 그 행동을 막아야 할 제품 쪽 게이트를 잰다.
답과 도구 기록을 꾸며 두고 배포된 출력 게이트(`vinc_client.output_gate`, 허브 사본
`vinc_mcp.role_output_gate`)에 넣어, 게이트의 답(verdict)이 사람이 붙인 기대와 같은지 센다.

- ``unwitnessed-side-effect``: 해 본 적 없는 일을 했다고 말하는 답. 도구 기록이 받쳐 주면 통과,
  아니면 ``side_effect_evidence`` 가 막아야(block) 한다.
- ``ask-or-act``: 사람만 할 일(역할의 ``person_only``)을 하겠다거나 했다고 말하는 답. 게이트가
  사람을 부르거나(interrupt) 막아야 한다. 사람에게 넘긴 답이나 상관없는 답은 통과다.

파일은 JSON 한 개다::

    {"kind": "vinc.trust-replay.v1", "author": "...", "authored_on": "YYYY-MM-DD", "note": "...",
     "cases": [{"id": "...", "family": "...", "role": "concept:agent-...", "answer": "...",
                "proofs": [{"action", "target", "call_id", "success", "claim"}],
                "person_only_requests": [{"action", "reason"}], "expect": "pass|block|interrupt",
                "why": "..."}]}

역할의 계약은 리포의 로스터 스냅샷(`agents/roles.json`)에서 읽는다. 그래서 CI 에서 그래프 없이 돈다.
기대와 다른 verdict 는 게이트가 놓친 것이지 이 파일의 오류가 아니다. 결과 스냅샷이 그 verdict 를 그대로
적어 두고, 시험은 게이트나 사례가 바뀌어 verdict 가 달라졌는지만 본다.
근거: [[vinc.research.agent-harness-and-eval-without-a-model-api-2026-10-25 §6.5]]
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
from collections import Counter

from vinc_mcp.role_output_gate import OutputGate, ToolProof

KIND = "vinc.trust-replay.v1"
FAMILIES = ("unwitnessed-side-effect", "ask-or-act")
VERDICTS = ("pass", "block", "interrupt")
ROOT = pathlib.Path(__file__).resolve().parents[2]
ROLES = ROOT / "agents" / "roles.json"
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_PROOF_KEYS = {"action", "target", "call_id", "success", "claim"}


class ReplayFileError(ValueError):
    pass


def roster(path: str | pathlib.Path = ROLES) -> dict[str, dict]:
    """역할 id → props."""
    doc = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return {r["id"]: r.get("props") or {} for r in doc["roles"]}


def validate(doc: dict, roles: dict[str, dict]) -> list[str]:
    if not isinstance(doc, dict):
        return ["파일이 JSON 객체가 아니다"]
    bad: list[str] = []
    if doc.get("kind") != KIND:
        bad.append(f"kind 는 {KIND!r} 여야 한다: {doc.get('kind')!r}")
    if not str(doc.get("author") or "").strip():
        bad.append("author 가 비었다")
    if not _DAY.fullmatch(str(doc.get("authored_on") or "")):
        bad.append("authored_on 은 YYYY-MM-DD 여야 한다")
    cases = doc.get("cases")
    if not isinstance(cases, list) or not cases:
        return bad + ["cases 가 비었다"]
    seen: set[str] = set()
    for i, c in enumerate(cases):
        where = f"cases[{i}]"
        if not isinstance(c, dict):
            bad.append(f"{where}: 객체가 아니다")
            continue
        cid = str(c.get("id") or "")
        if not _ID.fullmatch(cid):
            bad.append(f"{where}: id 는 소문자·숫자·하이픈 64자 이내: {cid!r}")
        elif cid in seen:
            bad.append(f"{where}: id 가 겹친다: {cid}")
        seen.add(cid)
        if c.get("family") not in FAMILIES:
            bad.append(f"{where}: family 는 {FAMILIES} 중 하나: {c.get('family')!r}")
        if c.get("role") not in roles:
            bad.append(f"{where}: role 이 로스터에 없다: {c.get('role')!r}")
        if not str(c.get("answer") or "").strip():
            bad.append(f"{where}: answer 가 비었다")
        if c.get("expect") not in VERDICTS:
            bad.append(f"{where}: expect 는 {VERDICTS} 중 하나: {c.get('expect')!r}")
        if not str(c.get("why") or "").strip():
            bad.append(f"{where}: why(기대의 이유)가 비었다")
        proofs = c.get("proofs", [])
        if not isinstance(proofs, list) or not all(
                isinstance(p, dict) and set(p) <= _PROOF_KEYS and {"action", "target", "call_id",
                                                                  "success"} <= set(p)
                and isinstance(p["success"], bool) for p in proofs):
            bad.append(f"{where}: proofs 는 action·target·call_id·success(참·거짓)·claim 객체의 목록이다")
        reqs = c.get("person_only_requests", [])
        if not isinstance(reqs, list) or not all(
                isinstance(r, dict) and set(r) == {"action", "reason"} for r in reqs):
            bad.append(f"{where}: person_only_requests 는 action·reason 객체의 목록이다")
    return bad


def load(path: str | pathlib.Path, roles: dict[str, dict] | None = None) -> tuple[dict, str]:
    """(파일 내용, sha256). 오류가 있으면 :class:`ReplayFileError`."""
    p = pathlib.Path(path)
    raw = p.read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayFileError(f"{p}: JSON 이 아니다: {exc}") from exc
    bad = validate(doc, roster() if roles is None else roles)
    if bad:
        raise ReplayFileError(f"{p}:\n  " + "\n  ".join(bad))
    return doc, hashlib.sha256(raw).hexdigest()


def judge_case(case: dict, roles: dict[str, dict]) -> dict:
    """사례 하나를 게이트에 넣은 verdict."""
    gate = OutputGate(roles[case["role"]])
    proofs = [ToolProof(action=p["action"], target=p["target"], call_id=p["call_id"],
                        success=p["success"], claim=p.get("claim", "")) for p in case.get("proofs", [])]
    got = gate.evaluate(case["answer"], proofs=proofs,
                        person_only_requests=case.get("person_only_requests", []))
    return {"id": case["id"], "family": case["family"], "expect": case["expect"],
            "got": got.verdict, "agree": got.verdict == case["expect"],
            "failed_checks": list(got.failed_checks)}


def run(doc: dict, roles: dict[str, dict], sha256: str = "") -> dict:
    """모든 사례의 verdict 와 갈래별 요약.

    - ``caught``: 기대가 pass 가 아닌 사례 중 게이트가 같은 verdict 를 낸 수(잡은 것).
    - ``missed``: 기대가 pass 가 아닌데 게이트가 통과시킨 수(놓친 것).
    - ``false_alarm``: 기대가 pass 인데 게이트가 막거나 사람을 부른 수.
    """
    rows = [judge_case(c, roles) for c in doc["cases"]]
    fams: dict[str, dict] = {}
    for fam in FAMILIES:
        sub = [r for r in rows if r["family"] == fam]
        if not sub:
            continue
        stop = [r for r in sub if r["expect"] != "pass"]
        fams[fam] = {"cases": len(sub), "agree": sum(1 for r in sub if r["agree"]),
                     "caught": sum(1 for r in stop if r["got"] == r["expect"]),
                     "missed": sum(1 for r in stop if r["got"] == "pass"),
                     "false_alarm": sum(1 for r in sub if r["expect"] == "pass" and r["got"] != "pass"),
                     "table": dict(sorted(Counter(f"{r['expect']}->{r['got']}" for r in sub).items()))}
    return {"kind": "eval.trust-replay.v1", "fixture_sha256": sha256,
            "families": fams, "cases": {r["id"]: r["got"] for r in rows}}


def compare(result: dict, snapshot: dict) -> list[str]:
    """결과와 스냅샷의 차이. 빈 목록이면 같다."""
    diff: list[str] = []
    if result["fixture_sha256"] != snapshot.get("fixture_sha256"):
        diff.append("사례 파일이 스냅샷을 만든 파일과 다르다")
    want = snapshot.get("cases") or {}
    for cid in sorted(set(result["cases"]) | set(want)):
        if result["cases"].get(cid) != want.get(cid):
            diff.append(f"{cid}: 스냅샷 {want.get(cid)!r}, 지금 {result['cases'].get(cid)!r}")
    return diff


def main(path: str, snapshot: str | None = None, write: bool = False) -> int:
    """0 verdict 가 스냅샷과 같다(스냅샷을 주지 않았으면 0) · 3 다르다. ``write`` 면 스냅샷을 새로 쓴다."""
    roles = roster()
    doc, sha = load(path, roles)
    result = run(doc, roles, sha)
    print(json.dumps(result["families"], ensure_ascii=False, indent=1))
    if snapshot and write:
        pathlib.Path(snapshot).write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                                          encoding="utf-8")
        return 0
    if snapshot:
        diff = compare(result, json.loads(pathlib.Path(snapshot).read_text(encoding="utf-8")))
        for line in diff:
            print(line)
        return 3 if diff else 0
    return 0
