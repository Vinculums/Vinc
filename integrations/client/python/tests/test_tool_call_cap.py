from vinc_client.tools import CAP_MESSAGE, TOOL_CALLS_PER_RUN, VINC_TOOL_NAMES, ToolCallCap, cap_message


def test_cap_counts_and_names_its_limit():
    cap = ToolCallCap(2)
    assert [cap.spend() for _ in range(3)] == [True, True, False]
    assert cap.message == cap_message(2) == CAP_MESSAGE.format(limit=2) and "2 per run" in cap.message
    assert ToolCallCap().limit == TOOL_CALLS_PER_RUN == 4
    assert all(ToolCallCap(None).spend() for _ in range(50))
    assert VINC_TOOL_NAMES == ("vinc_brief", "vinc_search")
