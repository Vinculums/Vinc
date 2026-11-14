"""`hatch_build.py` 의 재작성: 휠에 실리는 소스에 vinc_client import 가 남지 않는다.

hatchling 이 필요하다. 설치본 시험을 도는 CI 단계는 이 파일 옆에 hatch_build.py 를 복사하고 hatchling 을 깐다.
"""
import ast
import importlib.util
from pathlib import Path

import pytest


def _load():
    here = Path(__file__).resolve()
    for candidate in (here.parent / "hatch_build.py", here.parents[1] / "hatch_build.py"):
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location("vincs_hatch_build", candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    pytest.fail("hatch_build.py is not next to this test or in the package directory")


build = _load()


def rewrite(source):
    result = build.rewrite_client_imports(source, "sample.py")
    ast.parse(result)
    assert build.leftover_client_imports(result) == []
    return result


def test_top_level_from_import():
    assert rewrite("from vinc_client import A, B\nfrom vinc_client.run import x\n") == (
        "from vinc.sdk import A, B\nfrom vinc.sdk.run import x\n")


def test_indented_imports_in_every_block():
    source = (
        "def f():\n"
        "    from vinc_client.run import a\n"
        "    return a\n"
        "\n"
        "class C:\n"
        "    if True:\n"
        "        try:\n"
        "            from vinc_client.run import build_run_fragment\n"
        "        except ImportError:\n"
        "            build_run_fragment = None\n"
    )
    result = rewrite(source)
    assert result == source.replace("vinc_client", "vinc.sdk")


def test_one_line_blocks_and_continuations():
    source = (
        "if x: from vinc_client import a\n"
        "from vinc_client import (\n"
        "    b,\n"
        "    c,\n"
        ")\n"
        "from \\\n"
        "    vinc_client.run import d\n"
    )
    assert rewrite(source) == source.replace("vinc_client", "vinc.sdk")


def test_a_variable_named_like_the_package_is_left_alone():
    source = "vinc_client = 1\nif vinc_client: from vinc_client import a\nprint(vinc_client)\n"
    assert rewrite(source) == "vinc_client = 1\nif vinc_client: from vinc.sdk import a\nprint(vinc_client)\n"


def test_import_with_alias():
    source = ("import vinc_client as vc\n"
              "import os, vinc_client.run as run, sys\n"
              "def f():\n"
              "    import vinc_client.tools as tools\n")
    assert rewrite(source) == (
        "import vinc.sdk as vc\n"
        "import os, vinc.sdk.run as run, sys\n"
        "def f():\n"
        "    import vinc.sdk.tools as tools\n")


def test_dynamic_import_of_a_literal_name():
    source = ("import importlib\n"
              "def f():\n"
              "    return importlib.import_module('vinc_client.run'), __import__(\"vinc_client\"), "
              "importlib.import_module(name='vinc_client.tools')\n")
    assert rewrite(source) == source.replace("vinc_client", "vinc.sdk")


def test_korean_text_on_the_same_line_does_not_shift_the_edit():
    source = "def f():\n    return importlib.import_module('vinc_client.run')  # 가나다라 vinc_client\n"
    assert rewrite(source) == (
        "def f():\n    return importlib.import_module('vinc.sdk.run')  # 가나다라 vinc_client\n")


def test_docstrings_comments_and_plain_strings_are_not_touched():
    source = (
        '"""Uses vinc_client and shows:\n'
        '\n'
        '    from vinc_client import X\n'
        '"""\n'
        "import logging\n"
        "\n"
        "# from vinc_client import nothing\n"
        'log = logging.getLogger("vinc_client")\n'
        "\n"
        "\n"
        "def f():\n"
        '    """Run `import vinc_client` first.\n'
        "\n"
        "    >>> from vinc_client import Y\n"
        '    """\n'
        '    return "vinc_client is not installed; pip install vinc-client"\n'
    )
    assert build.rewrite_client_imports(source, "sample.py") == source
    assert build.leftover_client_imports(source) == []


@pytest.mark.parametrize("source", [
    "import vinc_client\n",
    "def f():\n    import vinc_client.run\n",
    "import os, vinc_client\n",
    "import subprocess\nsubprocess.run(['python', '-c', 'import vinc_client; print(1)'])\n",
    "CODE = 'x = 1\\nfrom vinc_client.run import build_run_fragment\\n'\n",
    "import importlib\nimportlib.import_module(f'vinc_client.{name}')\n",
    "import importlib\nimportlib.import_module('vinc_client'\n    '.run')\n",
], ids=["bare", "bare-indented", "bare-in-a-list", "dash-c-snippet", "code-in-a-string",
        "fstring-name", "name-over-two-lines"])
def test_what_cannot_be_rewritten_stops_the_build(source):
    with pytest.raises(RuntimeError, match="sample.py"):
        build.rewrite_client_imports(source, "sample.py")


def test_a_file_that_does_not_parse_and_names_the_package_stops_the_build():
    with pytest.raises(RuntimeError, match="cannot parse"):
        build.rewrite_client_imports("from vinc_client import (\n", "broken.py")


def test_leftover_check_reports_line_numbers():
    left = build.leftover_client_imports("x = 1\n\ndef f():\n    from vinc_client import a\n")
    assert [row for row, _ in left] == [4]
