"""토픽 이동 저널 집계는 판과 상태만 세고 식별자를 내지 않는다. 워크플로는 사본만 읽고 정리한다."""
import json
from pathlib import Path
import re

import pytest
import yaml

from api import topic_conversion as conversion
from scripts import topic_journal_inventory as inventory
from store.backend import open_store
from store.config import load_config
from store.embedded_schema import render_embedded_ddl

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/topic-journal-inventory.yml"
AUDIT = ROOT / ".github/workflows/topic-snapshot-audit.yml"

# 출력에 나오면 안 되는 값: 소유자, 대상, 토픽, 실행 식별자.
OWNER = "user:private-owner-0001"
TARGET = "team:00000000-0000-4000-8000-000000000001"
TOPIC = "private/topic-name"

# (inventory_version, status): 판을 적지 않은 저널은 1판이다.
SYNTHETIC = [
    (None, "prepared"), (None, "active"), (None, "cancelled"),
    (1, "graph_committed"), (1, "creating_target_keys"), (1, "active"),
    (2, "prepared"), (2, "active"), (2, "active"), (2, "cancelled"),
    (3, "prepared"), (3, "active"), (3, "creating_target_keys"),
]
EXPECTED = {
    "by_protocol_status": {
        "v1/active": 2, "v1/cancelled": 1, "v1/creating_target_keys": 1,
        "v1/graph_committed": 1, "v1/prepared": 1,
        "v2/active": 2, "v2/cancelled": 1, "v2/prepared": 1,
        "v3/active": 1, "v3/creating_target_keys": 1, "v3/prepared": 1,
    },
    # 열린 1·2판: v1 prepared, graph_committed, creating_target_keys + v2 prepared.
    "open_v1_or_v2": 4,
    "total": 13,
}


def _make_store(path: Path, journals):
    store = open_store(load_config({"data_dir": str(path)}))
    try:
        for statement in render_embedded_ddl(dim=4):
            store.run(statement)
        for index, (version, status) in enumerate(journals):
            journal = {"source": OWNER, "target": TARGET, "topic": TOPIC, "status": status}
            if version is not None:
                journal["inventory_version"] = version
            conversion.save_journal(store, f"run-{index:04d}-private", journal)
    finally:
        store.close()
    return path


@pytest.fixture
def copy(tmp_path):
    return _make_store(tmp_path / "synthetic.db", SYNTHETIC)


def test_counts_mixed_versions_and_statuses(copy):
    assert inventory.inventory(copy) == EXPECTED


