# -*- coding: utf-8 -*-
"""토픽 이동 라우트의 본문: `POST /topic-conversion`(조사·예약·상태·공개·취소).

함수는 사이드카 앱(`api.server.VincApp`)을 첫 인자로 받고, 앱의 같은 이름 메서드가 그대로 넘긴다
(`_resume_bounded_conversion` → :func:`resume_bounded_conversion` 처럼 앱 쪽 이름은 그대로다). 본문끼리도
앱의 메서드를 거쳐 부른다. 그래서 인스턴스에서 바꿔 끼운 메서드가 원래처럼 닿는다.

조사 잡 표(`_inventory_jobs`)와 유한 배치 표(`_conversion_jobs`)는 처음 쓸 때 앱의 속성으로 만든다.
앱의 `close` 가 `_conversion_jobs` 를 닫고, 조사 잡 표를 만드는 잠금(`_inventory_jobs_lock`)은 앱이 든다.
이동 모듈들(`topic_conversion` 등)은 부를 때 가져온다.
"""
from __future__ import annotations

import os


def write_topic_conversion(app, body):
    from . import topic_conversion as conversion
    from . import revstamp
    from store.topic_transfer import TransferRefused
    if body.get("action") == "preflight" and os.environ.get("VINC_TOPIC_CONVERSION") != "1":
        return 503, {"error": "topic_conversion_unavailable"}
    source = body.get("owner")
    action = body.get("action")
    conventions_policy = body.get("conventions_policy", "retain")
    if conventions_policy not in ("retain", "move_original"):
        return 400, {"error": "invalid_conventions_policy"}
    if not isinstance(source, str) or not source.startswith("user:"):
        return 400, {"error": "personal_source_required"}
    if action not in ("preflight", "inventory-status", "stage", "status", "release", "cancel"):
        return 400, {"error": "invalid_conversion_action"}
    # An account may not acknowledge a move in a non-durable hub setup.
    if action in ("preflight", "stage", "release", "cancel") and \
            app._durable is None and getattr(app, "_conversion_durable", None) is None:
        return 503, {"error": "conversion_requires_durable_ack"}
    if action in ("preflight", "inventory-status"):
        if app.store is None:
            return 503, {"error": "store_unavailable"}
        from .topic_inventory_jobs import InventoryJobs
        if not hasattr(app, "_inventory_jobs"):
            # HTTP 스레드가 동시에 처음 들어와도 큐와 조회권을 한 벌만 만든다.
            with app._inventory_jobs_lock:
                if not hasattr(app, "_inventory_jobs"):
                    app._inventory_jobs = InventoryJobs(app.jobs, app.changes.inventory_revision)
        if action == "inventory-status":
            return 200, app._inventory_jobs.status(source, body.get("ticket"))
        topic = str(body.get("topic") or "")
        def diagnose():
            conn = getattr(app.store, "_conn", None)
            try:
                if conn is not None:
                    conn.set_query_timeout(120_000)
                return conversion.preflight(app.store, source, topic, conventions_policy=conventions_policy)
            except TransferRefused as exc:
                return {"ready": False, "scope": "topic", "topic": topic, "counts_known": False,
                        "counts": None, "documents": None, "blockers": [str(exc).split(":", 1)[0]]}
            except (ValueError, KeyError, RuntimeError):
                return {"ready": False, "scope": "topic", "topic": topic, "counts_known": False,
                        "counts": None, "documents": None, "blockers": ["conversion_inventory_failed"],
                        "inventory_state": "failed"}
            finally:
                if conn is not None:
                    conn.set_query_timeout(0)
        return 200, app._inventory_jobs.start(source, topic, diagnose, policy=conventions_policy)
    if hasattr(app, "_inventory_jobs") and action in ("stage", "release", "cancel"):
        app._inventory_jobs.invalidate(source)
    def work():
        try:
            if action == "preflight":
                return conversion.preflight(app.store, source, str(body.get("topic") or ""))
            args = dict(source=source, target=body["target"], topic=body["topic"], run=body["run"])
            journal = conversion._get(app.store, args["run"])
            if journal is not None and conversion.journal_protocol(journal) == conversion.PROTOCOL_V3:
                conversion._identity(journal, source=source, target=body["target"], topic=body["topic"])
                return app._resume_bounded_conversion(action, args, body.get("fingerprint"), conventions_policy)
            if action == "status" and journal is None and hasattr(app, "_conversion_jobs"):
                # 저널을 쓰기 전에 거절된 새 예약은 작업 상태에만 남는다. 예전 경로는
                # 저널이 없으면 "prepared" 를 돌려주므로 먼저 작업을 본다.
                pending = app._conversion_jobs.status(source, args["run"])
                if pending is not None:
                    return pending
            if action == "stage" and journal is None and app._exceeds_single_transaction(args):
                # 큰 토픽의 새 예약은 봉인 목록과 유한 배치로만 시작한다.
                return app._resume_bounded_conversion(action, args, body.get("fingerprint"),
                                                      conventions_policy, allow_new=True)
            if action == "stage":
                args.update(expected=body["fingerprint"], stamp=revstamp.hlc_now(app.store),
                            conventions_policy=conventions_policy)
                out = conversion.stage(app.store, **args)
            elif action == "release":
                out = conversion.release(app.store, **args)
            elif action == "cancel":
                out = conversion.cancel(app.store, **args)
            else:
                out = conversion.status(app.store, **args)
            if action in ("stage", "release", "cancel"):
                for owner in (source, body["target"]):
                    app.invalidate_owner(owner)
            return out
        except (TransferRefused, ValueError, KeyError) as exc:
            return {"status": "blocked", "error": str(exc).split(":", 1)[0]}
    out = app.jobs.run_sync("topic-conversion." + action, work)
    return (409 if out.get("status") == "blocked" else 200), out


