# -*- coding: utf-8 -*-
"""MCP JSON-RPC 디스패처 — 전송 중립(stdio·Streamable HTTP 가 공유).

MCP 스펙의 서버 최소면만 구현한다: initialize / initialized / ping /
tools/list / tools/call / prompts/list / prompts/get, 그리고 모던 리비전의 server/discover 와
Events 확장(`events/list` · `events/poll` · `events/subscribe` · `events/unsubscribe`, `events.py`).
그 밖의 메서드는 -32601. 도구 실행 오류는 스펙대로 프로토콜 오류가 아니라
result.isError=True 로 돌려준다(호스트가 재시도·수정 가능하도록).

두 세대를 한 디스패처가 함께 받는다. `initialize` 로 여는 클라이언트는 지금 그대로이고,
요청마다 `_meta` 로 버전을 싣는 클라이언트는 `modern.py` 가 가린 뒤 같은 처리를 타고
결과에만 `resultType`·서버 신원·캐시 힌트가 붙는다.

`prompts/*` 는 W2 에서 열었다(역할 열여섯을 프롬프트로 낸다 — 구현은 `roles.py`).
역할과 별개로 그래프와 상관없이 늘 나가는 내장 프롬프트는 `builtin_prompts.py` 가 낸다.
**핸드셰이크 세대의 `resources/*` 는 계속 -32601 이다**(`vinc_conventions`·
`vinc_domains` 가 이미 같은 것을 도구로 내고, 회귀 테스트가 그 갈림을 못 박는다). 모던
세대에서만 Skills 확장을 위해 `skills/list`·`skills/get` 과 스킬 파일의 `resources/list`·
`resources/read` 를 연다. 근거: [[decision:mcp-skills-extension-opens-skill-files-in-the-modern-era-only]]

디스패처가 전송 중립이라 stdio 와 허브가 한 구현을 같이 쓴다 — 호스티드 노출(D3)이
따로 배선할 것 없이 여기서 성립한다.
"""
from __future__ import annotations

import json

from . import __version__
from . import builtin_prompts, community, events, modern, roles, run_tasks, selfhost
from . import tools
from . import toolcall
from .bridge import Bridge

# `initialize` 로 협상하는 리비전. 최신이 앞. 클라이언트가 목록 밖 버전을 요청하면
# **최신**으로 응답한다(스펙: "SHOULD respond with the latest version it supports") —
# 구버전으로 떨어뜨리면 최신 리비전으로 협상하는 비-Anthropic 호스트가 기능을 잃는다.
# 모던 리비전은 이 목록에 넣지 않는다: 그 세대에는 `initialize` 가 없고, 넣으면 목록 밖
# 버전을 요청한 핸드셰이크 클라이언트가 모르는 리비전을 답으로 받는다(`modern.MODERN_VERSIONS`).
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = PROTOCOL_VERSIONS[0]

# tools/list 는 tools.specs() 를 **그대로** 낸다. 여기 description override 계층이
# 있었는데(recall·domains 2건), 덧붙이는 게 아니라 통째로 **교체**하는 구조라
# tools.py 를 고쳐도 호스트에는 안 닿았다 — 바로 이 자리 주석이 "tools.py 가 단일
# 출처"라고 적어 둔 채로. vinc_ingest override 는 F1 때 같은 이유로 이미 원 정의로
# 되돌렸고, 남은 2건의 내용(에피소드 전용 · 도메인별 집계 카운트)도 tools.py 의
# 설명에 흡수했으므로 계층 자체를 걷어낸다. 정본은 tools.py 하나다.


def _tool_specs(hub: bool = False) -> list[dict]:
    return tools.specs(hub=hub)


def _capabilities(modern_era: bool = False) -> dict:
    """initialize 와 server/discover 가 광고하는 능력. 호출마다 새 dict 다. 모던 세대에만 스킬 파일의
    resources 와 Skills 확장이 더해진다(핸드셰이크 세대 응답은 그대로다)."""
    caps: dict = {"tools": {}, "prompts": {}}
    if modern_era:
        caps["resources"] = {}
        # Events 확장(`events.py`)도 모던 세대에서만 광고한다.
        caps["events"] = {}
        caps["extensions"] = {roles.SKILLS_EXTENSION: {}, events.EXTENSION: {},
                              run_tasks.EXTENSION: {}}
    return caps


