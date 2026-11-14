# -*- coding: utf-8 -*-
"""데스크톱 셸의 비밀 참조 규칙(`storage_secret.rs`)이 저장 위치 파서(`store/location.py`)와 같은 답을 내는가.

셸은 설정 파일에서 키링 항목 이름을 골라 사이드카 환경에 싣고, 사이드카는 같은 파일을 읽어 같은 이름을
검사한다. 두 쪽이 갈리면 파이썬이 받는 이름을 셸이 버려 비밀번호가 사이드카에 가지 않거나(스토어 없이 뜬다),
그 반대가 된다. 어느 쪽도 오류를 내지 않고 지나가므로 여기서 같은 표본을 양쪽에 먹여 본다.

이 시험은 러스트를 실행하지 못한다. `secret_ref` 의 판정식을 소스에서 뽑아(식의 모양이 바뀌면 이 시험이 떨어진다)
같은 규칙을 파이썬으로 세우고, 파이썬 쪽은 실제 `read_location` 으로 돌린다. 키 이름과 허용 문자, 길이 한도,
환경 변수 이름은 러스트 소스에서 읽은 값을 쓴다.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from dataclasses import dataclass

from _src import ROOT

sys.path.insert(0, str(ROOT))
from store import location  # noqa: E402

RUST = ROOT / "app" / "src-tauri" / "src" / "storage_secret.rs"

#: 두 쪽이 모두 받아야 하는 이름과 모두 거절해야 하는 이름.
VALID = [
    "a",
    "0",
    "vinc-database:graph.example.com/vinc",
    "A.b_c:d/e@f-g",
    "env:VINC_DATABASE_SECRET",
    "a" * 200,
]
INVALID = [
    "",
    "a" * 201,
    "has space",
    "tab\there",
    "inner\nnewline",
    "한글",
    "é",
    "ａ",
    "a;b",
    "a\\b",
    "a#b",
    "a?b",
    "a+b",
    "a=b",
    "a%b",
    "a!b",
    "a'b",
    'a"b',
]
#: 이름이 문자열이 아닌 설정. 두 쪽 모두 이름 없음이다.
NOT_TEXT = [None, 123, ["x"], {"x": 1}, True]

#: 지금 두 쪽이 갈리는 표본: (설정에 적힌 이름) → (파이썬이 돌려주는 이름, 러스트가 돌려주는 이름).
#: 파이썬은 `_text` 가 앞뒤 공백과 줄바꿈을 먼저 떼고 검사하지만 러스트는 떼지 않는다.
#: 한쪽을 고쳐 둘이 같아지면 `test_known_difference_is_still_a_difference` 가 떨어진다: 그때 표본을
#: 위 목록으로 옮기고 이 표에서 지운다.
KNOWN_DIFFERENT = {
    " padded ": ("padded", None),
    "padded\n": ("padded", None),
    "\tpadded": ("padded", None),
}


@dataclass(frozen=True)
class RustRule:
    mode_key: str
    mode_value: str
    database_key: str
    ref_key: str
    max_len: int
    extra_chars: str


def rust_rule() -> RustRule:
    src = RUST.read_text(encoding="utf-8")
    found = re.search(r"pub fn secret_ref\(config: &str\) -> Option<String> \{\n(.*?)\n\}\n", src, re.S)
    if found is None:
        raise AssertionError(f"pub fn secret_ref not found in {RUST}")
    body = found.group(1)
    mode = re.search(r'serde_json::from_str\(config\)\.ok\(\)\?;\s*'
                     r'if v\.get\("(\w+)"\)\?\.as_str\(\)\? != "(\w+)" \{\s*return None;\s*\}', body)
    ref = re.search(r'let name = v\.get\("(\w+)"\)\?\.get\("(\w+)"\)\?\.as_str\(\)\?;', body)
    rule = re.search(r"let ok = !name\.is_empty\(\)\s*&& name\.len\(\) <= (\d+)\s*"
                     r'&& name\s*\.chars\(\)\s*\.all\(\|c\| c\.is_ascii_alphanumeric\(\) \|\| "([^"]*)"\.contains\(c\)\);',
                     body)
    tail = re.search(r"if ok \{\s*Some\(name\.to_string\(\)\)\s*\} else \{\s*None\s*\}", body)
    missing = [what for what, hit in (("mode check", mode), ("name lookup", ref),
                                      ("name rule", rule), ("result", tail)) if hit is None]
    if missing:
        raise AssertionError(
            f"secret_ref in {RUST.name} no longer has the shape this test models ({', '.join(missing)}); "
            "update rust_rule() and rust_secret_ref() together with the Rust function")
    return RustRule(mode_key=mode.group(1), mode_value=mode.group(2), database_key=ref.group(1),
                    ref_key=ref.group(2), max_len=int(rule.group(1)), extra_chars=rule.group(2))


def rust_secret_ref(text: str, rule: RustRule) -> str | None:
    """`storage_secret::secret_ref` 와 같은 순서로 판정한다."""
    try:
        value = json.loads(text)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    mode = value.get(rule.mode_key)
    if not isinstance(mode, str) or mode != rule.mode_value:
        return None
    database = value.get(rule.database_key)
    if not isinstance(database, dict):
        return None
    name = database.get(rule.ref_key)
    if not isinstance(name, str):
        return None
    ok = (name != "" and len(name.encode("utf-8")) <= rule.max_len
          and all((c.isascii() and c.isalnum()) or c in rule.extra_chars for c in name))
    return name if ok else None


def config_with(rule: RustRule, ref, mode: str | None = None) -> dict:
    """파이썬 파서가 받는 데이터베이스 위치 설정. 키 이름은 러스트 소스에서 읽은 것이다."""
    return {rule.mode_key: mode or rule.mode_value,
            rule.database_key: {"engine": "neo4j", "uri": "neo4j+s://graph.example.com:7687",
                                "database": "vinc", "user": "vinc", rule.ref_key: ref}}


def python_secret_ref(conf: dict) -> str | None:
    """`read_location` 이 설정에서 읽는 비밀 참조. 데이터베이스 위치가 아니거나 틀리면 None."""
    try:
        loc = location.read_location(conf)
    except location.LocationError:
        return None
    return loc.secret_ref if isinstance(loc, location.DatabaseLocation) else None


class SecretRefMatchesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rule = rust_rule()

    def both(self, ref, mode: str | None = None) -> tuple[str | None, str | None]:
        conf = config_with(self.rule, ref, mode)
        return python_secret_ref(conf), rust_secret_ref(json.dumps(conf), self.rule)

    def test_the_name_the_shell_hands_over_is_the_one_the_parser_accepts(self) -> None:
        for ref in VALID:
            with self.subTest(ref=ref[:40]):
                self.assertEqual(self.both(ref), (ref, ref))

    def test_both_refuse_the_same_names(self) -> None:
        for ref in INVALID:
            with self.subTest(ref=ref[:40]):
                self.assertEqual(self.both(ref), (None, None))

    def test_both_refuse_a_name_that_is_not_text(self) -> None:
        for ref in NOT_TEXT:
            with self.subTest(ref=ref):
                self.assertEqual(self.both(ref), (None, None))

    def test_only_the_database_mode_has_a_name(self) -> None:
        for mode in ("local", "account", "none", "Database"):
            with self.subTest(mode=mode):
                self.assertEqual(self.both("vinc-database:x", mode=mode), (None, None))
        no_block = {self.rule.mode_key: self.rule.mode_value}
        self.assertEqual((python_secret_ref(no_block), rust_secret_ref(json.dumps(no_block), self.rule)),
                         (None, None))

    def test_rust_reads_no_name_from_text_that_is_not_a_database_config(self) -> None:
        for text in ("not json", "", "[]", "null", '"database"', "{}"):
            with self.subTest(text=text):
                self.assertIsNone(rust_secret_ref(text, self.rule))

    def test_known_difference_is_still_a_difference(self) -> None:
        for ref, expected in KNOWN_DIFFERENT.items():
            with self.subTest(ref=ref):
                self.assertEqual(self.both(ref), expected,
                                 "the two sides now answer alike: move this sample to VALID and drop it here")

    def test_the_environment_variable_has_one_name(self) -> None:
        src = RUST.read_text(encoding="utf-8")
        found = re.search(r'pub const SECRET_ENV: &str = "(\w+)";', src)
        self.assertIsNotNone(found, f"SECRET_ENV not found in {RUST.name}")
        self.assertEqual(found.group(1), location.SECRET_ENV)


if __name__ == "__main__":
    unittest.main()
