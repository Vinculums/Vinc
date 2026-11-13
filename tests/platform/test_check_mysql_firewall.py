# -*- coding: utf-8 -*-
"""vinc-mysql01 영구 방화벽 규칙의 원천 목록 검사 — scripts/check_mysql_firewall.py.

이 검사가 조용히 통과하면 낡은 목록이 apply 되어 vinc-account 의 새 DB 연결이 막힌다. 그래서
"실물에만 있는 IP 가 실제로 잡히는가"와 "조회 실패를 같음으로 읽지 않는가"를 못 박는다.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SRC = _ROOT / "scripts" / "check_mysql_firewall.py"


def _load():
    spec = importlib.util.spec_from_file_location("_vinc_check_mysql_firewall", _SRC)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class CheckMysqlFirewallTest(unittest.TestCase):
    def setUp(self):
        self.m = _load()
        self.declared = ["20.196.231.61", "4.230.74.65", "20.249.52.134"]

    def _run(self, live, declared=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.m.main(json.dumps(live), json.dumps(self.declared if declared is None else declared))
        return rc, out.getvalue() + err.getvalue()

    def test_same_set_in_another_order_passes(self):
        rc, _ = self._run(list(reversed(self.declared)))
        self.assertEqual(rc, 0)

    def test_an_ip_only_on_the_platform_fails_and_is_named(self):
        rc, text = self._run(self.declared + ["20.214.92.80"])
        self.assertEqual(rc, 1)
        self.assertIn("+ 20.214.92.80", text)

    def test_an_ip_only_in_the_list_fails_and_is_named(self):
        rc, text = self._run(self.declared[:2])
        self.assertEqual(rc, 1)
        self.assertIn("- 20.249.52.134", text)

    def test_an_empty_live_list_is_not_read_as_a_match(self):
        rc, _ = self._run([], declared=[])
        self.assertEqual(rc, 2)

    def test_malformed_input_is_a_usage_error(self):
        for live in (["not-an-ip"], ["2001:db8::1"], {"ip": "1.2.3.4"}, ["1.2.3.4", "1.2.3.4"]):
            rc, _ = self._run(live)
            self.assertEqual(rc, 2, live)

    def test_a_list_that_leaves_no_room_for_runner_rules_fails(self):
        many = [f"10.0.{i // 256}.{i % 256}" for i in range(self.m.RULE_CAP)]
        rc, _ = self._run(many, declared=many)
        self.assertEqual(rc, 1)

    def test_the_committed_list_is_well_formed_and_fits_under_the_cap(self):
        ips = self.m.parse(self.m.DECLARED.read_text(encoding="utf-8"), "declared")
        self.assertGreater(len(ips), 0)
        self.assertLessEqual(len(ips), self.m.RULE_CAP - self.m.RUNNER_HEADROOM)

    def test_the_terraform_rules_read_the_same_list(self):
        tf = (_ROOT / "infra" / "mysql-firewall.tf").read_text(encoding="utf-8")
        self.assertIn(self.m.DECLARED.name, tf)


if __name__ == "__main__":
    unittest.main()
