"""Assemble the unified distribution from the canonical repository packages."""
from __future__ import annotations

import ast
import io
import re
import tempfile
import tokenize
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# 휠 안에서 단독 배포판 vinc-client 의 패키지는 vinc.sdk 로 실린다. 휠에 vinc_client import 가 남으면
# vinc-client 를 따로 깔지 않은 환경에서 ModuleNotFoundError 가 난다.
_CLIENT = "vinc_client"
_SDK = "vinc.sdk"
_DYNAMIC_IMPORTERS = {"import_module", "__import__"}
# `-c` 조각처럼 문자열 안에 든 import 문. 문자열의 처음이나 `;`·줄바꿈 바로 뒤에서 시작하는 것만 센다.
_CODE_IN_STRING = re.compile(r"(?:\A|[;\n])[ \t]*(?:from[ \t]+vinc_client\b|import[ \t]+vinc_client\b)")
_NAME_LITERAL = re.compile(r"""[rRuU]?(["'])vinc_client(?:\.\w+)*\1""")
_LAYOUT_TOKENS = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT}


def _is_client(name: str) -> bool:
    return name == _CLIENT or name.startswith(_CLIENT + ".")


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                ids.add(id(first.value))
    return ids


def _char_col(lines: list[str], row: int, byte_col: int) -> int:
    # ast 의 열은 UTF-8 바이트 수이고 tokenize 의 열은 글자 수다. 한글이 같은 줄에 있어도 자리를 맞춘다.
    return len(lines[row - 1].encode("utf-8")[:byte_col].decode("utf-8"))


def _scan(text: str, label: str):
    """진짜 import 문과 동적 import 를 찾는다. docstring 과 주석은 보지 않는다.

    (고칠 자리, 고칠 수 없는 자리)를 돌려준다. 고칠 자리는 (줄, 시작 열, 끝 열, 새 글자)이고 줄은 1부터, 열은 0부터다.
    """
    try:
        tree = ast.parse(text)
        tokens = [t for t in tokenize.generate_tokens(io.StringIO(text).readline) if t.type not in _LAYOUT_TOKENS]
    except (SyntaxError, tokenize.TokenError) as exc:
        raise RuntimeError(f"{label}: cannot parse a file that names {_CLIENT}: {exc}") from exc
    lines = io.StringIO(text).readlines()
    docstrings = _docstring_ids(tree)
    edits: list[tuple[int, int, int, str]] = []
    problems: list[tuple[int, str]] = []

    def tokens_in(node):
        start = (node.lineno, _char_col(lines, node.lineno, node.col_offset))
        end = (node.end_lineno, _char_col(lines, node.end_lineno, node.end_col_offset))
        return [(i, t) for i, t in enumerate(tokens) if start <= t.start and t.end <= end]

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and _is_client(node.module):
            head = next((t for _, t in tokens_in(node) if t.type == tokenize.NAME and t.string == _CLIENT), None)
            if head is None:
                problems.append((node.lineno, "the module name of this import cannot be located"))
            else:
                edits.append((head.start[0], head.start[1], head.end[1], _SDK))
        elif isinstance(node, ast.Import) and any(_is_client(a.name) for a in node.names):
            if any(_is_client(a.name) and a.asname is None for a in node.names):
                # `import vinc_client.x` 는 이름 vinc_client 를 묶는다. vinc.sdk 로 바꾸면 묶이는 이름이 vinc 가 되어 뒤의 사용이 깨진다.
                problems.append((node.lineno, "an import of vinc_client without `as` binds that name, "
                                              "so it cannot be pointed at vinc.sdk"))
                continue
            for i, t in tokens_in(node):
                if t.type == tokenize.NAME and t.string == _CLIENT and tokens[i - 1].string in {"import", ","}:
                    edits.append((t.start[0], t.start[1], t.end[1], _SDK))
        elif isinstance(node, ast.Call):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called not in _DYNAMIC_IMPORTERS:
                continue
            for arg in node.args[:1] + [k.value for k in node.keywords if k.arg == "name"]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and _is_client(arg.value):
                    first = _char_col(lines, arg.lineno, arg.col_offset)
                    last = _char_col(lines, arg.end_lineno, arg.end_col_offset)
                    literal = lines[arg.lineno - 1][first:last] if arg.lineno == arg.end_lineno else ""
                    if _NAME_LITERAL.fullmatch(literal) is None:
                        problems.append((arg.lineno, f"{called}() names vinc_client in a form that cannot be rewritten"))
                        continue
                    at = first + literal.index(_CLIENT)
                    edits.append((arg.lineno, at, at + len(_CLIENT), _SDK))
                elif isinstance(arg, ast.JoinedStr) and any(
                        isinstance(v, ast.Constant) and _CLIENT in str(v.value) for v in arg.values):
                    problems.append((arg.lineno, f"{called}() builds a vinc_client name at run time"))
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
              and _CODE_IN_STRING.search(node.value)):
            problems.append((node.lineno, "a string holds code that imports vinc_client; "
                                          "it cannot be rewritten, so build it from vinc.sdk"))
    return edits, problems


