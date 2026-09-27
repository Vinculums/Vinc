"""제품 CLI의 인용 검사 종료 코드와 스크립트 위임을 검증한다."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from api import cli


def test_refs_check_exit_matrix(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("# [[vinc.spec.dataplane §7]]\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"docs": ["vinc.spec.dataplane"], "nodes": []}),
                        encoding="utf-8")
    args = ["refs", "check", "--manifest", str(manifest), "--root", str(tmp_path)]
    out = io.StringIO()
    with redirect_stdout(out):
        assert cli.main(args) == 0
    assert "PASS" in out.getvalue()
    manifest.write_text(json.dumps({"docs": [], "nodes": []}), encoding="utf-8")
    out = io.StringIO()
    with redirect_stdout(out):
        assert cli.main(args) == 1
    assert "BLOCK" in out.getvalue()
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        assert cli.main(["refs", "check", "--unknown"]) == 2
    assert out.getvalue() == ""
    assert "unrecognized" in err.getvalue()


def test_refs_refresh_uses_the_shared_engine(tmp_path):
    with patch("api.refs.refresh", return_value=0) as refresh:
        assert cli.main(["refs", "refresh", "--manifest", str(tmp_path / "m.json"),
                         "--root", str(tmp_path)]) == 0
    assert refresh.call_args.kwargs["manifest"] == str(tmp_path / "m.json")