def test_cli_prints_one_line_without_identifiers(copy, capsys):
    assert inventory.main([str(copy)]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and json.loads(out) == EXPECTED
    for private in (OWNER, TARGET, TOPIC, "run-0000", "private"):
        assert private not in out


def test_cli_out_file_holds_the_same_line_and_stdout_stays_empty(copy, tmp_path, capsys):
    report = tmp_path / "report.json"
    assert inventory.main([str(copy), "--out", str(report)]) == 0
    assert capsys.readouterr().out == ""
    text = report.read_text(encoding="utf-8")
    assert text.count("\n") == 1 and json.loads(text) == EXPECTED


def test_empty_store_counts_zero(tmp_path):
    empty = _make_store(tmp_path / "empty.db", [])
    assert inventory.inventory(empty) == {"by_protocol_status": {}, "open_v1_or_v2": 0, "total": 0}


def test_summary_buckets_unrecognised_values_instead_of_echoing_them():
    rows = [
        {"status": "prepared", "inventory_version": 1},
        {"status": "run-1234-abcd", "inventory_version": 2},
        {"status": "Prepared"},
        {"status": None, "inventory_version": 2},
        {"inventory_version": 2},
        {"status": "active", "inventory_version": "2"},
        {"status": "active", "inventory_version": True},
        {"status": "active", "inventory_version": 9},
        {"status": "prepared", "inventory_version": 9},
    ]
    report = inventory.summarize(rows)
    assert report == {
        "by_protocol_status": {
            "unknown/active": 3, "unknown/prepared": 1,
            "v1/prepared": 1, "v1/unknown": 1, "v2/unknown": 3,
        },
        # 알 수 없는 상태의 1·2판은 닫혔다고 세지 않는다. 알 수 없는 판은 1·2판이 아니다.
        "open_v1_or_v2": 5,
        "total": 9,
    }
    assert re.fullmatch(r"[a-z0-9_/:,{}\"]+", json.dumps(report, separators=(",", ":")))


def test_summary_open_count_follows_the_fence_rule_of_the_old_protocols():
    for version in (1, 2):
        for status in ("active", "cancelled"):
            journal = {"source": OWNER, "target": TARGET, "topic": TOPIC, "status": status,
                       "inventory_version": version}
            assert conversion._terminal_visible(None, journal)
            assert inventory.summarize([journal])["open_v1_or_v2"] == 0
        journal = {"source": OWNER, "target": TARGET, "topic": TOPIC, "status": "prepared",
                   "inventory_version": version}
        assert not conversion._terminal_visible(None, journal)
        assert inventory.summarize([journal])["open_v1_or_v2"] == 1


def test_unusable_copies_report_a_code_and_nothing_else(tmp_path, capsys):
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    target = tmp_path / "real.db"
    target.write_bytes(b"x")
    link = tmp_path / "link.db"
    link.symlink_to(target)
    for path in (tmp_path / "missing-private-name.db", empty, link, tmp_path):
        assert inventory.main([str(path)]) == 1
        out = capsys.readouterr().out
        assert json.loads(out) == {"error": "inventory_failed"}
        assert "private" not in out and str(tmp_path) not in out


def test_unreadable_file_is_refused_without_the_engine_text(tmp_path, capsys):
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database " * 64)
    assert inventory.main([str(junk)]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "inventory_failed"}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _load(WORKFLOW)["jobs"]["inventory"]["steps"]


def _step(name: str) -> dict:
    return next(s for s in _steps() if s.get("name") == name)


def test_workflow_is_dispatch_only_with_the_audit_identity_and_permissions():
    data, audit = _load(WORKFLOW), _load(AUDIT)
    assert data[True] == {"workflow_dispatch": None}
    assert data["permissions"] == audit["permissions"] == {"contents": "read", "id-token": "write"}
    assert data["concurrency"] == audit["concurrency"]
    login = next(s for s in _steps() if str(s.get("uses", "")).startswith("azure/login"))
    audit_login = next(s for s in audit["jobs"]["audit"]["steps"]
                       if str(s.get("uses", "")).startswith("azure/login"))
    assert login == audit_login
    env, audit_env = data["jobs"]["inventory"]["env"], audit["jobs"]["audit"]["env"]
    for key in ("RG", "STORAGE", "SHARE"):
        assert env[key] == audit_env[key]


def test_workflow_resolves_the_hub_app_from_the_target_setting():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert _step("허브 앱을 대상 설정에서 푼다")["run"].strip() == 'python3 scripts/hub_target.py >> "$GITHUB_ENV"'
    assert 'az containerapp show -n "$HUB_APP" -g "$RG"' in text
    assert not re.search(r"vinc-(hub|account)", text)


def test_workflow_only_reads_the_share_and_removes_only_its_own_snapshot():
    text = WORKFLOW.read_text(encoding="utf-8")
    for forbidden in ("upload-artifact", "containerapp update", "revision", "storage file upload",
                      "storage file delete", "share-rm", "keys list", "role assignment", "az storage blob",
                      "--delete-snapshots", "gh workflow", "git push"):
        assert forbidden not in text
    assert len(re.findall(r"az storage share snapshot\b", text)) == 1
    deletes = re.findall(r"az storage share delete[^\n]*", text)
    assert len(deletes) == 1 and '--snapshot "$SNAPSHOT"' in deletes[0]
    # 쓰기 계열 az 동사는 스냅숏 만들기와 지우기 둘뿐이다.
    verbs = set(re.findall(r"\baz ([a-z-]+ [a-z-]+)", text))
    assert verbs == {"containerapp show", "storage share"}


def test_workflow_checks_the_operator_and_main_before_touching_azure():
    steps = _steps()
    names = [s.get("name") or s.get("uses") for s in steps]
    gate = _step("Require operator and main branch")
    assert 'test "$GITHUB_REF" = refs/heads/main' in gate["run"]
    assert "PROD_OPERATORS" in json.dumps(gate["env"])
    assert names.index("Require operator and main branch") < names.index("azure/login@v2")


def test_workflow_cleans_up_the_snapshot_and_the_copy_whatever_happens():
    steps = _steps()
    names = [s.get("name") for s in steps]
    order = ["Create isolated share snapshot", "Restore private copy of the store",
             "Count journals in the copy", "Publish aggregate result only",
             "Remove only this inventory snapshot", "Remove private runner copy"]
    assert [names.index(n) for n in order] == sorted(names.index(n) for n in order)
    removal = _step("Remove only this inventory snapshot")
    assert removal["if"] == "${{ always() && steps.snap.outputs.ts != '' }}"
    assert removal["env"] == {"SNAPSHOT": "${{ steps.snap.outputs.ts }}"}
    assert 'test -n "$SNAPSHOT"' in removal["run"]
    assert _step("Publish aggregate result only")["if"] == "always()"
    assert _step("Remove private runner copy")["if"] == "always()"
    assert _step("Create isolated share snapshot")["id"] == "snap"


def test_workflow_keeps_native_engine_output_in_private_logs_and_prints_one_line():
    count = _step("Count journals in the copy")["run"]
    assert "scripts/topic_journal_inventory.py restored/db --out inventory-report.json" in count
    assert "> restored/inventory-private.log 2>&1" in count
    publish = _step("Publish aggregate result only")["run"]
    assert "$GITHUB_STEP_SUMMARY" in publish and "inventory-report.json" in publish
    assert "restored/" not in publish
