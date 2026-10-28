"""모델 실행 인터페이스 — 호스트가 모델 한 걸음을 부르는 자리 하나.

메시지는 프레임워크와 무관한 꼴이다::

    {"role": "system" | "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "args"}]}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str}

도구는 ``{"name", "description", "parameters"(JSON 스키마)}`` 목록이다. 호스트 셋
(:mod:`hosts`)이 각자의 메시지를 이 꼴로 바꿔 :meth:`ModelRunner.step` 을 부르므로, 같은
모델이 세 호스트에서 같은 입력을 받는다.

여기 실린 실제 모델은 둘이다.

- OpenAI 호환 chat completions(Azure 배포 포함). 키는 환경변수에서만 읽고 기록·지문·로그
  어디에도 싣지 않는다.
- ``claude-code:<모델 id>``: 걸음 하나가 헤드리스 ``claude -p`` 실행 하나다. 이 기계에 로그인한
  사람의 Claude 구독으로 돌고 API 키를 쓰지 않는다. 도구는 꺼 두고(격리), 하네스의 도구는
  텍스트 약속(``CALL <도구> {인자}`` 한 줄)으로 건넨다. 그래서 세 호스트의 루프·도구 상한·원본 기록·채점이
  그대로 쓰인다. 실행마다 스트림의 사용률을 읽어 5시간 0.80, 7일 0.90 에서 멈춘다
  (:attr:`ClaudeCodeModel.paused`).
"""
from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from rag_eval.agent_tasks import checkers


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Turn:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    model_version: str = ""
    error: str = ""
    #: 실행기가 알려 준 목록가 환산 비용(구독 실행). 청구액이 아니다.
    list_cost_usd: float | None = None
    #: 읽기 전 원래 답. 읽으며 바뀐 것이 있을 때만 싣는다(흘린 호출 꼴을 나중에 보려고).
    raw: str = ""


class ModelRunner(Protocol):
    def describe(self) -> dict:
        """지문에 싣는 판: kind · family · model · prices(백만 토큰당 USD, 모르면 None)."""

    def step(self, messages: list[dict], tools: list[dict]) -> Turn: ...

    def cost(self, tokens_in: int, tokens_out: int) -> float | None: ...


def _tokens(text: str) -> int:
    return math.ceil(len(text or "") / 4)


#: 경계 문장 안의 예시 id. 가짜 모델이 그것을 근거로 집지 않게 뺀다.
_PLACEHOLDER = {"decision:some-slug"}
_FIRST_SENTENCE = re.compile(r"[^\n]{1,300}")


class FakeModel:
    """결정론적 가짜 모델. 같은 입력이면 같은 출력이다(시간·난수를 안 쓴다).

    정책은 하나다. 도구가 있고 아직 도구 결과가 없으면 검색 도구 하나를 과제 지시로 부른다.
    그 다음(또는 도구가 없으면 곧바로) 받은 재료(시스템 메시지와 도구 결과)에서 노드 id 와
    문서 id 를 처음 나온 차례로 셋까지 집어 ``Sources:`` 줄로 답한다. 집을 것이 없으면
    모른다고 답한다. 품질이 아니라 하네스의 배관과 재현성을 보는 모델이다.
    """

    PREFERRED_TOOLS = ("vinc_search", "search", "vinc_brief", "vinc_http", "vinc")

    def describe(self) -> dict:
        return {"kind": "fake", "family": "fake", "model": "fake-deterministic-1",
                "prices": None}

    def cost(self, tokens_in: int, tokens_out: int) -> float | None:
        return 0.0

    def step(self, messages: list[dict], tools: list[dict]) -> Turn:
        names = [t["name"] for t in tools]
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        results = [m for m in messages if m["role"] == "tool"]
        tin = sum(_tokens(m.get("content") or "") for m in messages)
        pick = next((n for n in self.PREFERRED_TOOLS if n in names), None)
        if pick and not results:
            schema = next(t for t in tools if t["name"] == pick).get("parameters") or {}
            args = {"query": user[:300]}
            if "limit" in (schema.get("properties") or {}):
                args["limit"] = 5
            # A2 의 다른 문(REST·CLI)도 같은 검색 한 번으로 부른다.
            if pick == "vinc_http":
                args = {"method": "POST", "path": "/v1/search", "body": {"query": user[:300], "limit": 5}}
            elif pick == "vinc":
                args = {"command": f"search --query {shlex.quote(user[:300])} --top-k 5"}
            return Turn(text="", tool_calls=[ToolCall(id="fake-call-1", name=pick, args=args)],
                        tokens_in=tin, tokens_out=_tokens(json.dumps(args)),
                        model_version="fake-deterministic-1")
        material = "\n".join(m.get("content") or "" for m in messages
                             if m["role"] in ("system", "tool"))
        ids = [i for i in checkers.node_ids(material) if i not in _PLACEHOLDER]
        ids += re.findall(r"\(source: ([^)\s]+)\)", material)
        ids = list(dict.fromkeys(ids))[:3]
        if ids:
            first = material[material.find(ids[0]):]
            gist = " ".join((_FIRST_SENTENCE.match(first) or [""])[0].split())
            text = f"Based on the material: {gist}\nSources: " + " ".join(f"[{i}]" for i in ids)
        else:
            text = "I do not know: the knowledge available to me does not hold this.\nSources: none"
        return Turn(text=text, tokens_in=tin, tokens_out=_tokens(text),
                    model_version="fake-deterministic-1")