#: 모던 세대에서만 여는 스킬 메서드.
_SKILL_METHODS = ("skills/list", "skills/get", "resources/list", "resources/read")


def _where_am_i(forced_owner: str | None) -> str | None:
    """`initialize` 에 실을 한 문단. 로컬(`forced_owner` 없음)이면 None.

    ⚠ **계정 조회가 실패해도 문단은 나간다.** 목록을 못 얻는 것과 기본이 개인이라는
    사실은 다른 층이고, 후자가 더 중요하다. 목록만 빠지고 나머지는 남는다.
    """
    if not forced_owner:
        return None                     # 로컬 stdio — 고를 스페이스가 없다
    if forced_owner.startswith("team:"):
        return ("You are connected at a team address, so every call in this session "
                "acts on that team's shared graph (" + forced_owner + ") and the "
                "`space` argument is ignored. Call vinc_whoami to confirm.")
    # 도구마다 붙던 `space` 긴 설명은 한 줄로 줄었다. 고르는 규칙의 전부는 이 문단이 싣는다.
    line = ("You are connected to your personal graph (" + forced_owner + "). "
            "Every call acts here unless you pass `space` with a team id, so a write "
            "with no `space` lands in your personal graph even if you meant a team. "
            "A public community is space='community:<uuid>' and can be read without joining. "
            "vinc_whoami lists every id you can pass and your role in each.")
    try:
        from . import entitlement
        rows = entitlement.list_spaces(forced_owner.split(":", 1)[1])
    except Exception:
        rows = None
    if rows:
        named = ", ".join(
            (r.get("name") or "team") + " (" + r["id"] + ", " + r["role"] + ")"
            for r in rows)
        line += " Teams you can reach: " + named + "."
    elif rows is None:
        # 빈 목록과 "못 물었다"를 가른다 — 뭉개면 "팀이 없다"로 읽힌다.
        line += " The team list could not be fetched right now; vinc_whoami retries it."
    return line