def exceeds_single_transaction(app, args):
    """선택 범위가 기존 단일 트랜잭션 한도를 넘는가. 한도 안에서 읽기만 하고 쓰지 않는다."""
    from . import topic_conversion as conversion
    from store.topic_manifest import ManifestLimit
    try:
        conversion._inventory(app.store, args["source"], args["target"], args["topic"])
    except ManifestLimit as exc:
        return exc.reason in ("retained_payload", "retained_rows", "retained_bytes")
    except Exception:
        return False
    return False


def resume_bounded_conversion(app, action, args, expected, conventions_policy="retain",
                              allow_new=False):
    """Start a new large run only when ``allow_new``; otherwise recover a sealed v3 run."""
    from . import topic_conversion as conversion
    from .topic_conversion_jobs import ConversionJobs, StepBlocked, bounded_flow, bounded_step
    from store.topic_transfer import TransferRefused
    from . import revstamp
    durable = app._durable or app._conversion_durable
    if not hasattr(app, "_conversion_jobs"):
        # Called only on the sole writer. The measured probe has the same
        # one-hour work budget; exceeding it requires an explicit resume.
        app._conversion_jobs = ConversionJobs(app.jobs, work_seconds=3600)
    pending = app._conversion_jobs.status(args["source"], args["run"])
    if action == "status" and pending and pending["status"] in ("pending", "resume_required"):
        return pending
    if action in ("release", "cancel") and pending and pending["status"] == "pending":
        raise TransferRefused("conversion_resume_required")

    def invalidate():
        for owner in (args["source"], args["target"]):
            app.invalidate_owner(owner)

    if action == "stage":
        invalidate()

        def advance():
            try:
                return bounded_step(app.store, durable, **args, expected=expected,
                                    stamp=revstamp.hlc_now(app.store), qualified_indexes=True,
                                    allow_new=allow_new, conventions_policy=conventions_policy)
            except TransferRefused as exc:
                # 저널이 없으면 아무것도 쓰지 않은 거절이다(낡은 지문·의존 차단·다른 이동 중).
                if conversion._get(app.store, args["run"]) is None:
                    raise StepBlocked(str(exc).split(":", 1)[0]) from None
                raise
            finally:
                invalidate()

        return app._conversion_jobs.start(**args, expected=expected, advance=advance,
                                          conventions_policy=conventions_policy)
    # A pre-existing sealed indexed run passed initialize's index-policy
    # gate. Resume keeps its original index set; no new run is admitted here.
    flow = bounded_flow(app.store, durable, **args, qualified_indexes=True)
    if action == "release":
        result = flow.release(account_active=True)
    elif action == "cancel":
        result = flow.cancel()
    else:
        return flow.status()
    invalidate()
    app._conversion_jobs.forget(args["source"], args["run"])
    return result
