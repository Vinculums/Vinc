"""The Hermes memory provider, against a fake hub and, where Hermes is installed, the real host.

The unit tests stand in for Hermes's side (its context, its secret lookup and its
abstract class) and drive the provider over an httpx transport. The last test
loads the provider the way Hermes does, through the entry point, and skips when
Hermes is not importable.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from vinc.integrations import hermes
from vinc.integrations.hermes import VincMemoryProvider, parse_config, read_config, register
from vinc.sdk.tools import BRIEF_NAME, SEARCH_NAME

SK_KEY = "vinc_sk_" + "b" * 32
RO_KEY = "vinc_ro_" + "a" * 32
TEAM = "01a0bc92-ce1d-7867-b970-72cffed2dd89"

RESOLVED = {
    "status": "resolved",
    "node": {"id": "decision:tokens-are-oklch", "title": "Colour tokens are stored as OKLCH",
             "domain": "vinc/design/tokens", "validity": "current"},
    "one_liner": "Tokens keep OKLCH so contrast can be checked per theme.",
    "excerpts": [{"doc_id": "vinc.design.tokens", "doc_title": "Design tokens",
                  "section": "Colour", "excerpt": "Every colour token is OKLCH."}],
}
CHOOSE = {"status": "choose", "candidates": [
    {"id": "decision:a", "title": "Spacing scale A"}, {"id": "decision:b", "title": "Spacing scale B"}]}
SEARCH = {"node_hits": [{"id": "record:spacing-audit", "kind": "record", "title": "Spacing audit"}],
          "rows": []}


def hub_answer(method: str, path: str, body: dict, brief: dict) -> tuple[int, dict]:
    """One fake hub for both the httpx transport and the HTTP server of the host test."""
    if (method, path) == ("POST", "/v1/brief"):
        return 200, brief
    if (method, path) == ("POST", "/v1/search"):
        return 200, SEARCH
    if (method, path) == ("POST", "/v1/fragments"):
        return 200, {"merged": {"nodes": len(body.get("nodes", [])), "edges": len(body.get("edges", []))},
                     "warnings": []}
    if method == "GET" and path.startswith("/v1/nodes/"):
        node_id = path.rsplit("/", 1)[1].replace("%3A", ":")
        if node_id == "decision:missing":
            return 404, {"error": {"code": "not_found", "message": "No such node."}}
        return 200, {"id": node_id, "title": "A node"}
    return 404, {"error": {"code": "route_not_found", "message": "No such v1 route."}}


class Hub:
    def __init__(self, brief: dict = RESOLVED, status: int | None = None) -> None:
        self.brief = brief
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status is not None:
            code = "upstream_unavailable" if self.status >= 500 else "not_authenticated"
            return httpx.Response(self.status, json={"error": {"code": code, "message": "refused"}})
        body = json.loads(request.content or b"{}")
        status, payload = hub_answer(request.method, request.url.path, body, self.brief)
        return httpx.Response(status, json=payload)

    def writes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST" and r.url.path == "/v1/fragments"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VINC_API_KEY", SK_KEY)
    monkeypatch.delenv("VINC_API_URL", raising=False)
    return tmp_path


def started(hub: Hub, home, *, agent_context: str | None = "primary",
            config: dict | None = None) -> VincMemoryProvider:
    if config is not None:
        (home / "vinc.json").write_text(json.dumps(config), encoding="utf-8")
    provider = VincMemoryProvider(transport=httpx.MockTransport(hub))
    extra = {"agent_context": agent_context} if agent_context else {}
    provider.initialize("session-1", hermes_home=str(home), platform="cli", **extra)
    return provider


def tool_names(provider: VincMemoryProvider) -> list[str]:
    return [tool["name"] for tool in provider.get_tool_schemas()]


def test_register_hands_hermes_a_new_provider_each_load():
    collected = []
    ctx = types.SimpleNamespace(register_memory_provider=collected.append)
    register(ctx)
    register(ctx)
    assert [p.name for p in collected] == ["vinc", "vinc"]
    assert collected[0] is not collected[1]
    assert set(hermes.__all__) >= {"VincMemoryProvider", "register"}


def test_availability_reads_the_key_shape_without_the_network(monkeypatch):
    hub = Hub()
    provider = VincMemoryProvider(transport=httpx.MockTransport(hub))
    monkeypatch.delenv("VINC_API_KEY", raising=False)
    assert provider.is_available() is False
    monkeypatch.setenv("VINC_API_KEY", "not-a-vinc-key")
    assert provider.is_available() is False
    monkeypatch.setenv("VINC_API_KEY", RO_KEY)
    assert provider.is_available() is True
    assert hub.requests == []
    assert "VINC_API_KEY" in provider.unavailable_reason()


def test_prefetch_recalls_with_the_client_context_block(home):
    hub = Hub()
    provider = started(hub, home)
    block = provider.prefetch("Why are our colour tokens stored as OKLCH?")
    assert "decision:tokens-are-oklch" in block
    assert "DATA" in block and block.startswith("End every answer")
    assert [r.url.path for r in hub.requests] == ["/v1/brief"]
    assert hub.writes() == []


def test_prefetch_widens_a_tie_with_a_search(home):
    hub = Hub(brief=CHOOSE)
    provider = started(hub, home)
    block = provider.prefetch("spacing scale")
    assert "record:spacing-audit" in block
    assert [r.url.path for r in hub.requests] == ["/v1/brief", "/v1/search"]


@pytest.mark.parametrize("status", [503, 401])
def test_a_failed_recall_skips_the_turn(home, status, caplog):
    provider = started(Hub(status=status), home)
    with caplog.at_level("WARNING"):
        assert provider.prefetch("release checklist") == ""
    assert provider.recall_status() is None
    assert SK_KEY not in caplog.text


def test_tools_offer_record_only_to_the_primary_agent_with_a_write_key(home, monkeypatch):
    assert tool_names(started(Hub(), home)) == [SEARCH_NAME, BRIEF_NAME, "vinc_record"]
    for context in ("cron", "subagent", "flush"):
        assert tool_names(started(Hub(), home, agent_context=context)) == [SEARCH_NAME, BRIEF_NAME]
    # agent_context 를 주지 않는 Hermes 의 cli 는 사람의 대화다.
    assert tool_names(started(Hub(), home, agent_context=None)) == [SEARCH_NAME, BRIEF_NAME, "vinc_record"]
    monkeypatch.setenv("VINC_API_KEY", RO_KEY)
    assert tool_names(started(Hub(), home)) == [SEARCH_NAME, BRIEF_NAME]


def test_cron_platform_without_agent_context_counts_as_cron(home):
    provider = VincMemoryProvider(transport=httpx.MockTransport(Hub()))
    provider.initialize("s", hermes_home=str(home), platform="cron")
    assert tool_names(provider) == [SEARCH_NAME, BRIEF_NAME]


def test_tool_schemas_are_plain_function_schemas(home):
    for tool in started(Hub(), home).get_tool_schemas():
        assert set(tool) == {"name", "description", "parameters"}
        assert tool["parameters"]["type"] == "object"
        assert "title" not in tool["parameters"]


def test_read_tools_answer_with_formatted_text(home):
    hub = Hub()
    provider = started(hub, home)
    found = json.loads(provider.handle_tool_call(SEARCH_NAME, {"query": "spacing", "limit": 3}))
    assert "record:spacing-audit" in found["result"]
    node = json.loads(provider.handle_tool_call(BRIEF_NAME, {"query": "decision:tokens-are-oklch"}))
    assert "Colour tokens are stored as OKLCH" in node["result"]
    assert json.loads(hub.requests[0].content)["limit"] == 3
    assert hub.writes() == []


def test_tool_errors_come_back_as_json(home):
    provider = started(Hub(), home)
    assert "error" in json.loads(provider.handle_tool_call("vinc_delete", {}))
    assert "error" in json.loads(provider.handle_tool_call(SEARCH_NAME, {}))
    down = started(Hub(status=503), home)
    assert "not available" in json.loads(down.handle_tool_call(SEARCH_NAME, {"query": "x"}))["error"]


def test_record_writes_one_episode_about_existing_nodes(home):
    hub = Hub()
    provider = started(hub, home)
    answer = json.loads(provider.handle_tool_call("vinc_record", {
        "title": "Kept OKLCH for tokens", "summary": "The person confirmed it.",
        "about": ["decision:tokens-are-oklch"]}))
    assert answer["result"].startswith("Recorded episode:")
    [write] = hub.writes()
    body = json.loads(write.content)
    episode = body["nodes"][0]
    assert episode["type"] == "episode" and episode["tags"] == ["hermes"]
    assert body["edges"] == [{"from": episode["id"], "to": "decision:tokens-are-oklch", "type": "about"}]
    assert any(r.url.path.startswith("/v1/nodes/") for r in hub.requests)


def test_record_refuses_a_missing_about_target_before_writing(home):
    hub = Hub()
    provider = started(hub, home)
    answer = json.loads(provider.handle_tool_call("vinc_record", {
        "title": "Typo", "about": ["decision:missing"]}))
    assert "not_found" in answer["error"]
    assert hub.writes() == []


@pytest.mark.parametrize("context", ["cron", "subagent"])
def test_record_is_refused_outside_the_primary_agent(home, context):
    hub = Hub()
    provider = started(hub, home, agent_context=context)
    answer = json.loads(provider.handle_tool_call("vinc_record", {"title": "From a job"}))
    assert context in answer["result"]
    assert hub.writes() == []


def test_record_with_a_read_only_key_is_refused(home, monkeypatch):
    monkeypatch.setenv("VINC_API_KEY", RO_KEY)
    hub = Hub()
    answer = json.loads(started(hub, home).handle_tool_call("vinc_record", {"title": "x"}))
    assert "read_only_key" in answer["error"]
    assert hub.requests == []


def test_turns_are_not_recorded_unless_a_person_names_a_space(home):
    hub = Hub()
    provider = started(hub, home)
    provider.sync_turn("Remember the release plan", "Noted.", session_id="session-1")
    assert provider.on_pre_compress([{"role": "user", "content": "x"}]) == ""
    provider.on_memory_write("add", "memory", "a fact")
    provider.on_session_end([{"role": "user", "content": "x"}])
    provider.shutdown()
    assert hub.requests == []


def test_turns_are_recorded_in_the_one_space_a_person_named(home):
    hub = Hub()
    provider = started(hub, home, config={"auto_record_space": TEAM})
    provider.sync_turn("Remember the release plan", "Noted: ship on Friday.", session_id="s-7")
    provider.sync_turn("Remember the release plan", "Noted: ship on Friday.", session_id="s-7")
    assert provider.on_pre_compress([{"role": "user", "content": "x"}]) == ""
    provider.shutdown()
    writes = hub.writes()
    assert len(writes) == 2
    assert {w.url.params.get("space") for w in writes} == {TEAM}
    first, second = (json.loads(w.content) for w in writes)
    # 같은 턴을 다시 보내면 같은 노드와 같은 operation_id 다.
    assert first == second
    episode = first["nodes"][0]
    assert episode["id"].startswith("episode:") and "-hermes-turn-" in episode["id"]
    assert episode["tags"] == ["hermes", "hermes-turn"]
    assert "ship on Friday" in episode["summary"]
    assert first["operation_id"]


@pytest.mark.parametrize("context", ["cron", "subagent", "flush"])
def test_turns_are_never_recorded_outside_the_primary_agent(home, context):
    hub = Hub()
    provider = started(hub, home, agent_context=context, config={"auto_record_space": "personal"})
    provider.sync_turn("A scheduled report", "Done.")
    provider.shutdown()
    assert hub.requests == []


def test_turns_are_not_recorded_with_a_read_only_key(home, monkeypatch):
    monkeypatch.setenv("VINC_API_KEY", RO_KEY)
    hub = Hub()
    provider = started(hub, home, config={"auto_record_space": "personal"})
    provider.sync_turn("Remember this", "Ok.")
    provider.shutdown()
    assert hub.requests == []


def test_config_names_one_space_and_never_holds_the_key(home):
    assert read_config(None) == {"space": "personal", "auto_record_space": None, "base_url": None}
    with pytest.raises(ValueError):
        parse_config({"auto_record_space": [TEAM, "personal"]})
    with pytest.raises(ValueError):
        parse_config({"space": "everyone"})
    provider = VincMemoryProvider()
    fields = {f["key"]: f for f in provider.get_config_schema()}
    assert fields["api_key"]["secret"] is True and fields["api_key"]["env_var"] == "VINC_API_KEY"
    assert not fields["space"].get("secret") and not fields["auto_record_space"].get("secret")

    (home / "vinc.json").write_text(json.dumps({"base_url": "http://127.0.0.1:9"}), encoding="utf-8")
    provider.save_config({"space": TEAM, "auto_record_space": "personal", "api_key": SK_KEY}, str(home))
    stored = json.loads((home / "vinc.json").read_text(encoding="utf-8"))
    assert stored == {"base_url": "http://127.0.0.1:9", "space": TEAM, "auto_record_space": "personal"}
    # 다시 설정하면서 비워 두면 자동 기록이 꺼진다.
    provider.save_config({"space": "personal", "auto_record_space": ""}, str(home))
    stored = json.loads((home / "vinc.json").read_text(encoding="utf-8"))
    assert stored == {"base_url": "http://127.0.0.1:9", "space": "personal"}
    assert SK_KEY not in (home / "vinc.json").read_text(encoding="utf-8")


def test_a_broken_config_fails_initialize(home):
    (home / "vinc.json").write_text(json.dumps({"auto_record_space": ["a", "b"]}), encoding="utf-8")
    with pytest.raises(ValueError):
        VincMemoryProvider().initialize("s", hermes_home=str(home), agent_context="primary")


def test_inside_hermes_the_provider_is_hermes_s_abstract_class(monkeypatch):
    # Hermes 대신 같은 이름의 모듈을 세운다: 추상 메서드 넷과 비밀 조회.
    import abc
    agent = types.ModuleType("agent")
    agent.__path__ = []
    memory = types.ModuleType("agent.memory_provider")

    class MemoryProvider(abc.ABC):
        @property
        @abc.abstractmethod
        def name(self): ...

        @abc.abstractmethod
        def is_available(self): ...

        @abc.abstractmethod
        def initialize(self, session_id, **kwargs): ...

        @abc.abstractmethod
        def get_tool_schemas(self): ...

    memory.MemoryProvider = MemoryProvider
    secrets = types.ModuleType("agent.secret_scope")
    secrets.get_secret = lambda name, default=None: RO_KEY if name == "VINC_API_KEY" else default
    module = importlib.import_module("vinc.integrations.hermes.provider")
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.memory_provider", memory)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", secrets)
    monkeypatch.delenv("VINC_API_KEY", raising=False)
    try:
        reloaded = importlib.reload(module)
        provider = reloaded.VincMemoryProvider()
        assert isinstance(provider, MemoryProvider)
        assert provider.is_available() is True
    finally:
        monkeypatch.undo()
        importlib.reload(module)
        importlib.reload(hermes)


# ── 실제 Hermes ───────────────────────────────────────────────────────────────

def _hermes_installed() -> bool:
    try:
        return all(importlib.util.find_spec(name) is not None
                   for name in ("agent.memory_provider", "plugins.memory", "hermes_cli"))
    except (ImportError, ValueError):
        return False


HOST_SCRIPT = textwrap.dedent('''
    import json, os, sys
    from agent.memory_provider import MemoryProvider
    from agent.memory_manager import MemoryManager
    from hermes_cli.memory_setup import memory_provider_dependency_inputs
    from plugins.memory import find_provider_entry_point, list_memory_provider_names, load_memory_provider

    context = sys.argv[1]
    out = {"listed": "vinc" in list_memory_provider_names(),
           "entry_point": find_provider_entry_point("vinc").value}
    provider = load_memory_provider("vinc")
    out["subclass"] = isinstance(provider, MemoryProvider)
    out["available"] = provider.is_available()
    memory_provider_dependency_inputs("vinc")
    manager = MemoryManager()
    manager.add_provider(provider)
    manager.initialize_for_agent(session_id="host-1", platform="cli", agent_context=context,
                                 hermes_home=os.environ["HERMES_HOME"])
    out["tools"] = sorted(t["name"] for t in manager.get_all_tool_schemas())
    out["prefetch"] = manager.prefetch_all("Why are our colour tokens stored as OKLCH?", session_id="host-1")
    out["search"] = json.loads(manager.handle_tool_call("vinc_search", {"query": "spacing"}))
    out["record"] = json.loads(manager.handle_tool_call("vinc_record", {"title": "Asked to keep this"}))
    manager.sync_all("Remember the release plan", "Noted.", session_id="host-1")
    out["compress"] = manager.on_pre_compress([{"role": "user", "content": "old"}])
    manager.flush_pending(timeout=10)
    manager.shutdown_all()
    print(json.dumps(out))
''')


@pytest.mark.skipif(not _hermes_installed(), reason="Hermes Agent is not installed here")
@pytest.mark.parametrize("context,auto", [("primary", False), ("primary", True), ("cron", True)])
def test_real_hermes_loads_recalls_and_writes_only_as_allowed(tmp_path, context, auto):
    received: list[tuple[str, str, dict]] = []

    class Handler(BaseHTTPRequestHandler):
        def _answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else {}
            path = self.path.split("?", 1)[0]
            received.append((self.command, self.path, body))
            status, payload = hub_answer(self.command, path, body, RESOLVED)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = _answer

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = {"base_url": f"http://127.0.0.1:{server.server_port}"}
    if auto:
        config["auto_record_space"] = TEAM
    (tmp_path / "vinc.json").write_text(json.dumps(config), encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in {"PYTHONHOME", "PYTHONPATH"} and not k.startswith(("VINC_", "HERMES_"))}
    env.update({"HERMES_HOME": str(tmp_path), "VINC_API_KEY": SK_KEY})
    try:
        run = subprocess.run([sys.executable, "-c", HOST_SCRIPT, context], cwd=str(tmp_path), env=env,
                             capture_output=True, text=True, timeout=180)
    finally:
        server.shutdown()
        server.server_close()
    assert run.returncode == 0, run.stderr[-3000:]
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert out["listed"] and out["entry_point"] == "vinc.integrations.hermes:register"
    assert out["subclass"] and out["available"]
    assert "decision:tokens-are-oklch" in out["prefetch"]
    assert "record:spacing-audit" in out["search"]["result"]
    assert out["compress"] == ""
    writes = [(path, body) for method, path, body in received if path.startswith("/v1/fragments")]
    turns = [path for path, body in writes if "-hermes-turn-" in body["nodes"][0]["id"]]
    if context == "primary":
        assert out["tools"] == [BRIEF_NAME, "vinc_record", SEARCH_NAME]
        assert out["record"]["result"].startswith("Recorded episode:")
        assert len(turns) == (1 if auto else 0)
        assert len(writes) == 1 + len(turns)
        assert all(f"space={TEAM}" in path for path in turns)
    else:
        assert out["tools"] == [BRIEF_NAME, SEARCH_NAME]
        assert "cron" in out["record"]["result"]
        assert writes == []
