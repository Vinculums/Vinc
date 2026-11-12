# -*- coding: utf-8 -*-
"""데스크톱 바이너리의 `CLI_VERBS` 가 파이썬 CLI 파서의 최상위 동사와 어긋나지 않는가.

앱 바이너리(`app/src-tauri/src/main.rs`)는 첫 인자가 `CLI_VERBS` 에 있을 때만 번들 CLI 로
넘기고, 없으면 창을 연다. 그래서 파이썬에 동사가 늘었는데 이 배열이 그대로면, 앱 바이너리로
부른 그 동사만 조용히 창을 띄운다.

이 시험은 파이썬 쪽을 소스 문자열이 아니라 **실제로 조립한 파서**에서 읽는다. `_main` 이
파서를 다 만든 뒤 `parse_args` 를 부르는 순간 멈추게 하므로, 동사를 실행하지 않는다.

`UNDECIDED_DESKTOP_OR_HUB_ONLY` 는 파이썬에는 있고 Rust 목록에는 없는 동사 중, 데스크톱에서도
CLI 로 보낼지(배열에 넣는다) 허브·운영 전용으로 둘지(여기 남긴다) 아직 정하지 않은 것이다.
정하는 것은 사람이고 이 시험은 정하지 않는다. 새 동사는 둘 중 한 곳에 적어야 통과한다.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAIN_RS = os.path.join(ROOT, "app", "src-tauri", "src", "main.rs")

# 데스크톱 CLI 로 보낼지, 허브 전용으로 둘지 아직 정하지 않은 동사. 정해지면 여기서 빼고
# Rust 배열에 넣거나, 허브 전용이라는 이름의 집합을 새로 두어 옮긴다(첫 검사가 그 집합도 보게 한다).
UNDECIDED_DESKTOP_OR_HUB_ONLY = frozenset({
    "edges", "eval", "events", "gate", "migrate-body-mentions", "migrate-lex", "operation",
    "role", "run", "selfhost", "shim", "storage", "store-probe", "store-rebuild",
})


class _Built(Exception):
    def __init__(self, parser: argparse.ArgumentParser) -> None:
        super().__init__("parser built")
        self.parser = parser


def _stop_before_parse(self, *args, **kwargs):
    # 파서가 다 조립된 지점: 인자를 해석하거나 동사를 실행하기 전에 빠져나온다.
    raise _Built(self)


def python_parser() -> argparse.ArgumentParser:
    from api import cli
    # `_main` 은 표준 스트림을 UTF-8 로 재구성한다. 시험 프로세스의 스트림은 건드리지 않는다.
    with mock.patch.object(cli._Parser, "parse_args", _stop_before_parse), \
            mock.patch.object(sys, "stdout", io.StringIO()), \
            mock.patch.object(sys, "stderr", io.StringIO()), \
            mock.patch.object(sys, "stdin", io.StringIO()):
        try:
            cli._main([])
        except _Built as built:
            return built.parser
    raise AssertionError("api.cli._main returned without reaching parse_args")


def python_verbs(parser: argparse.ArgumentParser) -> set[str]:
    actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if len(actions) != 1:
        raise AssertionError(f"expected one top-level subparsers action, found {len(actions)}")
    return set(actions[0].choices)


def rust_cli_verbs() -> list[str]:
    with open(MAIN_RS, encoding="utf-8") as fh:
        src = fh.read()
    found = re.search(r"const CLI_VERBS: \[&str; \d+\] = \[(.*?)\];", src, re.S)
    if found is None:
        raise AssertionError(f"CLI_VERBS array not found in {MAIN_RS}")
    return re.findall(r'"([^"]*)"', found.group(1))


class CliVerbsMatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parser = python_parser()
        cls.python = python_verbs(cls.parser)
        entries = rust_cli_verbs()
        # `-` 로 시작하는 항목은 동사가 아니라 루트 옵션(`--help`·`--version` 등)이다.
        cls.rust_flags = {e for e in entries if e.startswith("-")}
        cls.rust_verbs = {e for e in entries if not e.startswith("-")}

    # 실패 메시지는 어긋난 항목을 한 줄에 하나씩 모두 적는다(첫 항목에서 멈추지 않는다).
    def _no_entries(self, entries: list[str]) -> None:
        if entries:
            self.fail("\n".join(entries))

    def test_every_python_verb_is_listed_or_undecided(self) -> None:
        self._no_entries([
            f"python CLI verb {verb!r} is neither in CLI_VERBS (app/src-tauri/src/main.rs) nor in "
            "UNDECIDED_DESKTOP_OR_HUB_ONLY; decide whether the desktop binary forwards it"
            for verb in sorted(self.python - self.rust_verbs - UNDECIDED_DESKTOP_OR_HUB_ONLY)])

    def test_rust_names_only_python_verbs(self) -> None:
        self._no_entries([
            f"CLI_VERBS names {verb!r}, which the python CLI parser does not have"
            for verb in sorted(self.rust_verbs - self.python)])

    def test_rust_flags_are_root_options(self) -> None:
        options = set(self.parser._option_string_actions)
        self._no_entries([
            f"CLI_VERBS names {flag!r}, which is not an option of the python CLI root parser"
            for flag in sorted(self.rust_flags - options)])

    def test_no_verb_is_both_listed_and_undecided(self) -> None:
        self._no_entries([
            f"{verb!r} is in CLI_VERBS and in UNDECIDED_DESKTOP_OR_HUB_ONLY; remove it from the undecided set"
            for verb in sorted(self.rust_verbs & UNDECIDED_DESKTOP_OR_HUB_ONLY)])

    def test_undecided_verbs_still_exist(self) -> None:
        self._no_entries([
            f"undecided verb {verb!r} is no longer a python CLI verb; remove it from the set"
            for verb in sorted(UNDECIDED_DESKTOP_OR_HUB_ONLY - self.python)])

if __name__ == "__main__":
    unittest.main()