class Dispatcher:
    # 클래스 기본값: `__new__` 로 만들어 `_dispatch` 만 보는 테스트도 initialize 를 탄다.
    hub = False
    client = ""

    def __init__(self, bridge: Bridge, *, hub: bool = False):
        self.bridge = bridge
        self.initialized = False
        # 프로파일은 둘이다: 로컬(stdio, 기본)과 허브. 허브는 이미 계정 그래프 자신이라
        # `target` 을 받지 않고 `space` 로 스페이스를 고른다(tools.specs).
        self.hub = hub
        #: initialize 의 clientInfo(stdio 만). run_start 의 host 기본값이 된다.
        self.client = ""
        #: 웹훅 구독과 전달(`events.Runtime`). 켜진 허브만 채운다. 없으면 poll 만 연다.
        self.events = None

    # 반환 None = 응답 없음(notification 또는 id 없는 요청).
    # forced_owner: 원격 인증경로가 해석한 소유자(멀티테넌트 강제 스탬핑, B-B2).
    # plan: 소유자 티어(B-D2 쿼터에서 사용) — 로컬 stdio 는 둘 다 None.
    # entitlements: 등급에서 해석된 숫자(주제 상한 등) — 사이드카로 그대로 실린다.
    def handle(self, msg: dict, forced_owner: str | None = None,
               plan: str | None = None, quota=None, quota_view=None,
               entitlements: dict | None = None, community_write=None, community_read=None,
               events_auth: dict | None = None, profile: str | None = None) -> dict | None:
        # profile: 연결의 도구 프로파일(`tools.TOOL_PROFILES`). None 이면 `all` 이고 지금과 바이트까지 같다.
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}
        if method is None:
            return None                    # 응답 메시지 등 — 서버는 소비만 한다
        if method.startswith("notifications/"):
            if method == "notifications/initialized":
                self.initialized = True
            return None
        # 세대 판별. 모던 `_meta` 가 없으면 version=None 이고 아래는 지금과 한 글자도 같다.
        version, bad = modern.classify(msg, PROTOCOL_VERSIONS)
        if bad is not None:
            return None if msg_id is None else bad.reply(msg_id)
        try:
            result = self._dispatch(method, params, forced_owner=forced_owner,
                                    plan=plan, quota=quota, quota_view=quota_view,
                                    entitlements=entitlements,
                                    community_write=community_write, community_read=community_read,
                                    modern_version=version, events_auth=events_auth,
                                    profile=profile)
        except events.EventsError as e:
            return None if msg_id is None else e.reply(msg_id)
        except modern.MissingClientCapability as e:
            return None if msg_id is None else e.rejection().reply(msg_id)
        except _MethodNotFound:
            return error_reply(msg_id, -32601, f"method not found: {method}")
        except _QuotaDenied as e:
            # 쿼터 거절문은 tools/call **결과 봉투**로 만들어져 있는데 prompts 에는
            # 그 봉투가 없다. 같은 문장을 프로토콜 오류로 낸다 — 조용히 서빙하는
            # 것보다 낫고, 호스트가 사용자에게 보여 줄 문장은 그대로다.
            return error_reply(msg_id, -32603, str(e))
        except (KeyError, ValueError) as e:
            return error_reply(msg_id, -32602, str(e))
        except Exception as e:              # 브리지 연결 실패 등
            return error_reply(msg_id, -32603, f"{type(e).__name__}: {e}")
        if msg_id is None:
            return None
        if version is not None:
            result = modern.complete(result)
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _dispatch(self, method: str, params: dict, *, forced_owner: str | None = None,
                  plan: str | None = None, quota=None, quota_view=None,
                  entitlements: dict | None = None, community_write=None, community_read=None,
                  modern_version: str | None = None, events_auth: dict | None = None,
                  profile: str | None = None) -> dict:
        if method in run_tasks.METHODS and modern_version is not None:
            return self._tasks(method, params, forced_owner=forced_owner, plan=plan, quota=quota,
                               events_auth=events_auth)
        if method.startswith("events/") and modern_version is not None:
            # Events 확장은 모던 세대에서만 연다. 핸드셰이크 세대에는 -32601 그대로다.
            return self._events(method, params, forced_owner=forced_owner, plan=plan,
                                quota=quota, events_auth=events_auth)
        if method == "server/discover" and modern_version is not None:
            # 모던 세대의 `initialize` 자리. 핸드셰이크 세대에서는 모르는 메서드다(-32601).
            # `instructions` 는 initialize 와 같은 문단이고, 소유자와 팀 이름을 담으므로
            # 허브에서는 private 다.
            out = {"supportedVersions": modern.supported_versions(PROTOCOL_VERSIONS),
                   "capabilities": _capabilities(modern_era=True),
                   "ttlMs": modern.DISCOVER_TTL_MS,
                   "cacheScope": "private" if forced_owner else "public"}
            txt = _where_am_i(forced_owner)
            if txt:
                out["instructions"] = txt
            return out
        if method == "initialize":
            if not self.hub:
                from . import run_protocol
                self.client = run_protocol.client_label(params.get("clientInfo"))
            want = params.get("protocolVersion")
            negotiated = want if want in PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
            info: dict = {"name": "vinc", "version": __version__}
            # icons·websiteUrl 은 SEP-973(스펙 2025-11-25)이 Implementation 에 더한 필드다.
            # **협상된 리비전이 그 필드를 아는 경우에만** 싣는다.
            #
            # ⚠ **게이트를 걷어내지 말 것** — 협상과 무관하게 실었다가 커넥터가 죽었다:
            # [[record:what-the-spec-permits-is-not-what-the-peer-accepts]].
            # PROTOCOL_VERSIONS 에 그 리비전이 없으므로 지금 이 필드는 아무에게도 안
            # 나가고, 나중에 목록에 넣으면 게이트가 저절로 열린다.
            # `sizes` 는 문자열 배열이다(두 스키마의 Icon 정의). 문자열 하나를 보내면 스키마를
            # 검사하는 클라이언트가 serverInfo 를 통째로 거절한다.
            # PNG 를 먼저 놓는 이유: 클라이언트는 PNG·JPEG 지원이 **필수**이고 SVG 는
            # 선택이다(SVG 는 스크립트를 품을 수 있다). 아이콘 자체는 /favicon.* 로
            # 계속 서빙한다 — 프로토콜 밖이라 무해하다.
            # 셀프호스트 허브는 자기 바깥 주소의 아이콘을 싣고, 주소가 없으면 아무것도 안 싣는다.
            icon_base, website = "https://mcp.vincs.io", "https://vincs.io"
            if selfhost.enabled():
                icon_base = website = selfhost.public_url()
            if negotiated >= "2025-11-25" and icon_base:   # ISO 날짜라 사전식 비교가 곧 시간순
                info["icons"] = [
                    {"src": f"{icon_base}/favicon.png",
                     "mimeType": "image/png", "sizes": ["128x128"]},
                    {"src": f"{icon_base}/favicon.svg",
                     "mimeType": "image/svg+xml", "sizes": ["any"]},
                ]
                info["websiteUrl"] = website
            out = {
                # `prompts` 는 로스터가 **비어 있어도** 광고한다. 그래프에 vinc/agent
                # 도메인이 없는 것은 고장이 아니라 상태이고(역할 없는 목록이 정답), 능력을
                # 그래프 내용에 따라 켰다 껐다 하면 호스트가 캐시한 목록과 어긋난다.
                # `listChanged` 는 안 싣는다 — 서버→클라이언트 알림 경로가 없다.
                "protocolVersion": negotiated,
                "capabilities": _capabilities(),
                "serverInfo": info,
            }
            # 스페이스를 **묻기 전에** 알려 준다. `space` 인자가 생기면서 "안 실으면
            # 개인"이 기본이 됐는데(A안), 그 기본은 조용해서 위험하다 — 팀에 쓰려던
            # 호출이 아무 오류 없이 개인 그래프에 쌓인다. 도구로만 알 수 있게 두면
            # **부르기로 마음먹은 세션만** 알게 되고, 읽기만 하는 세션은 영영 모른다.
            # `instructions` 는 첫 도구 호출 전에 무조건 도착하므로 그 구멍을 덮는다.
            # 연결당 한 번이고 스페이스는 연결 중에 안 바뀌므로 신선도 문제도 없다.
            txt = _where_am_i(forced_owner)
            if txt:
                out["instructions"] = txt
            return out
        if method == "ping":
            return {}
        if method == "tools/list":
            # 순서는 레지스트리(`tools.TOOLS` + `HOST_TOOLS`)의 소스 순서이고 계약이다.
            # 재시작이나 해시 시드가 바뀌어도 바이트까지 같아야 호스트의 프롬프트 캐시가
            # 산다(tests/mcp/test_mcp_modern.py 가 다른 해시 시드의 두 프로세스로 대조한다).
            listed = _tool_specs(self.hub)
            if profile is not None and profile != "all":
                # 프로파일은 순서를 지킨 채 걸러 낼 뿐이다. `all` 은 위 목록 그대로다.
                listed = [s for s in listed if tools.in_profile(s["name"], profile)]
            out = {"tools": listed}
            return modern.with_cache_hints(out, method) if modern_version else out
        if method == "tools/call":
            name = params.get("name") or ""
            args = params.get("arguments") or {}
            if not tools.in_profile(name, profile):
                # 목록에 없는 도구는 실행하지 않는다(쿼터도 안 쓴다). 모델이 읽을 수 있게 도구 오류로 낸다.
                return _tool_result(_profile_refusal(name, profile), name, is_error=True)
            if (modern_version is not None and name == run_tasks.SUBMIT_TOOL
                    and isinstance(args, dict) and "inputResponses" in params):
                # MRTR 재시도: 같은 호출에 사람의 답이 실려 왔다. 답이면 이어 가고, 입력창을
                # 거절하거나 닫았으면 실행은 대기 그대로이므로 실행 보기만 돌려준다.
                answer = run_tasks.answer_of(params.get("inputResponses"))
                keep = {k: args[k] for k in ("space", "owner") if k in args}
                args = {**keep, "run": args.get("run"), **({"answer": answer} if answer else {})}
                name = run_tasks.SUBMIT_TOOL if answer else "vinc_runs"
            if not self.hub and self.client:
                from . import run_protocol
                args = run_protocol.with_host(name, args, self.client)
            # REST도 같은 검증→한도→도구 호출을 탄다. MCP 봉투만 여기서 만든다.
            # 근거: [[decision:mcp-argument-errors-are-tool-results-not-protocol-errors-2026-09-24]]
            outcome = toolcall.execute(name, args, self.bridge,
                                       forced_owner=forced_owner, plan=plan, quota=quota,
                                       quota_view=quota_view, entitlements=entitlements,
                                       hub=self.hub, community_write=community_write, community_read=community_read)
            if outcome.kind == "protocol_error":
                raise outcome.error
            if outcome.kind != "ok":
                return outcome.result
            if name == community.WRITE_TOOL and outcome.result.get("isError"):
                return _tool_result(roles.serialize_result(outcome.result), name, is_error=True)
            if modern_version is not None and name == run_tasks.SUBMIT_TOOL:
                carried = run_tasks.carry(outcome.result, params, args)
                if carried is not None:
                    return carried
            return _result_text(_mcp_text(name, args, outcome.result), name, args,
                                result=outcome.result)
        if method in _SKILL_METHODS and modern_version is not None:
            # 스킬은 역할 계약을 읽는다. 프롬프트와 같은 규칙(커뮤니티 거절·쿼터)을 탄다.
            if community.is_space(forced_owner):
                raise PermissionError("Community roles are data. Add a role to your own graph before using it as a skill.")
            if quota is not None and forced_owner:
                denial = quota(forced_owner, plan)
                if denial is not None:
                    raise _QuotaDenied(_denial_text(denial))
            if method == "skills/list":
                out = roles.list_skills(self.bridge, forced_owner, params.get("cursor"))
            elif method == "skills/get":
                out = roles.get_skill(self.bridge, params.get("uri"), forced_owner)
            elif method == "resources/list":
                out = roles.list_skill_resources(self.bridge, forced_owner, params.get("cursor"))
            else:
                out = roles.read_skill_resource(self.bridge, params.get("uri"), forced_owner)
            return modern.with_cache_hints(out, method)
        if method in ("prompts/list", "prompts/get"):
            if community.is_space(forced_owner):
                raise PermissionError("Community roles are data. Add a role to your own graph before using it as a prompt.")
            # 쿼터를 tools/call 과 **같이** 센다. 이 둘은 initialize·ping 과 달리
            # 스토어를 실제로 조회하므로, 무료로 두면 인증된 호출자에게 계량되지 않는
            # 조회 루프가 열린다(허브는 단일 라이터다). 로컬 stdio 는 forced_owner 가
            # 없어 여기 안 걸린다.
            if quota is not None and forced_owner:
                denial = quota(forced_owner, plan)
                if denial is not None:
                    raise _QuotaDenied(_denial_text(denial))
            # 내장 프롬프트는 역할 앞에 놓고, 역할 목록과 역할 본문은 손대지 않는다.
            # 로스터 조회가 실패하면 내장만 내지 않고 오류를 그대로 낸다(빈 로스터와 다르다).
            if method == "prompts/list":
                listed = roles.list_prompts(self.bridge, forced_owner=forced_owner)
                out = {"prompts": builtin_prompts.listed() + listed["prompts"]}
                return modern.with_cache_hints(out, method) if modern_version else out
            name = params.get("name") or ""
            if builtin_prompts.is_builtin(name):
                return builtin_prompts.get(name, params.get("arguments") or {})
            return roles.get_prompt(name,
                                    params.get("arguments") or {},
                                    self.bridge, forced_owner=forced_owner)
        raise _MethodNotFound(method)

    def _events(self, method: str, params: dict, *, forced_owner: str | None, plan,
                quota, events_auth: dict | None) -> dict:
        """Events 확장. 피드 계산은 사이드카, 구독과 전달은 `self.events`(Runtime)."""
        if method == "events/list":
            return modern.with_cache_hints(
                {"events": events.definitions(webhook=self.events is not None)}, method)
        if method not in ("events/poll", "events/subscribe", "events/unsubscribe"):
            raise _MethodNotFound(method)
        owner = forced_owner or self.bridge.owner
        if community.is_space(owner):
            raise events.EventsError(events.FORBIDDEN, "events are not served in community spaces")
        if method == "events/poll":
            self._spend(quota, forced_owner, plan)
            return events.poll(self.bridge, params, owner=owner)
        principal = (events_auth or {}).get("principal")
        if method == "events/unsubscribe":
            if self.events is None:
                return {}
            if not forced_owner or not principal:
                raise events.EventsError(events.FORBIDDEN,
                                         "webhook subscriptions need an authenticated account")
            return self.events.unsubscribe(params, principal=principal)
        if self.events is None:
            raise events.EventsError(events.UNSUPPORTED,
                                     "webhook delivery is not enabled on this server",
                                     {"feature": "deliveryMode", "value": "webhook"})
        if not forced_owner or not principal:
            raise events.EventsError(events.FORBIDDEN,
                                     "webhook subscriptions need an authenticated account")
        self._spend(quota, forced_owner, plan)
        return self.events.subscribe(params, principal=principal, owner=forced_owner,
                                     can_write=bool((events_auth or {}).get("can_write")))

    def _tasks(self, method: str, params: dict, *, forced_owner: str | None, plan, quota,
               events_auth: dict | None) -> dict:
        """Tasks 확장. 작업은 실행 기록에서 매번 계산하므로 이 프로세스는 상태를 들지 않는다."""
        if not run_tasks.tasks_declared(run_tasks.client_capabilities(params)):
            raise modern.MissingClientCapability({"extensions": {run_tasks.EXTENSION: {}}})
        owner = forced_owner or self.bridge.owner
        if community.is_space(owner):
            raise ValueError("tasks are not served in community spaces")
        if method != "tasks/get" and forced_owner and not (events_auth or {}).get("can_write"):
            raise ValueError("this key is read-only: answering or closing a task writes to the run")
        self._spend(quota, forced_owner, plan)
        if method == "tasks/get":
            return run_tasks.get(self.bridge, owner, params,
                                 lambda view: _tool_result(roles.serialize_result(view),
                                                           run_tasks.SUBMIT_TOOL, is_error=False))
        if method == "tasks/update":
            return run_tasks.update(self.bridge, owner, params)
        return run_tasks.cancel(self.bridge, owner, params)

    @staticmethod
    def _spend(quota, owner: str | None, plan) -> None:
        """스토어를 읽는 Events 메서드는 tools/call 과 같이 센다(prompts 와 같은 이유)."""
        if quota is not None and owner:
            denial = quota(owner, plan)
            if denial is not None:
                raise _QuotaDenied(_denial_text(denial))


