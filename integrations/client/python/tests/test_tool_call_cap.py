from concurrent.futures import ThreadPoolExecutor

from vinc_client.tools import CAP_MESSAGE, TOOL_CALLS_PER_RUN, ToolCallCap


def test_cap_counts_resets_and_names_its_limit():
    cap = ToolCallCap(2)
    assert [cap.spend() for _ in range(3)] == [True, True, False]
    assert cap.message == CAP_MESSAGE.format(limit=2) and "2 per run" in cap.message
    cap.reset()
    assert cap.spend() is True
    assert ToolCallCap().limit == TOOL_CALLS_PER_RUN == 4
    assert all(ToolCallCap(None).spend() for _ in range(50))


def test_cap_holds_under_parallel_tool_calls():
    # 도구 노드는 한 턴의 호출을 스레드로 병렬 실행한다. 상한은 그 안에서도 정확해야 한다.
    cap = ToolCallCap(4)
    with ThreadPoolExecutor(16) as pool:
        granted = list(pool.map(lambda _: cap.spend(), range(64)))
    assert granted.count(True) == 4
