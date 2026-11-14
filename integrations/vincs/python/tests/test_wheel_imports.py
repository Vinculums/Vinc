"""설치된 휠 안의 모든 .py 가 단독 배포판 vinc_client 를 import 하지 않는다.

휠 안에서 그 패키지는 vinc.sdk 다. vinc-client 를 따로 깔지 않은 환경(이 시험이 도는 곳)에서 vinc_client import 가
실행되면 ModuleNotFoundError 다. CI 의 vincs 잡이 sdist 에서 휠을 짓고 빈 환경에 깐 뒤 이 파일을 설치본에 대고 돈다.
"""
import ast
import re
from pathlib import Path

import pytest

CLIENT = "vinc_client"
# 문자열 안의 `-c` 조각처럼 처음이나 `;`·줄바꿈 뒤에서 시작하는 import 문.
CODE_IN_STRING = re.compile(r"(?:\A|[;\n])[ \t]*(?:from[ \t]+vinc_client\b|import[ \t]+vinc_client\b)")
DYNAMIC = {"import_module", "__import__"}


def _named(value):
    return value == CLIENT or value.startswith(CLIENT + ".")


def client_imports(source):
    """(줄, 모양) 목록. 주석과 docstring 은 import 가 아니므로 세지 않는다."""
    tree = ast.parse(source)
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.body and isinstance(n.body[0], ast.Expr)
                  and isinstance(n.body[0].value, ast.Constant) and isinstance(n.body[0].value.value, str)}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and _named(node.module):
            found.append((node.lineno, "from-import"))
        elif isinstance(node, ast.Import) and any(_named(alias.name) for alias in node.names):
            found.append((node.lineno, "import"))
        elif isinstance(node, ast.Call):
            callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            args = node.args[:1] + [k.value for k in node.keywords if k.arg == "name"]
            if callee in DYNAMIC and any(
                    isinstance(a, ast.Constant) and isinstance(a.value, str) and _named(a.value)
                    or isinstance(a, ast.JoinedStr) and CLIENT in ast.unparse(a) for a in args):
                found.append((node.lineno, "dynamic import"))
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in docstrings and CODE_IN_STRING.search(node.value)):
            found.append((node.lineno, "import in a string"))
    return sorted(found)


@pytest.mark.parametrize("source, shapes", [
    ("from vinc_client import A\n", ["from-import"]),
    ("def f():\n    try:\n        from vinc_client.run import x\n    except ImportError:\n        pass\n",
     ["from-import"]),
    ("import vinc_client\nimport os, vinc_client.run as run\n", ["import", "import"]),
    ("import importlib\nimportlib.import_module('vinc_client.run')\n__import__('vinc_client')\n",
     ["dynamic import", "dynamic import"]),
    ("import subprocess\nsubprocess.run(['python', '-c', 'import vinc_client'])\n", ["import in a string"]),
    ('"""from vinc_client import A"""\n# import vinc_client\nlog = "vinc_client is not installed"\n', []),
])
def test_the_scan_sees_every_form(source, shapes):
    assert [shape for _, shape in client_imports(source)] == shapes


def _wheel_modules():
    import vinc
    root = Path(vinc.__file__).resolve().parent
    return root, sorted(root.rglob("*.py"))


def test_no_module_in_the_wheel_imports_vinc_client():
    root, modules = _wheel_modules()
    names = {path.relative_to(root).as_posix() for path in modules}
    # 훑는 범위가 비어 있지 않은지: SDK, 어댑터, 번들된 CLI 런타임이 모두 들어 있어야 한다.
    assert {"sdk/client.py", "_runtime/api/cli_gate.py", "integrations/langgraph/middleware.py"} <= names
    left = []
    for path in modules:
        source = path.read_text(encoding="utf-8")
        if CLIENT in source:
            left += [f"{path.relative_to(root).as_posix()}:{row} {shape}" for row, shape in client_imports(source)]
    assert not left, "the wheel still imports vinc_client: " + ", ".join(left)
