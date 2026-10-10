# -*- coding: utf-8 -*-
"""판단기 비교 실행기: 한 번만 보내고, 상한에서 멈추고, 첫 실패에서 멈춘다. 네트워크는 쓰지 않는다."""
import json
from pathlib import Path

import pytest

from rag_eval import judge_compare as J
from rag_eval import judge_execute as X

ROOT = Path(__file__).resolve().parents[2]
COMMUNITY = ROOT / "rag_eval/evalsets/predicate-judge-comparison-community-v1/contract.json"
KEY = "sk-test-do-not-write-me"


def answer(provider, body, model, p=0.7):
    usage = {"input_tokens": 40, "output_tokens": 2}
    if provider == "jev":
        return {"model": model, "usage": usage, "answers": {k: {"type": "noul", "noul": p} for k in body["questions"]}}
    return {"model": model, "usage": usage,
            "answers": [{"type": "predicate", "name": q["name"], "probability": p} for q in body["questions"]]}


class Fake:
    def __init__(self, provider, fail_at=None, status=200, wrong_model_at=None):
        self.provider, self.fail_at, self.status, self.wrong_model_at = provider, fail_at, status, wrong_model_at
        self.bodies, self.keys, self.urls = [], set(), set()

    def __call__(self, url, key, body, timeout):
        self.bodies.append(body)
        self.keys.add(key)
        self.urls.add(url)
        if self.fail_at == len(self.bodies):
            return self.status, None
        model = "someone-else" if self.wrong_model_at == len(self.bodies) else body["model"]
        return 200, answer(self.provider, body, model)


def prepare(tmp_path, provider="jev", contract=COMMUNITY):
    directory = tmp_path / provider
    assert J.main(["prepare", "--root", str(ROOT), "--directory", str(directory), "--provider", provider,
                   "--contract", str(contract)]) == 0
    return directory


def go(directory, send, tmp_path, set_name="calib", contract=COMMUNITY, **kw):
    return X.run(root=ROOT, contract_path=contract, directory=directory, set_name=set_name,
                 journal=directory / f"journal-{set_name}.jsonl", ledger=tmp_path / "ledger.jsonl", key=KEY, send=send,
                 sleep=lambda s: None, log=lambda m: None, **kw)


