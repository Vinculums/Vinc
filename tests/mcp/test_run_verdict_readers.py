# -*- coding: utf-8 -*-
"""실행 기록을 읽는 쪽(`vinc_runs`·이벤트 피드·승인함·알림·실행 보기)이 결론(`verdict`)을 어떻게
보이는가. 실엔진(임베디드 스토어) 위에서 실행 프로토콜을 돌린다.

- 결론을 보고하지 않은 실행은 모든 상태에서 고정 파일 `tests/fixtures/run_readers_main.json` 과
  바이트까지 같다. 그 파일은 이 파일의 `_ordinary` 를 결론 필드가 없는 main 의 코드로 돌려 썼다.
  다시 쓰려면 `VINC_RUN_READERS_WRITE=1` 로 이 시험을 돌린다(읽는 쪽을 일부러 바꿀 때만).
- 결론을 보고한 실행은 사람이 보는 어디에서도 그냥 success 로 읽히지 않는다.

두 키 모드(`VINC_PK_NAMESPACED`)에서 같은 바이트가 나와야 한다: 읽는 쪽은 스토어 키를 벗겨 낸다.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

import pytest

from api import events, fragment as frag, runs
from store.backend import open_store
from store.config import load_config
from vinc_mcp import run_notify, run_protocol, tools
from vinc_mcp.bridge import Bridge

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "run_readers_main.json"
OWNER = "user:run-readers-owner"
PROFILES = {p["id"]: p for p in json.loads(
    (ROOT / "integrations" / "fixtures" / "output_gate" / "proposed-role-checks.json")
    .read_text(encoding="utf-8"))["profiles"]}
MAKER = "concept:agent-dev-core"
#: 예산(벽시계 600초)이 걸린 역할. 같은 게이트를 쓴다.
TIMED = "concept:agent-timed"
CHECKER = "concept:agent-checker"
PLAIN = "The change reads correctly; two findings follow."
T0 = datetime.datetime(2026, 1, 5, 9, 0, 0, tzinfo=datetime.timezone.utc)


def _role(**extra) -> dict:
    props = dict(PROFILES[MAKER])
    props.update({"mission": "m", "tier": "execute", "rung": "strong", "never": [], "reads": [],
                  "act_policy": {"notify": "ask_first"}, **extra})
    return props


ROLES = {
    MAKER: _role(),
    TIMED: _role(budget={"wall_seconds": 600}),
    CHECKER: _role(tier="verify", person_only=[
        {"action": "override_failed_check", "reason": "overriding a failed check"}]),
}


class _Clock:
    """실행 프로토콜의 시계. 시험이 초 단위로 옮긴다."""

    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def at(self, seconds: int) -> None:
        self.now = T0 + datetime.timedelta(seconds=seconds)


class _StoreBridge(Bridge):
    """사이드카가 하는 변환만 흉내 낸다: 역할은 고정 계약, 실행 읽기와 쓰기는 실스토어."""

    def __init__(self, store):
        super().__init__(base="http://127.0.0.1:1", token="t", owner=OWNER)
        self.store = store

    def request(self, method, path, params=None, body=None, timeout=60):
        if path == "/roles":
            return {"executed": True, "found": True, "relations": [], "peers": [],
                    "node": {"id": params["role"], "title": "Agent role",
                             "domain": "vinc/agent/roles", "props": ROLES[params["role"]]}}
        if path == "/runs":
            return runs.list_runs(self.store, owner=params["owner"], run=params.get("run"),
                                  cited=params.get("cited"), role=params.get("role"),
                                  flagged=runs.parse_flagged(params.get("flagged")),
                                  limit=runs.clamp_limit(params.get("limit")),
                                  stored=params.get("stored") == "1")
        if path == "/ingest/fragment":
            return frag.merge_fragment(dict(body), store=self.store)
        return {"executed": False, "error": "unexpected path " + path}


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE_BACKEND", "embedded")
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "local-en")
    clock = _Clock()
    monkeypatch.setattr(run_protocol, "_now", clock)
    notices: list[dict] = []
    # 대기에 들어선 순간 계정에 갈 본문을 모은다(보내지는 않는다).
    monkeypatch.setattr(run_notify, "waiting",
                        lambda owner, eid, props, **_: notices.append(
                            run_notify.payload(owner, eid, props)) or True)
    cfg = load_config({"data_dir": str(tmp_path / "db")})
    store = open_store(cfg)
    store.provision(dim=cfg.embedding_dim)
    try:
        yield store, _StoreBridge(store), clock, notices
    finally:
        store.close()


def _call(bridge, name, **args):
    return tools.call(name, args, bridge)


def _start(bridge, role, run_id):
    return _call(bridge, "vinc_run_start", role=role, task="Review the change.", run_id=run_id)


def _answer(bridge, eid, answer, ask=1, text=""):
    return run_protocol.person_answer({"run": eid, "ask": ask, "answer": answer,
                                       **({"text": text} if text else {})},
                                      bridge, OWNER, actor=OWNER)


def _ordinary(bridge, clock) -> list[dict]:
    """결론을 보고하지 않는 실행을 상태마다 하나씩 만든다. 프로토콜의 응답을 순서대로 돌려준다."""
    said = []

    def say(step, out):
        said.append({"step": step, "out": out})

    say("open", _start(bridge, MAKER, "open-1"))
    blocked = _start(bridge, MAKER, "blocked-step-1")["run"]
    say("gate block", _call(bridge, "vinc_run_submit", run=blocked,
                            output="I merged the pull request and deployed it."))
    person = _start(bridge, MAKER, "wait-person-1")["run"]
    say("person only", _call(bridge, "vinc_run_submit", run=person, output="Ready once approved.",
                             person_only_requests=[{"action": "merge", "reason": "green"}]))
    asks = _start(bridge, MAKER, "wait-ask-1")["run"]
    say("ask first", _call(bridge, "vinc_run_submit", run=asks, output=PLAIN,
                           person_only_requests=[{"action": "notify", "reason": "tell them"}]))
    lapsed = _start(bridge, MAKER, "lapsed-read-1")["run"]
    say("lapsing", _call(bridge, "vinc_run_submit", run=lapsed, output="Ready once approved.",
                         expires_in_s=60,
                         person_only_requests=[{"action": "merge", "reason": "green"}]))
    closed = _start(bridge, MAKER, "lapsed-closed-1")["run"]
    _call(bridge, "vinc_run_submit", run=closed, output="Ready once approved.", expires_in_s=60,
          person_only_requests=[{"action": "merge", "reason": "green"}])
    say("success", _call(bridge, "vinc_run_submit", run=_start(bridge, MAKER, "done-1")["run"],
                         output=PLAIN, usage={"tool_calls": 2}, cited_ids=["record:x"]))
    declined = _start(bridge, MAKER, "declined-1")["run"]
    _call(bridge, "vinc_run_submit", run=declined, output="Plan ready.",
          person_only_requests=[{"action": "migration", "reason": "schema change"}])
    say("decline", _answer(bridge, declined, "decline"))
    approved = _start(bridge, MAKER, "approved-1")["run"]
    _call(bridge, "vinc_run_submit", run=approved, output="Ready once approved.",
          person_only_requests=[{"action": "merge", "reason": "green"}])
    say("approve", _answer(bridge, approved, "approve"))
    say("after approve", _call(bridge, "vinc_run_submit", run=approved, output=PLAIN))
    replied = _start(bridge, MAKER, "replied-1")["run"]
    _call(bridge, "vinc_run_submit", run=replied, output="Ready once approved.",
          person_only_requests=[{"action": "merge", "reason": "green"}])
    say("reply", _answer(bridge, replied, "reply", text="use the release branch"))
    say("abandon", _call(bridge, "vinc_run_submit", run=_start(bridge, MAKER, "abandoned-1")["run"],
                         abandon=True))
    timed = _start(bridge, TIMED, "budget-1")["run"]
    clock.at(700)
    say("budget", _call(bridge, "vinc_run_submit", run=timed, output=PLAIN))
    # 만료된 요청에 제출하면 프로토콜이 저장된 행을 막힌 채 닫는다.
    say("expire", _call(bridge, "vinc_run_submit", run=closed, output=PLAIN))
    return said


def _readers(store, bridge, notices, said) -> dict:
    """사람과 호스트가 실행을 읽는 모든 자리의 출력."""
    listed = _call(bridge, "vinc_runs", limit=200)
    one = {item["id"]: _call(bridge, "vinc_runs", run=item["id"]) for item in listed["runs"]}
    feed = events.public(events.scan(store, owner=OWNER, since=(0, 0),
                                     kinds=("run.waiting", "run.answered")))
    waiting = runs.waiting_runs(store, owner=OWNER, now=run_protocol._now())
    stored = {item["id"]: run_notify.payload(
        OWNER, item["id"], bridge.request("GET", "/runs", params={
            "owner": OWNER, "run": item["id"], "limit": 1, "stored": "1"})["runs"][0]["props"])
        for item in listed["runs"]}
    return {"responses": said, "vinc_runs": listed, "vinc_runs_one": one,
            "events": feed["events"], "approvals": waiting, "notified": notices,
            "notification_payloads": stored}


def _text(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1) + "\n"


def test_runs_that_report_no_verdict_read_as_on_main_byte_for_byte(rig):
    store, bridge, clock, notices = rig
    got = _text(_readers(store, bridge, notices, _ordinary(bridge, clock)))
    if os.environ.get("VINC_RUN_READERS_WRITE") == "1":
        FIXTURE.write_text(got, encoding="utf-8")
    assert got == FIXTURE.read_text(encoding="utf-8")


# ── 결론을 보고한 실행 ──────────────────────────────────────────────────────

FAIL = "Rows 2 and 4 do not hold; the result is in the review notes of the pull request."


def _shown(out: dict) -> list[str]:
    """사람이 실행의 결과로 읽는 모든 문장: 목록·한 실행·이벤트·승인함 카드·알림의 제목."""
    titles = [r["title"] for r in out["vinc_runs"]["runs"]]
    titles += [o["runs"][0]["title"] for o in out["vinc_runs_one"].values()]
    titles += [e["data"]["title"] for e in out["events"]]
    titles += [r["title"] for r in out["approvals"]["runs"]]
    titles += [n["title"] for n in out["notified"] + list(out["notification_payloads"].values())
               if n]
    return titles


def test_a_checker_that_reports_fail_reads_as_failed_wherever_a_person_looks(rig):
    store, bridge, clock, notices = rig
    direct = _start(bridge, CHECKER, "chk-direct-1")["run"]
    done = _call(bridge, "vinc_run_submit", run=direct, output=FAIL, verdict="fail",
                 verdict_note="rows 2 and 4")
    assert (done["outcome"], done["verdict"]) == ("success", "fail")
    asked = _start(bridge, CHECKER, "chk-asked-1")["run"]
    _call(bridge, "vinc_run_submit", run=asked, output=FAIL, verdict="fail", verdict_note="row 2",
          person_only_requests=[{"action": "override_failed_check", "reason": "row 2 flakes"}])
    declined = _start(bridge, CHECKER, "chk-declined-1")["run"]
    _call(bridge, "vinc_run_submit", run=declined, output=FAIL, verdict="fail",
          person_only_requests=[{"action": "override_failed_check", "reason": "row 4"}])
    assert _answer(bridge, declined, "decline")["outcome"] == "blocked"
    out = _readers(store, bridge, notices, [])

    listed = {r["run_id"]: r for r in out["vinc_runs"]["runs"]}
    assert {k: listed["chk-direct-1"][k] for k in ("outcome", "end_reason", "verdict",
                                                    "verdict_note", "title")} == {
        "outcome": "success", "end_reason": "gates passed", "verdict": "fail",
        "verdict_note": "rows 2 and 4", "title": "Run chk-direct-1 of agent-checker: verdict fail"}
    assert listed["chk-asked-1"]["title"] == "Run chk-asked-1 of agent-checker: waiting, verdict fail"
    assert (listed["chk-declined-1"]["outcome"], listed["chk-declined-1"]["end_reason"],
            listed["chk-declined-1"]["verdict"]) == ("blocked", "declined by a person", "fail")
    assert listed["chk-declined-1"]["title"] == (
        "Run chk-declined-1 of agent-checker: blocked, verdict fail")
    assert out["vinc_runs_one"][direct]["runs"][0]["props"]["verdict"] == "fail"
    feed = {(e["name"], e["data"]["run"]): e["data"]["title"] for e in out["events"]}
    assert feed == {("run.waiting", asked): "Run chk-asked-1 of agent-checker: waiting, verdict fail",
                    ("run.answered", declined):
                        "Run chk-declined-1 of agent-checker: blocked, verdict fail"}
    assert [r["title"] for r in out["approvals"]["runs"]] == [
        "Run chk-asked-1 of agent-checker: waiting, verdict fail"]
    assert [n["title"] for n in out["notified"]] == [
        "Run chk-asked-1 of agent-checker: waiting, verdict fail",
        "Run chk-declined-1 of agent-checker: waiting, verdict fail"]
    shown = _shown(out)
    assert len(shown) == 12
    assert all("verdict fail" in t and not t.endswith(": success") for t in shown), shown


def test_a_maker_that_reports_blocked_reads_as_blocked(rig):
    store, bridge, clock, notices = rig
    run = _start(bridge, MAKER, "mk-blocked-1")["run"]
    _call(bridge, "vinc_run_submit", run=run, verdict="blocked",
          verdict_note="every shell command was refused",
          output="Could not run any command in this session; nothing was changed.")
    out = _readers(store, bridge, notices, [])
    item = out["vinc_runs"]["runs"][0]
    assert (item["outcome"], item["verdict"], item["verdict_note"]) == (
        "success", "blocked", "every shell command was refused")
    assert item["title"] == "Run mk-blocked-1 of agent-dev-core: verdict blocked"
    assert all(not t.endswith(": success") for t in _shown(out))


def test_a_run_the_budget_stopped_after_its_verdict_keeps_it_and_names_the_budget(rig):
    store, bridge, clock, notices = rig
    run = _start(bridge, TIMED, "late-1")["run"]
    clock.at(700)
    late = _call(bridge, "vinc_run_submit", run=run, output=PLAIN, verdict="partial",
                 verdict_note="the second migration is not written")
    assert (late["outcome"], late["end_reason"], late["verdict"]) == (
        "blocked", "budget_wall_seconds", "partial")
    item = _readers(store, bridge, notices, [])["vinc_runs"]["runs"][0]
    assert (item["outcome"], item["end_reason"], item["verdict"], item["verdict_note"]) == (
        "blocked", "budget_wall_seconds", "partial", "the second migration is not written")
    assert item["title"] == "Run late-1 of agent-timed: blocked, verdict partial"


def test_the_verdict_is_searchable_by_the_run_title(rig):
    from api import query

    store, bridge, clock, notices = rig
    _call(bridge, "vinc_run_submit", run=_start(bridge, CHECKER, "chk-search-1")["run"],
          output=FAIL, verdict="fail")
    _call(bridge, "vinc_run_submit", run=_start(bridge, CHECKER, "chk-search-2")["run"],
          output=FAIL)
    hits = query.search("verdict fail", owner=OWNER, store=store, top_k=10)
    # 제목이 검색 표면이다: 결론을 보고한 실행만 걸리고, 같은 역할의 보고하지 않은 실행은 걸리지 않는다.
    assert [h["id"] for h in hits.get("node_hits") or []] == [
        run_protocol.episode_id("chk-search-1")], hits


def test_a_verdict_note_keeps_only_visible_characters():
    from store import run_state
    # NUL, ESC, BEL, 방향 바꿈은 빠지고 공백은 하나로 접힌다.
    assert run_state.visible_line("a\x00b\x1b[31mc‮d\x07  e") == "a b [31mc d e"
    assert run_state.readable({"verdict": "fail", "verdict_note": "x​y"})["verdict_note"] == "x y"


def test_a_verdict_written_outside_the_protocol_shows_in_the_listed_title(rig):
    store, bridge, clock, notices = rig
    run = _start(bridge, CHECKER, "chk-patched-1")["run"]
    assert _call(bridge, "vinc_run_submit", run=run, output=PLAIN)["outcome"] == "success"
    # 지금 rev 는 맞지 않는 expected_rev 의 거절(write_conflict)이 알려 준다.
    def patch(rev):
        return frag.merge_fragment({"owner": OWNER, "nodes": [{
            "id": run, "type": "episode", "props_patch": {"verdict": "fail"},
            "expected_rev": rev}]}, store=store)
    refused = patch(10_000)
    out = patch(refused["current_rev"])
    assert out.get("error") is None, (refused, out)
    # 결론은 보호되지 않는 키라 도구 쓰기로 바뀐다. 목록의 제목은 저장된 결론에서 다시 지어 맞는다.
    listed = {r["run_id"]: r for r in _call(bridge, "vinc_runs", limit=200)["runs"]}
    assert listed["chk-patched-1"]["verdict"] == "fail"
    assert listed["chk-patched-1"]["title"] == "Run chk-patched-1 of agent-checker: verdict fail"
