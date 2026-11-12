# -*- coding: utf-8 -*-
"""데스크톱 바이너리의 `CLI_VERBS` 가 파이썬 CLI 파서의 최상위 동사와 어긋나지 않는가.

앱 바이너리(`app/src-tauri/src/main.rs`)는 첫 인자가 `CLI_VERBS` 에 있을 때만 번들 CLI 로
넘기고, 없으면 창을 연다. 그래서 파이썬에 동사가 늘었는데 이 배열이 그대로면, 앱 바이너리로
부른 그 동사만 조용히 창을 띄운다.

이 시험은 파이썬 쪽을 소스 문자열이 아니라 **실제로 조립한 파서**에서 읽는다. `_main` 이
파서를 다 만든 뒤 `parse_args` 를 부르는 순간 멈추게 하므로, 동사를 실행하지 않는다.

`HUB_ONLY_BY_DECISION` 은 파이썬에는 있고 Rust 목록에는 일부러 없는 동사다. 허브나 오프라인
스토어 사본에서 운영자가 부르는 유지보수 동사라 앱 바이너리로는 넘기지 않는다. 동사마다 이유를
한 줄 적는다. 어느 쪽에 둘지는 사람이 정하고 이 시험은 정하지 않는다. 새 동사는 Rust 배열이나 이
집합 중 한 곳에 적어야 통과한다.
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

# 앱 바이너리가 번들 CLI 로 넘기지 않는 동사와 그 이유. 앱 바이너리로 부르면 창이 뜬다.
HUB_ONLY_BY_DECISION: dict[str, str] = {
    "migrate-lex": "rebuilds the text index on an offline copy of a store before a hub switches to it",
    "migrate-body-mentions": "opt-in schema change an operator runs against a store file",
    "store-probe": "child the engine starts on itself to open and close a store before WAL replay",
    "store-rebuild": "rebuilds a read-only store into an empty directory during a hub migration",
    "selfhost": "passwords, people and seats of a self-hosted hub, run inside the hub container",
}


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

    def test_every_python_verb_is_listed_or_hub_only(self) -> None:
        self._no_entries([
            f"python CLI verb {verb!r} is neither in CLI_VERBS (app/src-tauri/src/main.rs) nor in "
            "HUB_ONLY_BY_DECISION; decide whether the desktop binary forwards it"
            for verb in sorted(self.python - self.rust_verbs - set(HUB_ONLY_BY_DECISION))])

    def test_rust_names_only_python_verbs(self) -> None:
        self._no_entries([
            f"CLI_VERBS names {verb!r}, which the python CLI parser does not have"
            for verb in sorted(self.rust_verbs - self.python)])

    def test_rust_flags_are_root_options(self) -> None:
        options = set(self.parser._option_string_actions)
        self._no_entries([
            f"CLI_VERBS names {flag!r}, which is not an option of the python CLI root parser"
            for flag in sorted(self.rust_flags - options)])

    def test_no_verb_is_both_listed_and_hub_only(self) -> None:
        self._no_entries([
            f"{verb!r} is in CLI_VERBS and in HUB_ONLY_BY_DECISION; keep it in one of them"
            for verb in sorted(self.rust_verbs & set(HUB_ONLY_BY_DECISION))])

    def test_hub_only_verbs_still_exist(self) -> None:
        self._no_entries([
            f"hub-only verb {verb!r} is no longer a python CLI verb; remove it from the set"
            for verb in sorted(set(HUB_ONLY_BY_DECISION) - self.python)])

    def test_every_hub_only_verb_has_a_reason(self) -> None:
        self._no_entries([
            f"hub-only verb {verb!r} has no reason; write one line saying why it stays off the desktop"
            for verb, reason in sorted(HUB_ONLY_BY_DECISION.items()) if not reason.strip()])


if __name__ == "__main__":
    unittest.main()
