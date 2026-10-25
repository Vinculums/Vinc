"""모델 실행 인터페이스 — 호스트가 모델 한 걸음을 부르는 자리 하나.

메시지는 프레임워크와 무관한 꼴이다::

    {"role": "system" | "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "args"}]}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str}

도구는 ``{"name", "description", "parameters"(JSON 스키마)}`` 목록이다. 호스트 셋
(:mod:`hosts`)이 각자의 메시지를 이 꼴로 바꿔 :meth:`ModelRunner.step` 을 부르므로, 같은
모델이 세 호스트에서 같은 입력을 받는다.

모델이 루프 전체를 쥐는 실행기(헤드리스 ``claude -p`` 같은 구독 실행)는 이 인터페이스가
아니라 호스트로 붙는다: 그쪽은 걸음마다 부를 수 없고 대화 하나를 통째로 돌려준다.
:class:`hosts.Host` 의 ``run`` 이 같은 :class:`hosts.RunOutput` 을 돌려주면 채점·기록·지표는
그대로 쓴다.

여기 실린 실제 모델은 OpenAI 호환 chat completions 하나다(Azure 배포 포함). 키는
환경변수에서만 읽고 기록·지문·로그 어디에도 싣지 않는다.
"""
from __future__ import annotations

import json
import math
import os
import re
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

    PREFERRED_TOOLS = ("vinc_search", "search", "vinc_brief")

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


def make(spec: str, *, prices: tuple[float, float] | None = None) -> ModelRunner:
    if spec == "fake":
        return FakeModel()
    return OpenAIChatModel(spec, prices=prices)


def configured_keys() -> dict[str, bool]:
    """실제 모델 키가 이 환경에 있는가(값은 보지 않는다)."""
    return {"azure": bool(os.environ.get("AZURE_OPENAI_API_KEY") and os.environ.get("AZURE_OPENAI_ENDPOINT")),
            "openai": bool(os.environ.get("OPENAI_API_KEY"))}
