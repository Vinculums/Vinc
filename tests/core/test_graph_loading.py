"""Continuation payload limits, lifetime and owner/scope isolation."""
import threading
from api.graph_loading import GraphReads, GraphWire
import json


def payload(node='concept:synthetic'):
    return {'executed': True, 'nodes': [{'id': node}], 'edges': []}


def producer(publish, control):
    publish(payload())
    return 200, {**payload(), 'properties': {'unicode': '한글 ❖'}}


def test_owner_scope_token_isolation_and_idempotent_result():
    reads = GraphReads()
    try:
        code, first = reads.start('user:a', 'hub/.7', producer); token = first['snapshot']['token']
        assert code == 200
        for owner, scope in [('user:b', 'hub/.7'), ('team:a', 'hub/.7'), ('user:a', 'hub/.8')]:
            assert reads.finish(owner, scope, token)[0] == 404
            assert reads.cancel(owner, scope, token)[0] == 404
        assert reads.finish('user:a', 'hub/.7', 'not-a-token')[0] == 404
        assert reads.finish('user:a', 'hub/.7', token) == reads.finish('user:a', 'hub/.7', token)
        assert reads.finish('user:a', 'hub/.7', token)[1]['properties']['unicode'] == '한글 ❖'
    finally:
        reads.close()


def test_ownership_conversion_invalidates_only_both_affected_scopes():
    reads = GraphReads()
    try:
        _, first = reads.start('user:a', 'scope', producer)
        _, other = reads.start('user:b', 'scope', producer)
        a, b = first['snapshot']['token'], other['snapshot']['token']
        reads.finish('user:a', 'scope', a)
        reads.finish('user:b', 'scope', b)
        reads.invalidate_owner('user:a')
        assert reads.finish('user:a', 'scope', a)[0] == 404
        assert reads.finish('user:b', 'scope', b)[0] == 200
    finally:
        reads.close()


def test_expiry_and_closed_store_fail_without_data():
    now = [0]; reads = GraphReads(ttl=10, clock=lambda: now[0])
    code, first = reads.start('a', 'scope', producer); token = first['snapshot']['token']
    reads.finish('a', 'scope', token); now[0] = 11
    assert reads.finish('a', 'scope', token)[0] == 404
    reads.close(); assert reads.finish('a', 'scope', token)[0] == 404
    assert reads.start('a', 'scope', producer) is None


def test_oversized_preview_is_not_retained_or_exposed():
    reads = GraphReads(max_bytes=2048)
    def oversized(publish, control):
        assert not publish({'executed': True, 'nodes': [{'id': 'c', 'label': 'x'*1_000_000}]})
        return 200, payload()
    try:
        code, first = reads.start('a', 'scope', oversized)
        assert code == 200 and 'snapshot' not in first
        assert sum(r.size for r in reads._reads.values()) <= reads.max_bytes
        assert all(r.preview is None for r in reads._reads.values())
    finally:
        reads.close()


def test_preview_and_result_share_one_total_memory_budget():
    reads = GraphReads(max_bytes=4096)
    def large(publish, control):
        publish({**payload(), 'label': 'p'*1000})
        return 200, {**payload(), 'props': 'f'*3000}
    try:
        code, first = reads.start('a', 'scope', large)
        if 'snapshot' in first:
            assert reads.finish('a', 'scope', first['snapshot']['token'])[0] == 503
        else:
            assert code == 503
        assert sum(r.size for r in reads._reads.values()) <= reads.max_bytes
        assert all(r.preview is None and r.result is None for r in reads._reads.values())
    finally:
        reads.close()


def test_pending_capacity_cancel_releases_owned_producer_and_hides_result():
    entered = threading.Event(); reads = GraphReads(max_running=1)
    def blocked(publish, control):
        publish(payload()); entered.set(); control.cancelled.wait(2); control.check()
        return 200, payload()
    try:
        code, first = reads.start('a', 'scope', blocked); assert entered.wait(1)
        token = first['snapshot']['token']
        assert reads.finish('a', 'scope', token, wait=0)[1]['graphSnapshotPending']
        assert reads.start('b', 'scope', producer) is None
        assert reads.cancel('a', 'scope', token)[0] == 200
        reads._reads[token].thread.join(2)
        assert reads.finish('a', 'scope', token)[0] == 404
        assert reads.start('b', 'scope', producer)[0] == 200
    finally:
        reads.close()


def test_deadline_and_shutdown_signal_cancellation_before_join():
    reads = GraphReads(read_timeout=.05)
    def blocked(publish, control):
        publish(payload()); control.cancelled.wait(2); control.check()
    code, first = reads.start('a', 'scope', blocked)
    read = next(iter(reads._reads.values())); read.thread.join(2)
    assert not read.thread.is_alive() and read.control.cancelled.is_set()
    assert reads.finish('a', 'scope', first['snapshot']['token'])[0] == 404
    reads.close()
    reads = GraphReads()
    code, first = reads.start('a', 'scope', blocked)
    reads.close()
    assert not reads._reads


def test_failure_does_not_retain_private_query_contents():
    reads = GraphReads()
    def fail(publish, control):
        raise RuntimeError('private query contents')
    try:
        code, result = reads.start('a', 'scope', fail)
        assert code == 503 and 'private' not in str(result)
    finally:
        reads.close()


def test_completed_entries_can_be_replaced_for_next_initial_read():
    reads = GraphReads(max_entries=1)
    try:
        code, first = reads.start('a', 'one', producer); reads.finish('a', 'one', first['snapshot']['token'])
        code, next_read = reads.start('a', 'two', producer)
        assert code == 200 and next_read['snapshot']['token'] != first['snapshot']['token']
        assert reads.finish('a', 'one', first['snapshot']['token'])[0] == 404
    finally:
        reads.close()


def test_retained_full_result_is_serialized_once_and_direct_call_contract_is_preserved():
    reads = GraphReads()
    try:
        code, first = reads.start('a', 'scope', producer)
        token = first['snapshot']['token']
        code, wire = reads.finish('a', 'scope', token, wire=True)
        assert code == 200 and isinstance(wire, GraphWire) and wire.endswith(b'\n')
        assert json.loads(wire) == reads.finish('a', 'scope', token)[1]
        assert all(isinstance(r.result[1], bytes) for r in reads._reads.values())
        assert len(wire) <= sum(r.size for r in reads._reads.values()) <= reads.max_bytes
    finally:
        reads.close()