# 도구 결과 크기 상한 — Anthropic 원격 MCP 요건은 "도구 결과당 최대 25,000 토큰".
# 토큰 대신 **문자수**로 잰다(토크나이저 의존 없이 결정론적). 보수적으로 1토큰≈3자로
# 잡아 25k 토큰 ≈ 75k 자 → 여유를 둬 72k. 초과분을 잘라 보내지 않는 이유: JSON 을
# 자르면 모델이 **깨진 구문**을 받는다 — 조용한 오염보다 명시적 거절이 낫다.
MAX_RESULT_CHARS = 72_000
#: 축소기에 주는 예산은 상한보다 이만큼 작다. 축소기 셋은 예산에 드는 순간 멈추고 **그 뒤에**
#: 표식(shrunk·note·next_cursor, 최대 ~450자)을 붙인다. 상한을 그대로 주면 조금만 넘긴 원본은
#: 표식만큼 다시 넘겨 아래 거부로 떨어진다 — 2026-09-05 라이브에서 concept:agent-dev-account 의
#: depth 1(75,481자)이 축소기 배포 뒤에도 거부돼 `agents_sync --pull` 이 죽었다.
#: 근거: [[record:shrinker-marker-overshoots-the-budget-2026-09-05]]
_MARKER_RESERVE = 1_000