@pytest.mark.parametrize("provider", ["jev", "openai_decisions"])
def test_a_resumed_run_sends_every_request_exactly_once_and_imports(tmp_path, provider):
    directory = prepare(tmp_path, provider)
    send = Fake(provider)
    assert go(directory, send, tmp_path, limit=5) == X.OK
    assert go(directory, send, tmp_path) == X.OK
    assert go(directory, send, tmp_path) == X.OK                      # 남은 것이 없으면 아무것도 보내지 않는다
    expected = J.read_journal(directory / "requests-calib.jsonl")
    assert [J.digest(b) for b in send.bodies] == [r["body_sha256"] for r in expected]
    assert len(J.read_journal(tmp_path / "ledger.jsonl")) == len(expected) == 234
    assert send.urls == {json.loads((directory / "provenance.json").read_text())["endpoint"]}
    # 실행기가 쓴 저널을 오프라인 임포터가 그대로 받는다
    assert J.main(["import", "--root", str(ROOT), "--directory", str(directory), "--set", "calib",
                   "--journal", str(directory / "journal-calib.jsonl"), "--contract", str(COMMUNITY)]) == 0
    assert len(J.read_journal(directory / "calib.jsonl")) == 117 * 5
    for name in ("journal-calib.jsonl", "calib.jsonl", "calib-usage.json"):
        assert KEY not in (directory / name).read_text(encoding="utf-8")
    assert KEY not in (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")


def test_the_cap_counts_both_providers_in_one_ledger(tmp_path):
    jev, openai = prepare(tmp_path, "jev"), prepare(tmp_path, "openai_decisions")
    first, second = Fake("jev"), Fake("openai_decisions")
    assert go(jev, first, tmp_path, limit=4, max_requests=6) == X.OK
    assert go(openai, second, tmp_path, max_requests=6) == X.CAP_REACHED
    assert (len(first.bodies), len(second.bodies)) == (4, 2)
    assert go(openai, second, tmp_path, max_requests=6) == X.CAP_REACHED
    assert len(second.bodies) == 2


def test_the_command_line_cannot_raise_the_contract_cap(tmp_path):
    directory = prepare(tmp_path)
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("".join(json.dumps({"provider": "openai_decisions", "set": "c", "request_id": f"x{i}"}) + "\n"
                              for i in range(3000)))
    send = Fake("jev")
    assert go(directory, send, tmp_path, max_requests=10 ** 6) == X.CAP_REACHED
    assert send.bodies == []


@pytest.mark.parametrize("status", [429, 500, 0])
def test_the_first_http_failure_stops_the_run_and_is_never_retried(tmp_path, status):
    directory = prepare(tmp_path)
    send = Fake("jev", fail_at=3, status=status)
    assert go(directory, send, tmp_path) == X.STOPPED
    rows = J.read_journal(directory / "journal-calib.jsonl")
    assert len(rows) == 3 and rows[-1]["error"] == {"http_status": status} and "response" not in rows[-1]
    with pytest.raises(X.Refused):
        go(directory, send, tmp_path)
    assert len(send.bodies) == 3
    with pytest.raises((ValueError, KeyError)):                       # 끊긴 저널은 임포트되지 않는다
        J.main(["import", "--root", str(ROOT), "--directory", str(directory), "--set", "calib",
                "--journal", str(directory / "journal-calib.jsonl"), "--contract", str(COMMUNITY)])


def test_an_answer_the_importer_would_refuse_stops_after_one_request(tmp_path):
    directory = prepare(tmp_path, "openai_decisions")
    send = Fake("openai_decisions", wrong_model_at=1)
    assert go(directory, send, tmp_path) == X.STOPPED
    rows = J.read_journal(directory / "journal-calib.jsonl")
    assert len(send.bodies) == 1 and "invalid_response" in rows[0]["error"] and rows[0]["response"]["model"] == "someone-else"


def test_a_counted_request_without_a_receipt_is_not_sent_again(tmp_path):
    directory = prepare(tmp_path)
    first = J.read_journal(directory / "requests-calib.jsonl")[0]
    (tmp_path / "ledger.jsonl").write_text(json.dumps({"provider": "jev", "set": "calib", "request_id": first["request_id"]}) + "\n")
    send = Fake("jev")
    with pytest.raises(X.Refused):
        go(directory, send, tmp_path)
    assert send.bodies == []


def test_refusals_send_nothing(tmp_path):
    directory = prepare(tmp_path)
    send = Fake("jev")
    with pytest.raises(X.Refused):                                    # 보정을 동결하기 전에는 측정 요청이 없다
        go(directory, send, tmp_path, set_name="c")
    with pytest.raises(X.Refused):
        X.run(root=ROOT, contract_path=COMMUNITY, directory=directory, set_name="calib", journal=directory / "j.jsonl",
              ledger=tmp_path / "ledger.jsonl", key="", send=send, log=lambda m: None)
    assert go(directory, send, tmp_path, dry_run=True) == X.OK
    # 개인 그래프 문장이 든 VIN-425 계약은 실행 승인이 없다
    other = prepare(tmp_path / "full", "jev", J.CONTRACT)
    with pytest.raises(X.Refused):
        go(other, send, tmp_path, contract=J.CONTRACT)
    assert send.bodies == []
    requests = directory / "requests-calib.jsonl"
    lines = requests.read_text(encoding="utf-8").splitlines()
    requests.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")  # 요청 파일이 달라지면 보내지 않는다
    with pytest.raises(X.Refused):
        go(directory, send, tmp_path)
    assert send.bodies == []


def test_community_contract_holds_only_community_text_and_fits_the_cap():
    contract = json.loads(COMMUNITY.read_text(encoding="utf-8"))
    assert set(contract["sets"]) == {"calib", "c"}
    assert all("community" in spec["gold"] for spec in contract["sets"].values())
    assert contract["gates"] == json.loads(J.CONTRACT.read_text(encoding="utf-8"))["gates"]
    assert 2 * contract["per_provider"]["requests"] <= contract["execution"]["max_requests_total"] == 3000
    sets = J.check_contract(ROOT, contract)
    assert (len(sets["calib"]), len(sets["c"])) == (117, 140)


def test_measurement_requests_leave_only_after_the_freeze_and_import_cleanly(tmp_path):
    import hashlib
    from rag_eval.laya.pins import QUESTIONS
    args = ["--root", str(ROOT), "--directory", None, "--contract", str(COMMUNITY)]
    directory = prepare(tmp_path)
    args[3] = str(directory)
    send = Fake("jev")
    assert go(directory, send, tmp_path) == X.OK
    assert J.main(["import", *args, "--set", "calib", "--journal", str(directory / "journal-calib.jsonl")]) == 0
    rows = J.read_jsonl(directory / "calib.jsonl")
    pins = json.loads((directory / "provenance.json").read_text(encoding="utf-8"))
    (directory / "calibration").mkdir()
    for pred in QUESTIONS:                                           # fit_calib 이 쓰는 모양의 보정 파일(합성 값)
        for lang in ("en", "ko"):
            entry = {"predicate": pred, "lang": lang, "seed": 20261007, "questions_hash": J.questions_hash(),
                     "n": sum(r["predicate"] == pred and ("en" if r["lang"] == "mixed" else r["lang"]) == lang for r in rows),
                     "temperature": 1, "min_confidence": 0.6, "min_bucket_n": 30,
                     "source_input_sha256": J.file_hash(directory / "calib.jsonl")}
            entry["calibration_id"] = hashlib.sha256(json.dumps(entry, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
            J.write_json(directory / "calibration" / f"calib-{pred}-{lang}.json", {"pins": pins, **entry})
    assert J.main(["release-eval", *args]) == 0
    assert sorted(p.name for p in directory.glob("requests-*.jsonl")) == ["requests-c.jsonl", "requests-calib.jsonl"]
    assert go(directory, send, tmp_path, set_name="c") == X.OK
    assert len(send.bodies) == 234 + 280 == len(J.read_journal(tmp_path / "ledger.jsonl"))
    assert J.main(["import", *args, "--set", "c", "--journal", str(directory / "journal-c.jsonl")]) == 0
    measured = J.read_jsonl(directory / "c.jsonl")
    assert len(measured) == 140 * 5 and all(r["p_cal"] is not None and r["abstain_below"] == 0.6 for r in measured)
    path = directory / "calibration/calib-supports-ko.json"         # 동결 뒤 보정이 바뀌면 더는 보내지 않는다
    path.write_text(path.read_text(encoding="utf-8").replace('"temperature": 1', '"temperature": 2'), encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        go(directory, send, tmp_path, set_name="c")
