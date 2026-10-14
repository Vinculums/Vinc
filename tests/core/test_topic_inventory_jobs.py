"""진단 조회권은 데이터를 바꾸거나 다른 소유자의 결과를 내주지 않는다."""
import threading
from types import SimpleNamespace

from api.changes import ChangeMarker, counts
from api.jobs import JobQueue
from api.topic_inventory_jobs import InventoryJobs


OWNER="user:synthetic"
OTHER="user:other"
RESULT={"ready":True,"counts_known":True,"counts":{"Concept":2},"documents":0,"blockers":[]}


def test_pending_is_fast_and_owner_bound_then_cache_is_invalidated():
    queue=JobQueue()
    entered,release=threading.Event(),threading.Event()
    changes=ChangeMarker()
    inventories=InventoryJobs(queue,changes.inventory_revision)
    def work():
        entered.set()
        assert release.wait(5)
        return RESULT
    try:
        result=inventories.start(OWNER,"synthetic/topic",work)
        assert result["inventory_state"]=="pending" and not result["counts_known"]
        assert entered.wait(5)
        ticket=result["inventory_ticket"]
        assert inventories.status(OTHER,ticket)==inventories.status(OTHER,"not-a-ticket")
        assert inventories.start(OWNER,"another/topic",work)["inventory_state"]=="busy"
        release.set()
        assert inventories._entries[ticket].job._done.wait(5)
        assert inventories._entries[ticket].job.result == {"executed":True,"kind":"conversion-inventory"}
        assert inventories.status(OWNER,ticket)["ready"]
        assert inventories.start(OWNER,"synthetic/topic",work)["inventory_ticket"]==ticket
        changes.bump(OTHER)
        assert inventories.status(OWNER,ticket)["ready"]
        changes.bump(OWNER)
        stale=inventories.status(OWNER,ticket)
        assert stale["inventory_state"]=="stale" and stale["counts"] is None
    finally:
        release.set()
        queue.shutdown(drain=True)


def test_invalidation_during_work_cannot_be_overwritten_by_completion():
    queue=JobQueue()
    release=threading.Event()
    inventories=InventoryJobs(queue,lambda owner:("epoch",False))
    try:
        first=inventories.start(OWNER,"topic",lambda:(release.wait(5),RESULT)[1])
        inventories.invalidate(OWNER)
        release.set()
        assert inventories._entries[first["inventory_ticket"]].job._done.wait(5)
        assert inventories.status(OWNER,first["inventory_ticket"])["inventory_state"]=="stale"
    finally:
        release.set()
        queue.shutdown(drain=True)


def test_pending_source_writes_refuse_cached_readiness():
    marker=ChangeMarker()
    job=SimpleNamespace(_done=threading.Event(),status="queued")
    marker.bump_when_done(OWNER,job)
    assert marker.inventory_revision(OWNER)[1]
    assert not marker.inventory_revision(OTHER)[1]
    job.status="done";job.result={};job._done.set()
    assert not marker.inventory_revision(OWNER)[1]
    assert not counts("/topic-conversion")


def test_failed_work_never_looks_empty_and_finished_tickets_expire():
    queue=JobQueue()
    clock=[0]
    inventories=InventoryJobs(queue,lambda owner:("epoch",False),clock=lambda:clock[0],ttl=10)
    def fail():
        raise ValueError("private payload must not be returned")
    try:
        first=inventories.start(OWNER,"topic",fail)
        ticket=first["inventory_ticket"]
        assert inventories._entries[ticket].job._done.wait(5)
        result=inventories.status(OWNER,ticket)
        assert result["inventory_state"]=="failed" and result["counts"] is None
        assert "private" not in str(result)
        clock[0]=11
        assert inventories.status(OWNER,ticket)["inventory_state"]=="unknown"
    finally:
        queue.shutdown(drain=True)