# 응답이 큰 도구별로 '어떻게 줄일지'를 알려준다 — 그냥 "너무 큼"은 행동 불가.
_NARROW_HINT = {
    "vinc_community_read": "use authenticated POST /v1/community/read or the selected version's website export for the complete immutable artifact",
    # 허브의 depth 1 은 이제 축소기가 고리를 줄여 싣는다 — 여기 오는 것은 시드 노드
    # 자신이 상한을 넘을 때뿐이라 depth 를 낮추라는 말은 소용이 없다.
    # 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S4
    "vinc_graph": "the seed node itself is larger than the limit; read it with "
                  "vinc_brief or its document with vinc_doc_get, and its ring with "
                  "vinc_neighbors",
    "vinc_decide": "lower `depth` (e.g. 1)",
    "vinc_recall": "lower `limit` and continue with `next_cursor`, or narrow by "
                   "`domain`/`tags`",
    "vinc_search": "lower `limit`, use detail:'summary' (the default), or continue "
                   "with `next_cursor`",
    "vinc_brief": "lower `max_excerpts`, or ask a narrower question",
    "vinc_domains": "this owner has an unusually large domain set",
    "vinc_suggest_ties": "lower `limit`",
    "vinc_events": "lower `limit` and continue from the returned `cursor`",
}