class OpenAIChatModel:
    """OpenAI 호환 chat completions(도구 호출 포함). ``azure:<배포>`` 또는 ``openai:<모델>``.

    Azure 는 ``AZURE_OPENAI_ENDPOINT`` · ``AZURE_OPENAI_API_KEY`` · ``AZURE_OPENAI_API_VERSION``
    (기본 2024-10-21), OpenAI 는 ``OPENAI_API_KEY`` · ``OPENAI_BASE_URL`` 을 읽는다.
    온도 0 과 고정 seed 를 보내지만 원격 모델은 결정론을 약속하지 않는다.
    """

    def __init__(self, spec: str, *, prices: tuple[float, float] | None = None,
                 max_tokens: int = 1024, seed: int = 7, timeout: float = 120.0,
                 transport=None) -> None:
        vendor, _, name = spec.partition(":")
        if vendor not in ("azure", "openai") or not name:
            raise ValueError("model spec is azure:<deployment> or openai:<model>")
        self.vendor, self.name, self.prices = vendor, name, prices
        self.max_tokens, self.seed, self.timeout = max_tokens, seed, timeout
        self._transport = transport or self._post
        if vendor == "azure":
            endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
            if not endpoint or not os.environ.get("AZURE_OPENAI_API_KEY"):
                raise ValueError("azure models need AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY")
            version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
            self.url = (f"{endpoint}/openai/deployments/{name}/chat/completions"
                        f"?api-version={version}")
            self._header = ("api-key", "AZURE_OPENAI_API_KEY")
            self.api_version = version
        else:
            base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            if not os.environ.get("OPENAI_API_KEY"):
                raise ValueError("openai models need OPENAI_API_KEY")
            self.url = f"{base}/chat/completions"
            self._header = ("Authorization", "OPENAI_API_KEY")
            self.api_version = ""

    def describe(self) -> dict:
        return {"kind": "openai-chat", "family": "openai", "vendor": self.vendor,
                "model": self.name, "api_version": self.api_version,
                "prices": list(self.prices) if self.prices else None,
                "max_tokens": self.max_tokens, "seed": self.seed, "temperature": 0}

    def cost(self, tokens_in: int, tokens_out: int) -> float | None:
        if not self.prices:
            return None
        return round(tokens_in / 1e6 * self.prices[0] + tokens_out / 1e6 * self.prices[1], 8)

    def _post(self, url: str, headers: dict, body: dict) -> dict:
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _wire(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                out.append({"role": "assistant", "content": m.get("content") or None,
                            "tool_calls": [{"id": c["id"], "type": "function", "function": {
                                "name": c["name"], "arguments": json.dumps(c["args"], ensure_ascii=False)}}
                                for c in m["tool_calls"]]})
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"],
                            "content": m.get("content") or ""})
            else:
                out.append({"role": m["role"], "content": m.get("content") or ""})
        return out

    def step(self, messages: list[dict], tools: list[dict]) -> Turn:
        header, env = self._header
        key = os.environ.get(env, "")
        headers = {"Content-Type": "application/json",
                   header: key if header == "api-key" else f"Bearer {key}"}
        body: dict = {"messages": self._wire(messages), "temperature": 0, "seed": self.seed,
                      "max_tokens": self.max_tokens}
        if self.vendor == "openai":
            body["model"] = self.name
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t["name"], "description": t.get("description") or "",
                "parameters": t.get("parameters") or {"type": "object", "properties": {}}}}
                for t in tools]
        try:
            data = self._transport(self.url, headers, body)
        except urllib.error.HTTPError as exc:
            # 응답 본문과 헤더는 싣지 않는다(키가 비칠 수 있다). 상태 숫자만.
            return Turn(text="", error=f"http {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return Turn(text="", error=type(exc).__name__)
        choice = (data.get("choices") or [{}])[0].get("message") or {}
        calls = []
        for c in choice.get("tool_calls") or []:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_unparsed": fn.get("arguments") or ""}
            calls.append(ToolCall(id=c.get("id") or "", name=fn.get("name") or "", args=args))
        usage = data.get("usage") or {}
        return Turn(text=choice.get("content") or "", tool_calls=calls,
                    tokens_in=int(usage.get("prompt_tokens") or 0),
                    tokens_out=int(usage.get("completion_tokens") or 0),
                    model_version=str(data.get("model") or ""))


#: 구독 실행이 멈추는 사용률. 다음 걸음은 보내지 않고 회차를 끝낸다.
FIVE_HOUR_STOP = 0.80
SEVEN_DAY_STOP = 0.90
#: 자식 환경에서 뺀다. 키가 있으면 Claude Code 가 구독이 아니라 API 로 청구한다.
_KEY_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
# 도구 호출 약속은 평범한 한 줄이다. XML 꼴로 적으면 모델이 자기 함수 호출 형식(<invoke>)을 흘리고
# 그것이 깨져 호출이 사라졌다(2026-10-28 보정 실행의 A2). 흘린 꼴도 받아 준다.
#: ``CALL CALL 도구`` 처럼 접두를 겹쳐 쓴 줄도 한 호출이다(탐색 3회차).
_CALL_LINE = re.compile(r"^[ \t>*`-]*(?:CALL[ \t]+)+([A-Za-z0-9_.\-]+)[ \t]*", re.M)
_TOOL_CALL = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
_INVOKE = re.compile(r"<invoke\s+name=\"([^\"]+)\"\s*>(.*?)</invoke>", re.S)
_INVOKE_COLON = re.compile(r"<invoke:([A-Za-z0-9_.\-]+)>\s*(.*?)\s*(?:</invoke>|$)", re.S)
#: 호출하려 한 흔적. 이것이 있는데 읽힌 호출이 없으면 약속대로 다시 쓰라고 한 번 묻는다.
_CALL_ATTEMPT = re.compile(r"<invoke|<parameter|<tool_call|<function_calls", re.I)
_REPAIR = ("Your last reply tried to call a tool in a format this harness cannot read. Reply again with "
           "only CALL lines (CALL <tool name> <JSON object>), or with the final answer if you need no tool.")
_PARAM = re.compile(r"<parameter\s+name=\"([^\"]+)\"\s*>(.*?)</parameter>", re.S)
_TOOL_PROTOCOL = (
    "You can use tools. To use one or more, reply with nothing but lines of this exact form, one "
    "line per call:\nCALL <tool name> <arguments as one JSON object>\nFor example:\n"
    "CALL vinc_search {\"query\": \"release checklist\"}\n"
    "The results come back in the next message. When you need no more tools, reply with the final "
    "answer and no CALL line. The tools, as JSON schemas:\n")


def render(messages: list[dict], tools: list[dict]) -> tuple[str, str]:
    """(시스템 글, 대화 글). 도구 호출과 결과는 같은 텍스트 약속으로 다시 쓴다."""
    system = "\n\n".join(m.get("content") or "" for m in messages if m["role"] == "system")
    if tools:
        spec = [{"name": t["name"], "description": t.get("description") or "",
                 "parameters": t.get("parameters") or {}} for t in tools]
        system = f"{system}\n\n{_TOOL_PROTOCOL}{json.dumps(spec, ensure_ascii=False)}".strip()
    turns = []
    for m in messages:
        role = m["role"]
        if role == "user":
            turns.append(f"User:\n{m.get('content') or ''}")
        elif role == "assistant":
            calls = "".join(f"\nCALL {c['name']} {json.dumps(c['args'], ensure_ascii=False)}"
                            for c in m.get("tool_calls") or [])
            turns.append(f"Assistant:\n{m.get('content') or ''}{calls}".rstrip())
        elif role == "tool":
            turns.append(f"Tool result ({m.get('name') or ''}):\n{m.get('content') or ''}")
    if len(turns) == 1 and turns[0].startswith("User:\n"):
        return system, turns[0][len("User:\n"):]
    return system, "\n\n".join(turns + ["Continue as the assistant."])


def _json_args(raw: str) -> dict:
    raw = (raw or "").strip()
    params = dict(_PARAM.findall(raw))
    if params and not raw.startswith("{"):
        # 인자를 JSON 대신 ``<parameter name="k">v</parameter>`` 로 쓴 호출(탐색 3회차).
        return {k: v.strip() for k, v in params.items()}
    try:
        value, _ = json.JSONDecoder().raw_decode(raw) if raw else ({}, 0)
    except json.JSONDecodeError:
        return {"_unparsed": raw}
    return value if isinstance(value, dict) else {"_unparsed": raw}


def parse_tool_calls(text: str, n_messages: int) -> tuple[str, list[ToolCall]]:
    """(첫 호출 앞의 본문, 호출들). 호출 id 는 대화 길이와 순번으로 정한다(실행 안에서 유일).

    받는 꼴: ``CALL 도구 {인자}`` 줄(약속), ``<tool_call>{"name", "args"}</tool_call>``,
    그리고 모델이 흘린 ``<invoke name="도구"><parameter name="k">v</parameter></invoke>`` 와
    ``<parameter name="name">도구</parameter><parameter name="args">{..}</parameter>``.
    """
    text = text or ""
    found: list[tuple[str, dict]] = []
    spans: list[tuple[int, int]] = []
    for m in _CALL_LINE.finditer(text):
        rest = text[m.end():]
        args = _json_args(rest) if rest.lstrip().startswith("{") else {}
        end = m.end()
        if rest.lstrip().startswith("{"):
            lead = len(rest) - len(rest.lstrip())
            try:
                _, used = json.JSONDecoder().raw_decode(rest.lstrip())
                end += lead + used
            except json.JSONDecodeError:
                end = text.find("\n", m.end()) if "\n" in text[m.end():] else len(text)
        found.append((m.group(1), args))
        spans.append((m.start(), end))
    for m in _TOOL_CALL.finditer(text):
        data = _json_args(m.group(1))
        found.append((str(data.get("name") or ""), data.get("args") if isinstance(data.get("args"), dict)
                      else ({"_unparsed": m.group(1)} if "_unparsed" in data else {})))
        spans.append(m.span())
    for m in _INVOKE_COLON.finditer(text):
        name, inner = m.group(1), m.group(2)
        if name == "CALL" and not _CALL_LINE.search(inner):
            # ``<invoke:CALL>도구 {인자}</invoke>``: 껍데기 이름이 약속의 머리말이다(탐색 4회차).
            head = re.match(r"([A-Za-z0-9_.\-]+)\s*(.*)$", inner, re.S)
            if head:
                name, inner = head.group(1), head.group(2)
        found.append((name, _json_args(inner)))
        spans.append(m.span())
    for m in _INVOKE.finditer(text):
        params = dict(_PARAM.findall(m.group(2)))
        if set(params) == {"name", "args"} or (params.get("name") and "args" in params):
            found.append((params["name"].strip(), _json_args(params["args"])))
        else:
            found.append((m.group(1), {k: v.strip() for k, v in params.items()}))
        spans.append(m.span())
    if not _INVOKE.search(text):
        params = dict(_PARAM.findall(text))
        if params.get("name") and "args" in params:
            found.append((params["name"].strip(), _json_args(params["args"])))
            spans.append((text.find("<parameter"), len(text)))
    if not found:
        return text.strip(), []
    # 호출 뒤의 글은 결과를 보기 전에 쓴 것이다. 모델이 "Result: …" 를 지어 쓰고 그 지어낸 id 로
    # 다음 호출을 내는 일이 있어서, 첫 호출부터 글 없이 이어진 호출 묶음만 받고 나머지는 버린다.
    # 근거: 2026-10-28 구독 탐색 2회차, 지어낸 온콜 문서 id 로 vinc_doc_get 을 불렀다.
    pairs = list(zip(spans, found))
    # 다른 호출을 감싼 꼴은 껍데기다(``<invoke:CALL>`` 안의 CALL 줄). 안쪽만 받는다.
    pairs = sorted((p for p in pairs if not any(
        q is not p and p[0][0] <= q[0][0] and q[0][1] <= p[0][1] for q in pairs)),
        key=lambda p: p[0][0])
    kept = [pairs[0]]
    for (start, end), call in pairs[1:]:
        if text[kept[-1][0][1]:start].strip():
            break
        kept.append(((start, end), call))
    # 한 답 안의 똑같은 호출은 하나다. 같은 CALL 줄을 999번 되풀이한 답이 있었다(Sonnet 탐색 회차).
    unique = list(dict.fromkeys((name, json.dumps(args, sort_keys=True, ensure_ascii=False))
                                for _, (name, args) in kept))
    calls = [ToolCall(id=f"tc{n_messages}-{i}", name=name, args=json.loads(args))
             for i, (name, args) in enumerate(unique)]
    return text[:pairs[0][0][0]].strip(), calls


def parse_stream(stdout: str) -> dict:
    """stream-json 한 실행의 답·사용량·사용률·목록가·모델 판."""
    out = {"text": None, "usage": {}, "meter": {}, "cost_usd": None, "model": "",
           "api_key_source": None, "error": ""}
    for line in (stdout or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            out["model"] = str(event.get("model") or "")
            out["api_key_source"] = event.get("apiKeySource")
        elif kind == "rate_limit_event":
            windows = (event.get("rate_limit_info") or {}).get("unifiedWindows") or {}
            out["meter"].update({k: v for k, v in windows.items() if isinstance(v, dict)})
        elif kind == "result":
            if event.get("subtype") != "success" or event.get("is_error"):
                out["error"] = f"claude-code result {event.get('subtype')}"
            else:
                out["text"] = event.get("result")
            out["usage"] = event.get("usage") or {}
            out["cost_usd"] = event.get("total_cost_usd")
    if out["text"] is None and not out["error"]:
        out["error"] = "claude-code returned no result event"
    return out


class ClaudeCodeModel:
    """``claude-code:<모델 id>`` — 걸음 하나가 격리된 헤드리스 ``claude -p`` 하나다.

    격리: 빈 작업 디렉터리, 도구·설정·MCP·세션 파일 없음, 모델 id 고정, API 키 환경변수 제거.
    시스템 글은 임시 파일로, 대화는 stdin 으로 넘긴다(역할의 읽기는 명령줄보다 길다).
    5시간 사용률 0.80 이나 7일 0.90 을 본 뒤에는 :attr:`paused` 에 이유를 남기고 더 보내지 않는다.
    """

    def __init__(self, spec: str, *, claude_bin: str = "claude", timeout: float = 900.0,
                 five_hour_stop: float = FIVE_HOUR_STOP, seven_day_stop: float = SEVEN_DAY_STOP,
                 run=subprocess.run) -> None:
        name = spec.split(":", 1)[1] if spec.startswith("claude-code:") else ""
        if not name:
            raise ValueError("model spec is claude-code:<model id>")
        self.name, self.claude_bin, self.timeout = name, claude_bin, timeout
        self.five_hour_stop, self.seven_day_stop = five_hour_stop, seven_day_stop
        self._run = run
        self.meter: dict = {}
        self.paused = ""
        self.calls = 0

    def describe(self) -> dict:
        return {"kind": "claude-code", "family": "anthropic", "model": self.name, "prices": None,
                "transport": "headless claude -p on the logged-in subscription; tools off, "
                             "harness tools as CALL lines",
                "stops": {"five_hour": self.five_hour_stop, "seven_day": self.seven_day_stop}}

    def cost(self, tokens_in: int, tokens_out: int) -> float | None:
        # 구독은 호출 단가로 청구되지 않는다. 목록가 환산은 걸음마다 list_cost_usd 로 따로 남긴다.
        return None

    def _check_meter(self) -> None:
        five, seven = self.meter.get("five_hour") or {}, self.meter.get("seven_day") or {}
        if float(five.get("utilization") or 0) >= self.five_hour_stop:
            self.paused = f"five_hour {five.get('utilization')} resets {five.get('resetsAt')}"
        elif float(seven.get("utilization") or 0) >= self.seven_day_stop:
            self.paused = f"seven_day {seven.get('utilization')} resets {seven.get('resetsAt')}"

    def step(self, messages: list[dict], tools: list[dict], repaired: bool = False) -> Turn:
        if self.paused:
            return Turn(text="", error=f"subscription_paused: {self.paused}")
        system, conversation = render(messages, tools)
        env = {k: v for k, v in os.environ.items() if k not in _KEY_ENV}
        work = tempfile.mkdtemp(prefix="rag-eval-claude-")
        try:
            command = [self.claude_bin, "-p", "--output-format", "stream-json", "--verbose",
                       "--tools", "", "--setting-sources", "", "--strict-mcp-config",
                       "--no-session-persistence", "--model", self.name]
            if system:
                path = os.path.join(work, "system.md")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(system)
                command += ["--system-prompt-file", path]
            try:
                done = self._run(command, input=conversation, capture_output=True, text=True,
                                 encoding="utf-8", cwd=work, env=env, timeout=self.timeout)
            except subprocess.TimeoutExpired:
                return Turn(text="", error="claude-code timeout")
            except OSError as exc:
                return Turn(text="", error=f"claude-code {type(exc).__name__}")
        finally:
            shutil.rmtree(work, ignore_errors=True)
        self.calls += 1
        got = parse_stream(done.stdout)
        self.meter.update(got["meter"])
        self._check_meter()
        if got["api_key_source"] not in (None, "none"):
            # 키로 청구된 실행은 구독 실행이 아니다. 답을 쓰지 않는다.
            return Turn(text="", error=f"claude-code billed {got['api_key_source']}, not the subscription")
        if got["error"]:
            return Turn(text="", error=got["error"], list_cost_usd=got["cost_usd"])
        text, calls = parse_tool_calls(got["text"], len(messages))
        usage = got["usage"]
        tokens_in = sum(int(usage.get(k) or 0) for k in (
            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        turn = Turn(text=text, tool_calls=calls if tools else [], tokens_in=tokens_in,
                    tokens_out=int(usage.get("output_tokens") or 0),
                    model_version=got["model"] or self.name, list_cost_usd=got["cost_usd"],
                    raw="" if (got["text"] or "").strip() == text else got["text"])
        if repaired:
            return turn
        if tools and not calls and _CALL_ATTEMPT.search(got["text"] or ""):
            # 깨진 호출 꼴은 답이 아니다. 같은 걸음 안에서 한 번만 약속대로 다시 쓰게 한다.
            again = self.step(messages + [{"role": "assistant", "content": got["text"] or ""},
                                          {"role": "user", "content": _REPAIR}], tools, repaired=True)
        elif not (got["text"] or "").strip():
            # 성공으로 끝난 빈 답(2026-10-28 탐색 2회차에 한 번). 같은 입력으로 한 번만 다시 묻는다.
            again = self.step(messages, tools, repaired=True)
        else:
            return turn
        again.raw = f"{got['text'] or ''}\n<<asked again>>\n{again.raw or again.text}"
        again.tokens_in += turn.tokens_in
        again.tokens_out += turn.tokens_out
        again.list_cost_usd = round((again.list_cost_usd or 0) + (turn.list_cost_usd or 0), 8)
        return again


def make(spec: str, *, prices: tuple[float, float] | None = None) -> ModelRunner:
    if spec == "fake":
        return FakeModel()
    if spec.startswith("claude-code:"):
        return ClaudeCodeModel(spec)
    return OpenAIChatModel(spec, prices=prices)


def configured_keys() -> dict[str, bool]:
    """실제 모델 키가 이 환경에 있는가(값은 보지 않는다)."""
    return {"azure": bool(os.environ.get("AZURE_OPENAI_API_KEY") and os.environ.get("AZURE_OPENAI_ENDPOINT")),
            "openai": bool(os.environ.get("OPENAI_API_KEY")),
            # 구독 실행은 키가 아니라 이 기계의 Claude Code 로그인을 쓴다. 실행 파일이 있는지만 본다.
            "claude_code": shutil.which("claude") is not None}
