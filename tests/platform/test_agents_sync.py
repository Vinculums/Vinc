# -*- coding: utf-8 -*-
"""역할 껍데기 생성기 — 결정론과 리포 정합.

왜 이 파일이 있나: `--check` 는 "스냅샷에서 재생성한 것과 파일이 바이트가 같은가"만
본다. 그 검사는 `render_shell` 이 **결정론**일 때만 뜻이 있다. 같은 입력에서 두 번
다른 바이트가 나오면 게이트는 손대지 않은 파일도 빨갛게 만들고, 곧 꺼진다.

`build_payload` 를 여기서 직접 부르는 이유는 `--pull` 이 네트워크와 토큰을 요구하기
때문이다. 형태를 잡는 부분만 순수 함수로 떼어 두면 오프라인에서 검사할 수 있다.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 렌더러는 `vinc_mcp/roles.py` 로 옮겼다 — `prompts/get` 이 같은 함수를 쓴다(W2).
# 여기서 그 자리를 그대로 임포트하는 이유는, 재수출로 가리면 사본이 둘이 됐을 때도
# 이 파일이 계속 초록이기 때문이다.
from scripts.agents_sync import (GENERATED_MARK, HOOK_SRC,  # noqa: E402
                                 _manifest_docs, dangling_reads, install,
                                 main, main_checkout, read_snapshot,
                                 shell_path)
from vinc_mcp.roles import (RUNG_TO_MODEL, build_payload,  # noqa: E402
                            render_shell, works_with)

_NODE = {
    "id": "concept:agent-example",
    "label": "Agent role: example",
    "domain": "vinc/agent",
    "tags": ["role", "agent"],
    "props": {
        "tier": "execute",
        "rung": "strong",
        "mission": "Does one thing and stops.",
        "reads": ["vinc.canon.repo-workflow", "vinc.spec.dataplane"],
        "gates": ["scripts/gate_forbidden.py"],
        "never": ["merge"],
        "person_only": ["merge"],
    },
}


class Payload(unittest.TestCase):
    def test_role_ids_only(self):
        raw = [_NODE, {"id": "record:not-a-role", "props": {}}]
        got = build_payload(raw)
        self.assertEqual([r["id"] for r in got["roles"]], ["concept:agent-example"])
        self.assertEqual(got["roles"][0]["name"], "agent-example")

    def test_reads_keep_their_order(self):
        """목록은 정렬하지 않는다 — `reads` 는 '이 순서로 읽어라'라서 정렬하면 지시가
        사라진다. dict 키만 정렬한다."""
        got = build_payload([_NODE])["roles"][0]
        self.assertEqual(got["props"]["reads"],
                         ["vinc.canon.repo-workflow", "vinc.spec.dataplane"])
        self.assertEqual(list(got["props"]), sorted(got["props"]))

    def test_payload_is_stable_under_input_order(self):
        second = dict(_NODE, id="concept:agent-another")
        a = build_payload([_NODE, second])
        b = build_payload([second, _NODE])
        self.assertEqual(a, b)


class Shell(unittest.TestCase):
    def test_render_is_deterministic(self):
        role = build_payload([_NODE])["roles"][0]
        self.assertEqual(render_shell(role), render_shell(role))

    def test_frontmatter_carries_name_description_and_model(self):
        role = build_payload([_NODE])["roles"][0]
        head = render_shell(role).splitlines()
        self.assertEqual(head[0], "---")
        self.assertEqual(head[1], "name: agent-example")
        self.assertTrue(head[2].startswith("description: Does one thing"))
        self.assertEqual(head[3], f"model: {RUNG_TO_MODEL['strong']}")

    def test_description_is_one_line(self):
        """프런트매터의 description 은 한 줄이어야 한다. 임무 문장에 줄바꿈이 들어오면
        YAML 이 깨지므로 render 가 접는다."""
        node = dict(_NODE, props=dict(_NODE["props"], mission="two\nlines here"))
        role = build_payload([node])["roles"][0]
        self.assertIn("description: two lines here", render_shell(role))

    def test_isolation_reaches_the_frontmatter_only_when_the_node_asks(self):
        """격리는 역할 노드가 정한다. 빈 값이 `isolation:` 줄을 내면 하네스가 빈 낱말을
        읽고, 그 실패는 껍데기가 아니라 실행 시점에 나온다 — 여기서 막는다."""
        self.assertNotIn("isolation:", render_shell(build_payload([_NODE])["roles"][0]))
        node = dict(_NODE, props=dict(_NODE["props"], isolation="worktree"))
        head = render_shell(build_payload([node])["roles"][0]).splitlines()
        self.assertEqual(head[4], "isolation: worktree")

    def test_every_rung_maps_to_a_model(self):
        for rung in ("strongest", "strong", "fast", "cheapest"):
            self.assertTrue(RUNG_TO_MODEL.get(rung), rung)

    def _render(self, **props):
        node = dict(_NODE, props=dict(_NODE["props"], **props))
        return render_shell(build_payload([node])["roles"][0])

    def test_job_title_and_team_lead_the_description_and_name_the_team(self):
        text = self._render(job_title="Product Owner", team="Program Office",
                            team_role="lead")
        self.assertIn("\ndescription: Product Owner (Program Office). Does one thing",
                      text)
        self.assertIn("\nTeam Program Office, lead.\n", text)

    def test_a_role_without_a_team_keeps_the_mission_as_its_description(self):
        text = self._render()
        self.assertIn("\ndescription: Does one thing and stops.\n", text)
        self.assertNotIn("\nTeam ", text)

    def test_tools_are_declared_in_the_body_and_never_as_an_allowlist(self):
        """허용목록은 부팅 호출까지 자른다. 도구는 본문 선언으로만 싣는다."""
        text = self._render(tools=["vinc: the graph", "Linear: the backlog"])
        self.assertIn("## Tools and MCP servers\n\n- vinc: the graph\n- Linear: the backlog",
                      text)
        self.assertNotIn("\ntools:", text.split("---")[1])

    def test_works_with_comes_from_edges_between_roles_only(self):
        nodes = [{"id": "concept:agent-qa", "label": "Agent role: QA Engineer"}]
        edges = [
            {"source": "concept:agent-example", "target": "concept:agent-qa",
             "type": "PREREQUISITE_OF"},
            {"source": "concept:agent-po", "target": "concept:agent-example",
             "type": "PREREQUISITE_OF"},
            {"source": "concept:agent-qa", "target": "concept:agent-example",
             "type": "RELATED_TO"},
            # 역할이 아닌 끝점·자기 고리·로스터 개념은 협업이 아니다
            {"source": "concept:agent-example", "target": "concept:repo-workflow",
             "type": "RELATED_TO"},
            {"source": "concept:agent-example", "target": "concept:agent-example",
             "type": "PREREQUISITE_OF"},
            {"source": "concept:agent-orchestration", "target": "concept:agent-example",
             "type": "RELATED_TO"},
        ]
        got = works_with("concept:agent-example", nodes, edges)
        self.assertEqual(got, {"hands_to": ["agent-qa (QA Engineer)"],
                               "receives_from": ["agent-po"],
                               "works_beside": ["agent-qa (QA Engineer)"]})
        node = dict(_NODE, works_with=got)
        text = render_shell(build_payload([node])["roles"][0])
        self.assertIn("## Works with\n\n- Hands off to: agent-qa (QA Engineer)\n"
                      "- Receives from: agent-po\n- Works beside: agent-qa (QA Engineer)",
                      text)

    def test_no_edges_means_no_works_with_key_or_section(self):
        role = build_payload([_NODE])["roles"][0]
        self.assertNotIn("works_with", role)
        self.assertNotIn("## Works with", render_shell(role))


class Pull(unittest.TestCase):
    """`--pull` 을 가짜 원격으로 부른다. 네트워크도 토큰도 쓰지 않는다."""

    def test_a_model_free_role_gets_no_shell_and_is_no_ones_peer(self):
        from unittest import mock

        import scripts.agents_sync as sync
        gate = {"id": "concept:agent-gate", "label": "Decision Gate",
                "tags": ["role", "agent", sync.MODEL_FREE_TAG],
                "props": {"tier": "verify", "rung": "none", "mission": "Lists."}}
        nodes = {_NODE["id"]: _NODE, gate["id"]: gate}
        edge = {"source": _NODE["id"], "target": gate["id"], "type": "PREREQUISITE_OF"}

        def call(tool, args):
            if tool == "vinc_role":
                return {"executed": True, "next_offset": None,
                        "roles": [{"id": i} for i in nodes]}
            return {"found": True, "nodes": list(nodes.values()), "edges": [edge]}

        written = []
        with mock.patch("vinc_mcp.remote.call", side_effect=call), \
                mock.patch.object(sync, "write_snapshot", written.append):
            self.assertEqual(sync.cmd_pull(), 0)
        roles = written[0]["roles"]
        self.assertEqual([r["id"] for r in roles], [_NODE["id"]])
        self.assertNotIn("works_with", roles[0])


class RepoIsClean(unittest.TestCase):
    def test_committed_shells_match_the_committed_snapshot(self):
        """CI 와 같은 검사를 로컬에서도 돌린다 — 게이트를 푸시 후에 처음 만나면 고치는
        자리가 이미 멀다."""
        self.assertEqual(main(["--check"]), 0, "역할 껍데기가 스냅샷과 어긋난다(출력 참조)")


class Install(unittest.TestCase):
    """`--install` 은 리포 밖(워크스페이스 루트)에 쓴다. 그래서 여기서는 임시 루트에 대고
    부른다 — 사람의 실제 루트를 테스트가 만지면 안 되고, 결과가 체크아웃 위치에 따라
    갈려도 안 된다. 근거: [[record:role-shells-at-the-workspace-root-load-in-every-session-2026-09-13]]"""

    def test_installs_the_roster_and_hooks_touches_nothing_else_and_repeats_as_a_no_op(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            claude = root / ".claude"
            (claude / "agents").mkdir(parents=True)
            launch = b'{"configurations": []}\r\n'
            (claude / "launch.json").write_bytes(launch)
            (claude / "settings.json").write_text(json.dumps({
                "permissions": {"allow": ["Bash(ls)"]},
                "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "true"}]}],
                          "SubagentStop": [{"hooks": [{"type": "command", "command": "notify"}]}]},
            }, indent=4), encoding="utf-8")
            mine = claude / "agents" / "mine.md"
            mine.write_text("---\nname: mine\n---\n", encoding="utf-8")
            gone = claude / "agents" / "agent-gone.md"
            gone.write_text(f"<!-- {GENERATED_MARK} from agents/roles.json -->\n", encoding="utf-8")

            first = install(root, root / "Vinc", "python")
            self.assertTrue(first)

            snap = read_snapshot()
            for role in snap["roles"]:
                got = (claude / "agents" / f"{role['name']}.md").read_text(encoding="utf-8")
                # 설치본은 추적 사본과 바이트가 같다 — CI 가 대조한 것이 곧 하네스가 읽는 것이다.
                self.assertEqual(got, shell_path(role).read_text(encoding="utf-8"), role["name"])
            self.assertIn("\nisolation: worktree\n",
                          (claude / "agents" / "agent-maker.md").read_text(encoding="utf-8"))
            self.assertTrue(mine.exists(), "사람이 둔 에이전트 파일을 지웠다")
            self.assertFalse(gone.exists(), "그래프에서 사라진 역할의 생성 껍데기가 남았다")
            self.assertEqual((claude / "launch.json").read_bytes(), launch)

            hook = claude / "hooks" / HOOK_SRC.name
            self.assertEqual(hook.read_text(encoding="utf-8"), HOOK_SRC.read_text(encoding="utf-8"))
            settings = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["permissions"], {"allow": ["Bash(ls)"]})
            self.assertIn("PreToolUse", settings["hooks"])
            self.assertEqual(settings["hooks"]["SubagentStop"][0],
                             {"hooks": [{"type": "command", "command": "notify"}]},
                             "남이 건 SubagentStop 훅을 지웠다")
            for event, verb in (("WorktreeCreate", "create"), ("WorktreeRemove", "remove"),
                                ("SubagentStop", "subagent-stop")):
                entry = settings["hooks"][event][-1]
                self.assertEqual(len(settings["hooks"][event]), 2 if event == "SubagentStop" else 1)
                (cmd,) = entry["hooks"]
                self.assertEqual(cmd["command"], "python")
                self.assertEqual(cmd["args"], ["-I", "-X", "utf8", str(hook), verb,
                                               "--repo", str(root / "Vinc"),
                                               "--into", str(root / "_wt")])

            self.assertEqual(install(root, root / "Vinc", "python"), [], "두 번째 설치가 뭔가를 썼다")

    def test_the_main_checkout_is_found_from_any_worktree(self):
        """`ROOT.parent` 는 worktree 에서 루트가 아니다(`_wt/x` 의 부모는 `_wt`). 공용 git
        디렉터리로 찾으면 어느 worktree 에서 물어도 `.git` 디렉터리를 가진 체크아웃이다."""
        self.assertTrue((main_checkout() / ".git").is_dir())


class Reads(unittest.TestCase):
    """`reads` 는 이 설계의 핵심이면서 지금껏 아무도 검사하지 않는 문자열이었다.

    표기가 두 형식으로 섞여 있는 것이 그것을 가렸다. P5 코퍼스는 점 표기 자체가 store
    키이고(`vinc.spec.dataplane`), 2026-08-19 이후 문서는 해시다. 해시로 통일하는 길은
    없다 — 앞쪽에는 해시가 존재하지 않는다. 그래서 두 형식 모두 존재를 검사한다.
    """

    def test_a_missing_document_is_reported_with_its_role(self):
        snap = {"roles": [{"name": "agent-example",
                           "props": {"reads": ["vinc.canon.repo-workflow", "vinc.gone.away"]}}]}
        self.assertEqual(dangling_reads(snap, {"vinc.canon.repo-workflow"}),
                         [("agent-example", "vinc.gone.away")])

    def test_both_id_shapes_pass_when_present(self):
        snap = {"roles": [{"name": "agent-example",
                           "props": {"reads": ["vinc.spec.dataplane", "d8fa9efa40f1a62c7"]}}]}
        self.assertEqual(dangling_reads(snap, {"vinc.spec.dataplane", "d8fa9efa40f1a62c7"}), [])

    def test_a_role_with_no_reads_is_not_a_finding(self):
        self.assertEqual(dangling_reads({"roles": [{"name": "a", "props": {}}]}, set()), [])

    def test_every_committed_read_resolves_against_the_manifest(self):
        """리포 정합. 매니페스트가 낡으면 여기서 먼저 빨개진다 — 그게 맞다.
        `scripts/refresh_graph_refs.py` 를 돌려 같은 커밋에 넣으라는 신호다."""
        docs = _manifest_docs()
        self.assertIsNotNone(docs, "인용 매니페스트가 없다")
        self.assertEqual(dangling_reads(read_snapshot(), docs), [])


class Denylist(unittest.TestCase):
    """금지는 `tools`(허용목록)가 아니라 `disallowedTools`(거부목록)로 낸다.

    허용목록이면 MCP 도구를 서버 이름으로 대야 하는데 그 이름은 기기 설정이고 이 리포에
    `.mcp.json` 이 없다. 그러면 역할이 자기 정의를 읽는 부팅 두 호출이 죽는다. 거부목록은
    상속 풀에서 빼기만 하므로 그 결합이 없다:
    [[record:an-agent-tool-allowlist-cannot-hold-mcp-here-so-the-denylist-does]]
    """

    def _front(self, props):
        node = dict(_NODE, props=dict(_NODE["props"], **props))
        return render_shell(build_payload([node])["roles"][0]).split("---")[1]

    def test_denylist_lands_in_frontmatter_as_one_comma_line(self):
        front = self._front({"disallowed_tools": ["Edit", "Write", "Bash"]})
        self.assertIn("\ndisallowedTools: Edit, Write, Bash\n", front)

    def test_a_role_without_one_gets_no_key_at_all(self):
        """편집하는 역할에 빈 거부목록을 내면 안 된다 — 빈 값은 허용목록으로 오해될
        여지를 남기고, 없는 것과 뜻이 다르다."""
        self.assertNotIn("disallowedTools", self._front({}))
        self.assertNotIn("disallowedTools", self._front({"disallowed_tools": []}))

    def test_frontmatter_key_order_is_stable(self):
        front = self._front({"disallowed_tools": ["Edit"]})
        keys = [ln.split(":")[0] for ln in front.strip().splitlines()]
        self.assertEqual(keys, ["name", "description", "model", "disallowedTools"])

    def test_the_committed_roster_denies_the_edit_tools_of_every_verify_role(self):
        """리포 정합. verify 층은 편집하지 않는 것이 그 층의 정의다."""
        snap = read_snapshot()
        verify = [r for r in snap["roles"] if r["props"].get("tier") == "verify"]
        self.assertTrue(verify, "verify 역할이 스냅샷에 없다")
        for role in verify:
            denied = set(role["props"].get("disallowed_tools") or [])
            self.assertTrue({"Edit", "Write", "NotebookEdit"} <= denied,
                            f"{role['name']} 이 편집 도구를 거부하지 않는다: {sorted(denied)}")
