# -*- coding: utf-8 -*-
"""소유권 게이트 — 매칭 규칙과 리포 정합.

왜 이 파일이 있나: CODEOWNERS 는 GitHub 가 읽는 것이라 로컬에서 "이 줄이 이 파일을
잡는가"를 볼 길이 없다. 게이트가 GitHub 와 **같은 규칙**으로 읽어야 "주인 없는 파일 0"
이라는 초록이 참이다. 그래서 규칙을 먼저 잠그고, 그 다음 커밋된 리포 전체를 그 규칙으로
센다. 통과하는 것만 재면 게이트를 지워도 초록이라 떨어져야 하는 것도 함께 잠근다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts import gate_ownership as G                     # noqa: E402
from scripts.agents_sync import (CODEOWNERS, TEAMS,          # noqa: E402
                                 read_snapshot, read_teams,
                                 render_codeowners)

RULES = G.parse_codeowners("""
# comment
/scripts/                 @o/core
/scripts/gate_*.py        @o/platform
/scripts/qa/              @o/platform
*.md                      @o/docs
/api/auth.py              @o/core @o/platform
""")


class Matching(unittest.TestCase):
    def test_last_line_wins(self):
        self.assertEqual(G.owners_for("scripts/encoder.py", RULES), ("@o/core",))
        self.assertEqual(G.owners_for("scripts/gate_pr_title.py", RULES), ("@o/platform",))

    def test_a_directory_line_covers_every_depth(self):
        self.assertEqual(G.owners_for("scripts/qa/deep/one.py", RULES), ("@o/platform",))

    def test_star_does_not_cross_a_slash(self):
        """`/scripts/gate_*.py` 는 `scripts/qa/gate_x.py` 를 잡지 않는다."""
        self.assertEqual(G.owners_for("scripts/qa/gate_x.py", RULES), ("@o/platform",))
        self.assertEqual(G.owners_for("scripts/sub/gate_x.py", RULES), ("@o/core",))

    def test_a_bare_name_matches_at_any_depth(self):
        self.assertEqual(G.owners_for("README.md", RULES), ("@o/docs",))
        self.assertEqual(G.owners_for("design-hub/notes/x.md", RULES), ("@o/docs",))

    def test_shared_line_yields_both_owners(self):
        self.assertEqual(G.owners_for("api/auth.py", RULES), ("@o/core", "@o/platform"))

    def test_unmatched_is_empty_and_reported(self):
        self.assertEqual(G.owners_for("app/src/main.ts", RULES), ())
        self.assertEqual(G.unowned(["app/src/main.ts", "scripts/x.sh"], RULES),
                         ["app/src/main.ts"])


_TEAMS = {
    "teams": {
        "core": {"github": "@o/core", "roles": ["agent-dev-core"], "paths": ["api/"]},
        "platform": {"github": "@o/platform", "roles": ["agent-infra"], "paths": ["infra/"],
                     "planned": ["future/"]},
    },
    "shared": {"paths": {"api/auth.py": ["core", "platform"]}},
    "read_only_roles": ["agent-orchestrator"],
}
_ROLES = {"roles": [
    {"name": "agent-dev-core", "props": {"paths": ["api/", "store/"], "repos": ["Vinc"]}},
    {"name": "agent-infra", "props": {"paths": ["infra/", "ghost/"], "repos": ["Vinc"]}},
    {"name": "agent-orchestrator", "props": {"paths": ["read only"], "repos": ["Vinc"]}},
]}


class Roster(unittest.TestCase):
    def test_render_is_deterministic_and_specific_lines_come_last(self):
        a = render_codeowners(_TEAMS)
        self.assertEqual(a, render_codeowners(_TEAMS))
        body = [ln for ln in a.splitlines() if ln and not ln.startswith("#")]
        self.assertEqual(body[-1].split(), ["/api/auth.py", "@o/core", "@o/platform"])

    def test_deeper_paths_sort_after_their_prefix_regardless_of_team_order(self):
        teams = {"teams": {
            "platform": {"github": "@o/platform", "paths": ["tools/gate_x.sh"]},
            "core": {"github": "@o/core", "paths": ["tools/"]},
        }}
        body = [ln.split()[0] for ln in render_codeowners(teams).splitlines()
                if ln and not ln.startswith("#")]
        self.assertEqual(body, ["/tools/", "/tools/gate_x.sh"])

    def test_planned_paths_are_not_ghosts_but_missing_paths_are(self):
        exists = lambda p: p in {"api/", "api/auth.py"}  # noqa: E731
        self.assertEqual(G.ghost_team_paths(_TEAMS, exists), [("platform", "infra/")])

    def test_mismatch_names_the_lost_role_on_both_sides(self):
        teams = {"teams": {"core": {"github": "@o/core", "roles": ["agent-nobody"], "paths": []}},
                 "read_only_roles": []}
        got = G.roster_mismatch(teams, _ROLES)
        self.assertTrue(any("agent-nobody" in m for m in got))
        self.assertTrue(any("agent-dev-core" in m and "어느 팀에도" in m for m in got))

    def test_read_only_and_a_team_at_once_is_a_mismatch(self):
        teams = {"teams": {"core": {"github": "@o/core", "roles": ["agent-orchestrator"],
                                    "paths": []}},
                 "read_only_roles": ["agent-orchestrator"]}
        got = G.roster_mismatch(teams, {"roles": [{"name": "agent-orchestrator"}]})
        self.assertEqual(len(got), 1)

    def test_drift_is_a_warning_that_names_the_ghost_and_the_other_owner(self):
        rules = G.parse_codeowners(render_codeowners(_TEAMS))
        exists = lambda p: p in {"api/", "infra/", "store/"}  # noqa: E731
        got = G.roster_drift(_TEAMS, _ROLES, rules, exists)
        self.assertTrue(any("ghost/" in m for m in got))
        # store/ 는 존재하지만 어느 팀도 소유하지 않는다 — 드리프트가 아니라 UNOWNED 의 몫이다.
        self.assertFalse(any("store/" in m for m in got))

    def test_prose_in_paths_is_ignored(self):
        self.assertFalse(G._looks_like_path("read only across all three"))
        self.assertTrue(G._looks_like_path("app/src"))
        self.assertEqual(G._strip_note("app/src (consuming copy)"), "app/src")

    def test_teams_touched_maps_handles_back_to_team_names(self):
        rules = G.parse_codeowners(render_codeowners(_TEAMS))
        self.assertEqual(G.teams_touched(["api/x.py", "api/auth.py"], rules, _TEAMS),
                         ["core", "platform"])


class Layers(unittest.TestCase):
    def test_a_lazy_import_inside_a_function_is_still_a_violation(self):
        src = {"store/x.py": "def f():\n    from api.docstore import g\n",
               "vinc_mcp/y.py": "import api.server\n",
               "api/z.py": "from store import migrate\n",
               "store/ok.py": "from . import pkns\nimport os\n"}
        got = G.layer_violations(list(src), src.__getitem__)
        self.assertEqual(sorted(got), [("store/x.py", "api"), ("vinc_mcp/y.py", "api")])

    def test_a_name_that_merely_starts_with_api_is_not_a_hit(self):
        src = {"store/x.py": "import apixyz\nfrom apiary import bee\n"}
        self.assertEqual(G.layer_violations(list(src), src.__getitem__), [])


class RepoIsClean(unittest.TestCase):
    """커밋된 리포에 대한 실측. 여기서 떨어지면 게이트도 CI 에서 떨어진다."""

    def test_lower_layers_do_not_import_upper_layers(self):
        read = lambda f: open(os.path.join(ROOT, f), encoding="utf-8", errors="replace").read()  # noqa: E731
        self.assertEqual(G.layer_violations(G.tracked_files(), read), [])

    def test_committed_codeowners_matches_teams_json(self):
        self.assertTrue(CODEOWNERS.exists(), "scripts/agents_sync.py --gen")
        self.assertEqual(CODEOWNERS.read_text(encoding="utf-8"),
                         render_codeowners(read_teams()))

    def test_every_tracked_file_has_an_owner(self):
        rules = G.parse_codeowners(CODEOWNERS.read_text(encoding="utf-8"))
        orphans = G.unowned(G.tracked_files(), rules)
        self.assertEqual(orphans, [], f"주인 없는 파일 {len(orphans)}건: {orphans[:10]}")

    def test_no_ghost_paths_and_no_roster_mismatch(self):
        teams, roles = read_teams(), read_snapshot()
        exists = lambda p: os.path.exists(os.path.join(ROOT, p))  # noqa: E731
        self.assertEqual(G.ghost_team_paths(teams, exists), [])
        self.assertEqual(G.roster_mismatch(teams, roles), [])

    def test_the_gate_script_passes_on_the_repo(self):
        r = subprocess.run([sys.executable, "-X", "utf8", "scripts/gate_ownership.py"],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("소유권 이상 없음", r.stdout)

    def test_teams_json_is_tracked(self):
        self.assertTrue(TEAMS.exists())


if __name__ == "__main__":
    unittest.main()
