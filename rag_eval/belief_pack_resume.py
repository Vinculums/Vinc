"""실패한 파일럿의 원장을 보존하고 별도 승인으로 미완료 요청만 이어 간다."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError

from rag_eval.belief_pack import protocol as p
from rag_eval.belief_pack import run as r
from rag_eval.belief_pack.review import export_review


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parent_answers(plan, approval, journal):
    raw = Path(journal).read_bytes()
    lines = raw.splitlines(keepends=True)
    if len(lines) < 2 or not raw.endswith(b'\n'):
        raise ValueError('incomplete parent journal')
    start, failure = (json.loads(x) for x in lines[-2:])
    if (start.get('event') != 'started' or failure.get('event') != 'failed'
            or failure.get('error_type') != 'HTTPError' or failure.get('result') is not None
            or start['key'] != failure['key']):
        raise ValueError('one final HTTP failure required; unknown outcomes need another protocol')
    for row in (start, failure):
        if row['plan_sha256'] != plan['plan_sha256'] or row['approval_sha256'] != p.digest(approval):
            raise ValueError('parent attempt identity changed')
    r.check_approval(plan, approval, datetime.fromisoformat(start['at']))
    # 완료 부분은 원래 검증기를 그대로 쓰고 원본에는 쓰지 않는다.
    with tempfile.TemporaryDirectory() as tmp:
        prefix = Path(tmp) / 'completed-prefix.jsonl'
        prefix.write_bytes(b''.join(lines[:-2]))
        done = r.completed(plan, approval, prefix)
    expected = plan['requests'][:len(done)]
    if set(done) != {j['key'] for j in expected} or len(done) >= plan['call_count']:
        raise ValueError('parent must be a completed prefix')
    job = plan['requests'][len(done)]
    if start['key'] != job['key'] or start['request_sha256'] != p.digest(job):
        raise ValueError('failed request identity changed')
    return done, job


def make_plan(parent, cases, approval, journal):
    p.verify_plan(parent, cases)
    done, failed = parent_answers(parent, approval, journal)
    s = parent['settings']
    requests = parent['requests'][len(done):]
    def reserved(j):
        return (j['input_token_allowance'] * s['input_usd_per_million']
                + s['max_completion_tokens'] * s['output_usd_per_million']) / 1e6
    used = sum((v['usage']['prompt_tokens'] * s['input_usd_per_million']
                + v['usage']['completion_tokens'] * s['output_usd_per_million']) / 1e6 for v in done.values())
    body = dict(schema='vin409.continuation.v1', parent_plan_sha256=parent['plan_sha256'],
                parent_approval_sha256=p.digest(approval), parent_journal_sha256=file_hash(journal),
                source_sha256=hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
                settings=s, requests=requests, call_count=len(requests), preserved=len(done),
                retry_failed_key=failed['key'], initial_wait_seconds=60, min_interval_seconds=30,
                total_attempt_cap=len(done) + 1 + len(requests),
                known_parent_usd=used, failed_parent_reservation_usd=reserved(failed),
                continuation_reservation_usd=sum(reserved(j) for j in requests),
                reserved_usd=used + reserved(failed) + sum(reserved(j) for j in requests))
    return {**body, 'plan_sha256': p.digest(body)}


def verify(plan, parent, cases, parent_approval, parent_journal):
    if make_plan(parent, cases, parent_approval, parent_journal) != plan:
        raise ValueError('continuation plan or parent evidence changed')


def execute(plan, parent, cases, parent_approval, parent_journal, approval, out,
            transport=r.azure_call, sleeper=time.sleep):
    verify(plan, parent, cases, parent_approval, parent_journal)
    r.check_approval(plan, approval)
    if (approval.get('retry_failed_key') != plan['retry_failed_key']
            or approval.get('total_attempt_cap') != plan['total_attempt_cap']):
        raise ValueError('explicit failed-request retry and cumulative attempt cap required')
    out = Path(out)
    if out.resolve() != Path(approval['run_dir']).resolve():
        raise ValueError('approval is bound to one continuation directory')
    if transport is r.azure_call and not os.environ.get('AZURE_OPENAI_API_KEY'):
        raise ValueError('AZURE_OPENAI_API_KEY is missing')
    out.mkdir(parents=True, exist_ok=True)
    lock = out / 'run.lock'
    with lock.open('x', encoding='utf-8') as f:
        f.write(approval['run_id'])
    try:
        journal = out / 'journal.jsonl'
        done = r.completed(plan, approval, journal)
        first = True
        for job in plan['requests']:
            if job['key'] in done:
                continue
            sleeper(plan['initial_wait_seconds'] if first else plan['min_interval_seconds'])
            first = False
            r.check_approval(plan, approval)
            common = dict(key=job['key'], plan_sha256=plan['plan_sha256'], approval_sha256=p.digest(approval))
            r.append(journal, dict(common, event='started', at=r.utcnow().isoformat(), request_sha256=p.digest(job)))
            result = None
            try:
                result = transport(plan['settings'], job)
                r.validate_result(result, plan['settings'], job)
            except Exception as exc:
                detail = {'http_status': exc.code} if isinstance(exc, HTTPError) else {}
                r.append(journal, dict(common, event='failed', error_type=type(exc).__name__,
                                       result=result, at=r.utcnow().isoformat(), **detail))
                raise RuntimeError('continuation stopped without retry') from None
            r.append(journal, dict(common, event='completed', result=result,
                                   result_sha256=p.digest(result), at=r.utcnow().isoformat()))
            done[job['key']] = result
        return dict(completed=len(done), preserved=plan['preserved'], total=len(done)+plan['preserved'])
    finally:
        lock.unlink()


def review(plan, parent, cases, parent_approval, parent_journal, approval, journal, out):
    verify(plan, parent, cases, parent_approval, parent_journal)
    prior, _ = parent_answers(parent, parent_approval, parent_journal)
    latest = r.completed(plan, approval, Path(journal))
    if len(latest) != plan['call_count'] or set(prior) & set(latest):
        raise ValueError('all remaining unique responses required')
    answers = {**prior, **latest}
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    # 원래 채점기의 입력 어댑터다. 실호출 원장은 보존하고 승인 불가능한 문맥으로 구별한다.
    context = dict(approved=False, purpose='offline review projection only, never execution approval',
                   parent_journal_sha256=file_hash(parent_journal), parent_approval_sha256=p.digest(parent_approval),
                   continuation_journal_sha256=file_hash(journal), continuation_approval_sha256=p.digest(approval),
                   continuation_plan_sha256=plan['plan_sha256'])
    p.write_new(out/'review-context.json', context)
    projection = out/'review-projection.jsonl'
    for job in parent['requests']:
        common = dict(key=job['key'], plan_sha256=parent['plan_sha256'], approval_sha256=p.digest(context),
                      derived=True, source='parent' if job['key'] in prior else 'continuation')
        r.append(projection, dict(common, event='started', request_sha256=p.digest(job)))
        r.append(projection, dict(common, event='completed', result=answers[job['key']],
                                  result_sha256=p.digest(answers[job['key']])))
    return export_review(parent, cases, context, projection, out/'labels')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('prepare', 'execute', 'review'))
    for arg in ('parent-plan', 'cases', 'parent-approval', 'parent-journal', 'out'):
        parser.add_argument('--'+arg, type=Path, required=True)
    for arg in ('plan', 'approval', 'journal'):
        parser.add_argument('--'+arg, type=Path)
    a = parser.parse_args()
    parent, cases = p.read(a.parent_plan), p.load_cases(a.cases)
    pa = p.read(a.parent_approval)
    if a.command == 'prepare':
        result = make_plan(parent, cases, pa, a.parent_journal)
        p.write_new(a.out, result)
        print(json.dumps({k: result[k] for k in ('plan_sha256', 'call_count', 'reserved_usd', 'total_attempt_cap')}))
        return
    if a.plan is None or a.approval is None or (a.command == 'review' and a.journal is None):
        parser.error('--plan/--approval required; review also requires --journal')
    args = (p.read(a.plan), parent, cases, pa, a.parent_journal, p.read(a.approval))
    result = (execute(*args, a.out) if a.command == 'execute'
              else review(*args, a.journal, a.out))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
