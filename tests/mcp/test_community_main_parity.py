"""있던 커뮤니티 동작이 허브에서 main 과 같은 바이트로 나가고 같은 문장으로 거절되는지.

`fixtures/community_main.json` 은 main 의 코드에서 이 파일을 `WRITE_COMMUNITY_MAIN_FIXTURE=1` 로 돌려 만든
관측값이다(계정으로 나가는 요청 본문과 서명 클레임, 도구 결과, 거절 문장). 이 파일은 main 에 없는 이름을
부르지 않아야 한다: 그래야 같은 파일로 main 에서 고정 본을 다시 만들 수 있다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from vinc_mcp import community, edverify, entitlement, hubhttp, toolcall, tools

FIXTURE = Path(__file__).parent / "fixtures" / "community_main.json"
SEED = bytes(range(32))
A = "community:0191f0aa-1111-7abc-8def-0123456789ab"
USER = "0191f0aa-3333-7abc-8def-0123456789ab"
ACCOUNT = "http://account.invalid"
WRITE, READ = community.WRITE_TOOL, community.READ_TOOL
# main 에 있던 동작만. 새 동작은 test_community_unified_actions.py 가 본다.
MAIN_ACTIONS = (
    "page_save", "page_revert", "talk_create", "talk_comment", "lounge_create",
    "lounge_comment", "react", "report", "moderate", "appoint_operator", "resolve_report",
    "social_edit", "community_join", "community_leave", "community_create", "community_edit",
    "share_create", "share_version", "share_withdraw", "share_moderate", "pack_share", "agent_take",
)
MAIN_READS = ("agent_detail", "agent_export", "agent_take_preview", "agent_take_result")
PAYLOAD = {"slug": "same", "title": "Same", "body": "Text", "summary": "Edit", "baseRevision": None,
           "mutationId": "0191f0aa-9999-7abc-8def-0123456789ab", "channel": "web", "editor": "someone"}
READ_PAYLOAD = {"shareId": "0191f0aa-7777-7abc-8def-0123456789ab", "version": 1, "format": "claude"}
MEMBER = {"owner": A, "member": "user:" + USER, "scope": "full", "plan": "free"}
ERROR = {"error": {"code": "account_code", "message": "Account message.", "request_id": "r1"}}

#: 이 패키지가 일부러 바꾼 자리. 고정 본에 이 치환을 한 뒤에는 바이트가 같아야 하고, 치환마다 실제로 한 번 이상 일어나야 한다.
#: - 읽기 도구의 거절 문장 셋: 피드 읽기가 더해져 "share"·"Agent" 읽기만이 아니게 됐다.
#: - 모르는 동작의 거절 문장: 문장 틀은 같고, 받는 동작 목록 끝에 새 이름이 붙는다.
RENAMED = (
    ("Community share reads require an authenticated account connector.",
     "Community reads require an authenticated account connector."),
    ("Community share reads require an authenticated explicit community space.",
     "Community reads require an authenticated explicit community space."),
    ("Community Agent reads require an explicit community:<uuid> space.",
     "Community reads require an explicit community:<uuid> space."),
    (", ".join(MAIN_ACTIONS), ", ".join(community.ACTIONS)),
    (", ".join(MAIN_READS), ", ".join(community.READ_ACTIONS)),
    # 개설이 빠져 커뮤니티 주소가 없는 쓰기의 거절 문장이 하나로 줄었다.
    ("Community creation requires personal identity; other writes require a community space.",
     "Community writes require an explicit community space and an action of vinc_community_write."),
    ("Community creation uses personal identity; all other actions require community:<uuid> space.",
     "Community writes require an explicit community:<uuid> space."),
)
#: 도구가 받지 않게 된 옛 이름. 고정 본에서는 계정으로 나갔고, 지금은 허브가 거절하고 아무것도 보내지 않는다.
REMOVED = ("talk_create", "talk_comment", "lounge_create", "lounge_comment",
           "community_join", "community_leave", "community_create")
KEPT = tuple(a for a in MAIN_ACTIONS if a not in REMOVED)
#: 그 이름을 부르는 관측 가운데 문장 치환만으로 같아지지 않는 것. 고정 본과 바이트로 대조하지 않고 아래 시험이 따로 본다.
CHANGED = tuple("write:" + a for a in REMOVED) + ("refuse:preflight:create_in_community",)


class Account:
    """계정 내부 라우트 대신 hubhttp.request 를 받는다. 나간 본문·서명 클레임을 적고 정해 둔 답을 준다."""

    def __init__(self):
        self.sent, self.answer = [], (200, {"saved": True, "revision": 1, "graph": "pending"})

    def __call__(self, url, *, data=None, timeout=None, headers=None, **_):
        claims = edverify.verify_assertion(edverify.secret_to_public(SEED), headers["X-Vinc-Assertion"], max_age=120)
        assert claims and claims["exp"] - claims["iat"] == 120 and claims["jti"]
        self.sent.append({"path": url[len(ACCOUNT):], "body": data.decode("utf-8"), "timeout": timeout,
                          "claims": {k: claims[k] for k in ("iss", "aud", "typ", "sub", "bh")}})
        status, body = self.answer
        if status != 200:
            raise hubhttp.HubHTTPError(url, status, body=json.dumps(body) if status else "")

        class Reply:
            def json(self):
                return body
        return Reply()


@pytest.fixture
def account(monkeypatch):
    monkeypatch.setattr(entitlement, "_seed", lambda: SEED)
    monkeypatch.setattr(entitlement, "_account_url", lambda: ACCOUNT)
    fake = Account()
    monkeypatch.setattr(community.hubhttp, "request", fake)
    return fake


def _shape(value):
    if isinstance(value, BaseException):
        return {"raise": type(value).__name__, "text": str(value)}
    if isinstance(value, toolcall.Outcome):
        return {"kind": value.kind, "result": value.result, "message": value.message,
                "error": _shape(value.error) if value.error is not None else None}
    return value


def _run(fn):
    try:
        return _shape(fn())
    except Exception as exc:  # noqa: BLE001 — 거절도 관측값이다.
        return _shape(exc)


def _execute(name, args, auth=MEMBER):
    return toolcall.execute(name, args, None, forced_owner=auth["owner"], hub=True,
                            community_write=lambda a: community.write(auth, a),
                            community_read=lambda a: community.read(auth, a))


def _preflight(name, args, auth):
    return toolcall.preflight(name, args, base_auth={"ok": True, **auth}, address_space=auth["owner"],
                              authenticate=lambda _s: {"ok": False}, valid_space=lambda _s: True)


def _cases():
    """(이름, 계정의 답, 부를 것). 이름은 고정 본의 열쇠다."""
    out = []
    for action in MAIN_ACTIONS:
        args = {"space": A, "action": action, "client_name": " Codex ", "payload": dict(PAYLOAD)}
        auth = MEMBER
        if action == "community_create":
            args.pop("space")
            auth = {**MEMBER, "owner": "user:" + USER}
        out.append(("write:" + action, None, lambda a=args, u=auth: _execute(WRITE, a, u)))
    for action in MAIN_READS:
        args = {"space": A, "action": action, "payload": dict(READ_PAYLOAD)}
        out.append(("read:" + action, (200, {"executed": True, "share": {"id": "s"}}),
                    lambda a=args: _execute(READ, a)))
    base = {"space": A, "action": "page_save", "client_name": "Codex", "payload": {"slug": "same", "body": "x"}}
    bad_writes = {
        "client_name_empty": {"client_name": ""}, "client_name_control": {"client_name": "x\nsecret"},
        "client_name_long": {"client_name": "x" * 121}, "client_name_missing": {"client_name": None},
        "payload_list": {"payload": []}, "payload_missing": {"payload": None},
        "payload_nan": {"payload": {"n": float("nan")}}, "action_unknown": {"action": "share_agent"},
        "action_read_name": {"action": "agent_detail"}, "action_missing": {"action": None},
    }
    for key, over in bad_writes.items():
        args = {k: v for k, v in {**base, **over}.items() if v is not None}
        out.append(("refuse:write:" + key, None, lambda a=args: _execute(WRITE, a)))
    rbase = {"space": A, "action": "agent_detail", "payload": dict(READ_PAYLOAD)}
    bad_reads = {
        "space_missing": {"space": None}, "space_personal": {"space": "personal"},
        "payload_list": {"payload": []}, "action_unknown": {"action": "agent_list"},
        "action_write_name": {"action": "page_save"}, "action_missing": {"action": None},
    }
    for key, over in bad_reads.items():
        args = {k: v for k, v in {**rbase, **over}.items() if v is not None}
        out.append(("refuse:read:" + key, None, lambda a=args: _execute(READ, a)))
    page = {"action": "page_save", "client_name": "Codex", "payload": {"slug": "same"}}
    out += [
        ("refuse:direct:write_personal_owner", None,
         lambda: community.write({**MEMBER, "owner": "user:" + USER}, page)),
        ("refuse:direct:write_read_scope", None, lambda: community.write({**MEMBER, "scope": "read"}, page)),
        ("refuse:direct:write_no_member", None, lambda: community.write({**MEMBER, "member": None}, page)),
        ("refuse:direct:create_from_community", None,
         lambda: community.write(MEMBER, {**page, "action": "community_create"})),
        ("refuse:direct:read_unknown_action", None,
         lambda: community.read(MEMBER, {"action": "agent_list", "payload": {}})),
        ("refuse:direct:read_personal_owner", None,
         lambda: community.read({**MEMBER, "owner": "user:" + USER}, {"action": "agent_detail", "payload": {}})),
        ("refuse:direct:read_no_member", None,
         lambda: community.read({**MEMBER, "member": None}, {"action": "agent_detail", "payload": {}})),
        ("refuse:direct:unavailable_write", None, lambda: community.unavailable_write({}, None)),
        ("refuse:direct:unavailable_read", None, lambda: community.unavailable_read({}, None)),
        ("refuse:preflight:read_personal", None,
         lambda: _preflight(READ, {"action": "agent_detail"}, {**MEMBER, "owner": "user:" + USER})),
        ("refuse:preflight:create_in_community", None,
         lambda: _preflight(WRITE, {"action": "community_create"}, MEMBER)),
        ("refuse:preflight:write_personal", None,
         lambda: _preflight(WRITE, {"action": "page_save"}, {**MEMBER, "owner": "user:" + USER})),
        ("refuse:preflight:generic_write_in_community", None,
         lambda: _preflight("vinc_ingest", {}, MEMBER)),
        ("refuse:preflight:read_only_key", None,
         lambda: _preflight(WRITE, {"action": "page_save"}, {**MEMBER, "scope": "read"})),
    ]
    write_args = {"space": A, "action": "page_save", "client_name": "Codex", "payload": {"slug": "same", "body": "x"}}
    for status in (400, 401, 403, 404, 409, 410, 429, 500, 503):
        out.append((f"account:write:{status}", (status, ERROR), lambda: _execute(WRITE, write_args)))
    read_args = {"space": A, "action": "agent_detail", "payload": dict(READ_PAYLOAD)}
    for status in (400, 404, 409, 429, 500):
        out.append((f"account:read:{status}", (status, ERROR), lambda: _execute(READ, read_args)))
    return out


def _observe(account) -> dict:
    seen = {}
    for name, answer, fn in _cases():
        account.sent.clear()
        account.answer = answer or (200, {"saved": True, "revision": 1, "graph": "pending"})
        seen[name] = {"outcome": _run(fn), "sent": list(account.sent)}
    return seen


def _text(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def test_existing_actions_and_refusals_match_main(account):
    seen = _observe(account)
    if os.environ.get("WRITE_COMMUNITY_MAIN_FIXTURE") == "1":
        FIXTURE.write_text(_text(seen), encoding="utf-8", newline="\n")
        pytest.skip("wrote " + str(FIXTURE))
    main = FIXTURE.read_text(encoding="utf-8")
    for old, new in RENAMED:
        assert old in main, "a listed difference no longer occurs: " + old[:60]
        main = main.replace(old, new)
    expected = json.loads(main)
    assert set(expected) == set(seen)
    for name in CHANGED:
        old = expected.pop(name)
        assert old != seen.pop(name), "a listed difference no longer occurs: " + name
        assert old["sent"] or not name.startswith("write:"), "the main fixture no longer sends " + name
    for name, value in expected.items():
        assert seen[name] == value, name
    assert _text(seen) == _text(expected)


def test_removed_actions_now_stop_in_the_hub(account):
    """고정 본에서 계정으로 나가던 옛 이름이 지금은 허브의 인자 검사에서 멈춘다. 계정에는 한 바이트도 가지 않는다."""
    seen = _observe(account)
    for name in CHANGED:
        assert seen[name]["sent"] == [], name
    for action in REMOVED:
        outcome = seen["write:" + action]["outcome"]
        # 개인 주소로 부르던 개설도 주소를 보기 전에 인자 검사가 거절한다.
        assert outcome["kind"] == "invalid_argument", action
    assert "not accepted by vinc_community_write" in json.dumps(seen["write:talk_create"]["outcome"])
    assert seen["refuse:direct:create_from_community"]["outcome"]["raise"] == "PermissionError"


def test_every_existing_write_leaves_the_hub_with_the_same_envelope(account):
    """고정 본과 별개로 봉투를 읽어 둔다: 동작·payload 는 그대로, 들어온 길은 늘 mcp·full, 이름은 다듬어진 값."""
    seen = _observe(account)
    for action in KEPT:
        (sent,) = seen["write:" + action]["sent"]
        body = json.loads(sent["body"])
        assert sent["path"] == "/internal/mcp/community" and sent["claims"]["typ"] == "community-write"
        assert body == {"sub": USER, "community": A[10:],
                        "action": action, "payload": PAYLOAD, "client_name": "Codex",
                        "channel": "mcp", "scope": "full"}