def _tool_result(text: str, name: str, *, is_error: bool) -> dict:
    """도구 결과 봉투. ChatGPT 인용 쌍은 structuredContent 도 같이 싣는다."""
    out = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if not is_error and name in ("search", "fetch"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            out["structuredContent"] = payload
    return out


def _profile_refusal(name: str, profile: str) -> str:
    """프로파일 밖 도구 호출의 거절문. 프로파일 이름과 전체 목록을 얻는 길을 같이 말한다."""
    return (f"{name or 'This tool'} is not in this connection's tool profile '{profile}', so it "
            "did not run. For the full tool list, connect with profile=all on the MCP URL "
            "(or remove the profile parameter).")


def _mcp_text(name: str, args: dict, result) -> str:
    """tools/call 결과의 본문. 읽기 넷(`tools.LEAN_TOOLS`)은 줄여서 빈칸 없는 JSON 으로 싣는다.
    핸들러 결과는 건드리지 않으므로 REST v1·CLI 의 꼴은 그대로다. `raw` 면 예전 그대로다."""
    lean = tools.lean_result(name, args, result)
    if lean is result:
        return roles.serialize_result(result)
    return json.dumps(lean, ensure_ascii=False, separators=(",", ":"))


def _result_text(text: str, name: str, args: dict, result=None) -> dict:
    """tools/call 결과 봉투. 상한 초과면 **행동 가능한 오류**로 강등한다.

    GM-14: `tools.SHRINKERS` 에 등재된 도구(현재 vinc_search·vinc_brief·vinc_graph)는 거부
    대신 덜 중요한 조각을 덜어 예산 안의 **잘린 결과**로 돌려준다 — 잘렸다는 사실은
    도구별 표식(has_more/next_cursor, shrunk)이 알린다. JSON 을 문자 단위로 자르지 않으므로 구문은 항상 온전하다.
    미등재 도구는 기존 거부를 유지한다(깨진 JSON 보다 명시적 거절이 낫다).

    `result`(핸들러 결과)가 오면 축소기는 줄인 사본을 `page` 가 남은 단계(`tools.lean_project`)에서
    받고, 축소가 끝난 뒤에 `page` 를 뺀다. 축소기가 다음 커서를 `page` 로 쓰기 때문이다."""
    if len(text) <= MAX_RESULT_CHARS:
        return _tool_result(text, name, is_error=False)
    shrink = tools.SHRINKERS.get(name)
    if shrink is not None:
        projected = tools.lean_project(name, args, result) if result is not None else None
        try:
            slim = shrink(projected if projected is not None else json.loads(text),
                          MAX_RESULT_CHARS - _MARKER_RESERVE)
        except Exception:
            slim = None
        if slim is not None:
            if projected is not None:
                slim_text = json.dumps(tools.lean_finish(name, slim, result),
                                       ensure_ascii=False, separators=(",", ":"))
            else:
                slim_text = _mcp_text(name, args, slim)
            if len(slim_text) <= MAX_RESULT_CHARS:
                return _tool_result(slim_text, name, is_error=False)
    hint = _NARROW_HINT.get(name, "request a narrower slice")
    msg = (f"Result too large: {len(text):,} characters (limit {MAX_RESULT_CHARS:,}). "
           f"Nothing was changed. Narrow the request — {hint}.")
    return {"content": [{"type": "text", "text": msg}], "isError": True,
            "_meta": {"vinc_too_large": {"chars": len(text),
                                         "limit": MAX_RESULT_CHARS, "tool": name}}}


class _MethodNotFound(Exception):
    pass


class _QuotaDenied(Exception):
    """쿼터 거절. tools/call 은 결과 봉투로 강등하지만 prompts 에는 그 봉투가 없다."""


def _denial_text(denial: dict) -> str:
    """쿼터 거절 dict(= tools/call 결과 모양)에서 사람이 읽을 문장만 꺼낸다."""
    for part in denial.get("content") or []:
        if part.get("text"):
            return str(part["text"])
    return "rate limit reached"


def error_reply(msg_id, code: int, message: str) -> dict | None:
    """JSON-RPC 오류 봉투 한 벌(프록시도 이것을 쓴다). 알림(`id` 없음)에는 답하지 않는다 → None."""
    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": code, "message": message}}
