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
    assert json.loads(out.getvalue())["status"] == 0
    manifest.write_text(json.dumps({"docs": [], "nodes": []}), encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        assert cli.main(args) == 1
    assert out.getvalue() == ""
    result = json.loads(err.getvalue())
    assert result["status"] == 1
    assert result["missing"] == [{"file": "source.py", "line": 1, "ref": "vinc.spec.dataplane"}]
    assert "vinc refs refresh" in result["error"]["message"]
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        assert cli.main(["refs", "check", "--unknown"]) == 2
    assert out.getvalue() == ""
    assert "unrecognized" in err.getvalue()


def test_refs_refresh_uses_the_shared_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("VINC_API_KEY", "vinc_sk_test")
    with patch("api.refs.refresh", return_value=0) as refresh:
        assert cli.main(["refs", "refresh", "--manifest", str(tmp_path / "m.json"),
                         "--root", str(tmp_path)]) == 0
    assert refresh.call_args.kwargs["manifest"] == str(tmp_path / "m.json")


def _check(tmp_path):
    manifest = tmp_path / "m.json"
    return ["refs", "check", "--manifest", str(manifest), "--root", str(tmp_path)], manifest


def _wl(ident: str) -> str:
    """런타임에 `[[ident]]` 를 만든다. 이 소스 파일 자체에 `[[...]]` 리터럴을 두면
    전 리포 refs 게이트가 그것을 인용으로 읽어, 매니페스트에 없는 테스트용 id 가
    게이트를 깨뜨린다. 그래서 대괄호를 이어 붙여 소스에는 리터럴이 없게 한다."""
    return "[" + "[" + ident + "]" + "]"


def test_refs_check_scans_code_files_and_reports_skipped(tmp_path):
    """VIN-889 — 펌웨어·코드 파일의 인용도 대조하고, 스캔 안 한 확장자의 인용은 알린다."""
    (tmp_path / "pump.cpp").write_text(f"// {_wl('vinc.spec.dataplane §7')}\n", encoding="utf-8")
    (tmp_path / "pins.h").write_text(f"/* {_wl('vinc.spec.dataplane')} */\n", encoding="utf-8")
    # .mjs 는 기본 스캔 밖이다 — 인용이 있으면 '건너뜀'으로 보고된다(조용히 통과 금지).
    (tmp_path / "helper.mjs").write_text(f"// {_wl('vinc.spec.dataplane')}\n", encoding="utf-8")
    args, manifest = _check(tmp_path)
    manifest.write_text(json.dumps({"docs": ["vinc.spec.dataplane"], "nodes": []}),
                        encoding="utf-8")
    out = io.StringIO()
    with redirect_stdout(out):
        assert cli.main(args) == 0
    res = json.loads(out.getvalue())
    assert res["status"] == 0
    assert res["cited"] == 2                      # .cpp 와 .h 가 스캔됐다(.mjs 는 아니다)
    assert any(s.endswith("helper.mjs") for s in res.get("skipped", [])), res


def test_refs_check_blocks_a_dead_citation_in_a_code_file(tmp_path):
    """VIN-889 후속 — 이제 .cpp 의 죽은 인용도 잡힌다(예전엔 cited:0 으로 통과)."""
    (tmp_path / "pump.cpp").write_text(f"// {_wl('decision:does-not-exist')}\n", encoding="utf-8")
    args, manifest = _check(tmp_path)
    manifest.write_text(json.dumps({"docs": [], "nodes": []}), encoding="utf-8")
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        assert cli.main(args) == 1
    assert json.loads(err.getvalue())["missing"][0]["ref"] == "decision:does-not-exist"


def test_refs_check_accepts_a_manifest_listed_non_vinc_doc_id(tmp_path):
    """VIN-890 — 매니페스트에 실재하면 vinc. 접두가 아닌 사용자 문서 id 도 인용으로 받는다."""
    (tmp_path / "s.py").write_text(f"# {_wl('sprout.prd.watering-v1')}\n", encoding="utf-8")
    args, manifest = _check(tmp_path)
    manifest.write_text(json.dumps({"docs": ["sprout.prd.watering-v1"], "nodes": []}),
                        encoding="utf-8")
    out = io.StringIO()
    with redirect_stdout(out):
        assert cli.main(args) == 0
    res = json.loads(out.getvalue())
    assert res["status"] == 0 and res["missing"] == [] and res["invalid"] == []


def test_refs_check_still_rejects_a_non_vinc_id_absent_from_the_manifest(tmp_path):
    """VIN-890 가 지나치게 받지 않는지 — 매니페스트에 없는 사용자 id 는 여전히 결함이다."""
    (tmp_path / "s.py").write_text(f"# {_wl('sprout.prd.watering-v1')}\n", encoding="utf-8")
    args, manifest = _check(tmp_path)
    manifest.write_text(json.dumps({"docs": [], "nodes": []}), encoding="utf-8")
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        assert cli.main(args) == 1
    assert json.loads(err.getvalue())["invalid"][0]["reason"] == "not_graph_id"
