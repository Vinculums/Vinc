"""승인된 계획만 순차 실행하며 불확실한 호출을 자동으로 재시도하지 않는다."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .protocol import digest, encoded, finite, verify_plan


def utcnow():
    return datetime.now(timezone.utc)


def check_approval(plan, approval, now=None):
    now = now or utcnow()
    if approval.get("approved") is not True or approval.get("plan_sha256") != plan["plan_sha256"]:
        raise ValueError("explicit approval for this exact plan required")
    for name in ("approved_by", "decision_ref", "run_id", "run_dir"):
        if not isinstance(approval.get(name), str) or not approval[name].strip():
            raise ValueError("approval identity missing")
    if approval.get("second_vendor") != "deferred_single_vendor_pilot":
        raise ValueError("second-vendor disposition must be approved explicitly")
    issued = datetime.fromisoformat(approval["approved_at"])
    expires = datetime.fromisoformat(approval["expires_at"])
    if issued.tzinfo is None or expires.tzinfo is None or not issued <= now < expires:
        raise ValueError("approval expired or not yet valid")
    if type(approval.get("max_calls")) is not int or approval["max_calls"] != plan["call_count"]:
        raise ValueError("exact call cap required")
    if not finite(approval.get("max_usd"), plan["reserved_usd"]):
        raise ValueError("dollar cap insufficient")


def append(path, value):
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(encoded(value).decode() + "\n")
        f.flush()
        os.fsync(f.fileno())


def completed(plan, approval, path):
    jobs = {j["key"]: j for j in plan["requests"]}
    started, done = {}, {}
    if not path.exists():
        return done
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise ValueError("partial journal line; do not retry uncertain calls")
    for line in raw.splitlines():
        row = json.loads(line)
        key = row["key"]
        if (key not in jobs or row["plan_sha256"] != plan["plan_sha256"]
                or row["approval_sha256"] != digest(approval)):
            raise ValueError("journal identity mismatch")
        if row["event"] == "started":
            if key in started or key in done or row["request_sha256"] != digest(jobs[key]):
                raise ValueError("duplicate or changed request")
            started[key] = row
        elif row["event"] == "completed":
            if key not in started or key in done or row["result_sha256"] != digest(row["result"]):
                raise ValueError("invalid completion")
            validate_result(row["result"], plan["settings"], jobs[key])
            done[key] = row["result"]
        else:
            raise ValueError("failed/unknown attempt; new owner decision required")
    if set(started) != set(done):
        raise ValueError("in-flight outcome unknown; do not resend")
    return done


def validate_result(r, settings, job):
    u = r["usage"]
    if any(type(u.get(k)) is not int or u[k] < 0 for k in ("prompt_tokens", "completion_tokens", "total_tokens")):
        raise ValueError("missing token usage")
    if u["total_tokens"] != u["prompt_tokens"] + u["completion_tokens"]:
        raise ValueError("inconsistent token usage")
    if u["prompt_tokens"] > job["input_token_allowance"] or u["completion_tokens"] > settings["max_completion_tokens"]:
        raise ValueError("token reservation exceeded")
    if r["model"] != settings["model"] + "-" + settings["model_version"]:
        raise ValueError("returned model version drifted")
    if not isinstance(r["text"], str) or not r["text"].strip() or r["finish_reason"] != "stop" or r.get("refusal"):
        raise ValueError("empty, refused, filtered or truncated response")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("redirect refused")


def azure_call(settings, job):
    # 자동 재시도·리디렉션·벤더 대체가 없는 표준 라이브러리 전송이다.
    key = os.environ["AZURE_OPENAI_API_KEY"]
    url = (settings["endpoint"].rstrip("/") + "/openai/deployments/"
           + quote(settings["deployment"], safe="") + "/chat/completions?api-version="
           + quote(settings["api_version"], safe=""))
    req = Request(url, data=encoded(job["body"]), method="POST",
                  headers={"api-key": key, "Content-Type": "application/json"})
    with build_opener(NoRedirect).open(req, timeout=settings["timeout_seconds"]) as response:
        data = json.load(response)
    choice = data["choices"][0]
    return {"text": choice["message"].get("content") or "", "refusal": choice["message"].get("refusal"),
            "finish_reason": choice["finish_reason"], "model": data["model"],
            "usage": data["usage"], "response_id": data["id"],
            "system_fingerprint": data.get("system_fingerprint")}


def execute(plan, cases, approval, out, transport=azure_call):
    verify_plan(plan, cases)
    check_approval(plan, approval)
    out = Path(out)
    if out.resolve() != Path(approval["run_dir"]).resolve():
        raise ValueError("approval is bound to one run directory")
    if transport is azure_call and not os.environ.get("AZURE_OPENAI_API_KEY"):
        raise ValueError("AZURE_OPENAI_API_KEY is missing")
    out.mkdir(parents=True, exist_ok=True)
    lock = out / "run.lock"
    # 강제 종료 뒤 남은 잠금은 수동 확인한다. 다른 실행의 잠금을 지우지 않는다.
    with lock.open("x", encoding="utf-8") as f:
        f.write(approval["run_id"])
    try:
        journal = out / "journal.jsonl"
        done = completed(plan, approval, journal)
        for job in plan["requests"]:
            if job["key"] in done:
                continue
            check_approval(plan, approval)
            common = {"key": job["key"], "plan_sha256": plan["plan_sha256"],
                      "approval_sha256": digest(approval)}
            append(journal, {**common, "event": "started", "at": utcnow().isoformat(),
                             "request_sha256": digest(job)})
            result = None
            try:
                result = transport(plan["settings"], job)
                validate_result(result, plan["settings"], job)
            except Exception as exc:
                # 예외 메시지에는 키나 요청 본문이 있을 수 있으므로 종류만 남긴다.
                append(journal, {**common, "event": "failed", "error_type": type(exc).__name__,
                                 "result": result, "at": utcnow().isoformat()})
                raise RuntimeError("request failed; stopped without retry") from None
            append(journal, {**common, "event": "completed", "result": result,
                             "result_sha256": digest(result), "at": utcnow().isoformat()})
            done[job["key"]] = result
        return {"completed": len(done), "plan_sha256": plan["plan_sha256"]}
    finally:
        lock.unlink()