def leftover_client_imports(text: str, label: str = "<source>") -> list[tuple[int, str]]:
    """휠에 남으면 안 되는 vinc_client import 의 (줄, 이유). 고칠 수 있는 것도 아직 있으면 센다."""
    if _CLIENT not in text:
        return []
    edits, problems = _scan(text, label)
    return sorted([(row, f"imports {_CLIENT}") for row, *_ in edits] + problems)


def rewrite_client_imports(text: str, label: str) -> str:
    """vinc_client 를 가리키는 import 를 vinc.sdk 로 바꾼 소스. 못 바꾸는 것이 하나라도 있으면 빌드를 멈춘다."""
    if _CLIENT not in text:
        return text
    edits, problems = _scan(text, label)
    if problems:
        raise RuntimeError("; ".join(f"{label}:{row}: {why}" for row, why in sorted(problems)))
    lines = io.StringIO(text).readlines()
    for row, start, end, new in sorted(edits, reverse=True):
        lines[row - 1] = lines[row - 1][:start] + new + lines[row - 1][end:]
    result = "".join(lines)
    # 바꾼 결과를 다시 훑는다. 하나라도 남았으면 휠을 만들지 않는다.
    left = leftover_client_imports(result, label)
    if left:
        raise RuntimeError("; ".join(f"{label}:{row}: {why} after the rewrite" for row, why in left))
    return result


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root)
        # sdist 에는 이미 조립된 소스가 있다. 저장소 밖에서 다시 빌드할 수 있어야 한다.
        if (root / "src/vinc/sdk/client.py").is_file():
            # 조립된 소스가 vinc_client import 를 품고 있으면 휠에 싣기 전에 멈춘다.
            for path in sorted((root / "src/vinc").rglob("*.py")):
                name = path.relative_to(root).as_posix()
                left = leftover_client_imports(path.read_text(encoding="utf-8"), name)
                if left:
                    raise RuntimeError("; ".join(f"{name}:{row}: {why}" for row, why in left))
            return
        repo = root.parents[2]
        sources = {
            "sdk": repo / "integrations/client/python/src/vinc_client",
            "integrations/langgraph": repo / "integrations/langgraph/python/src/vinc_langgraph",
            "integrations/agent_framework": repo / "integrations/agent-framework/python/src/vinc_agent_framework",
            **{f"_runtime/{name}": repo / name
               for name in ("api", "store", "engines", "vinc_mcp", "vinc_rag")},
        }
        self._stage = tempfile.TemporaryDirectory(prefix="vincs-build-")
        stage = Path(self._stage.name)
        prefix = "src/vinc" if self.target_name == "sdist" else "vinc"
        for destination, source in sources.items():
            if not source.is_dir():
                raise RuntimeError(f"canonical package source missing: {source}")
            for path in sorted(source.rglob("*")):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                if path.suffix not in {".py", ".json"} and path.name != "py.typed":
                    continue
                relative = Path(destination) / path.relative_to(source)
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if path.suffix == ".py":
                    # 어느 원본 패키지의 파일이든 vinc_client import 는 같은 SDK 소스(vinc.sdk)에 연결한다.
                    # 원본은 그대로 두고 스테이지의 사본만 바꾼다.
                    text = path.read_bytes().decode("utf-8")
                    target.write_bytes(rewrite_client_imports(text, relative.as_posix()).encode("utf-8"))
                else:
                    target.write_bytes(path.read_bytes())
                build_data["force_include"][str(target)] = f"{prefix}/{relative.as_posix()}"

    def finalize(self, version, build_data, artifact_path):
        if hasattr(self, "_stage"):
            self._stage.cleanup()
