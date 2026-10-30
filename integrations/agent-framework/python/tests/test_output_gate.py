import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_framework import AgentContext, AgentResponse, Message
from agent_framework import Agent, BaseChatClient, ChatResponse
from vinc_client.output_gate import PENDING_HEADER, OutputGate, OutputGateStopped
from vinc_agent_framework import VincOutputGateMiddleware

FIXTURES = Path(__file__).resolve().parents[3] / 'fixtures' / 'output_gate'
CASES = json.loads((FIXTURES / 'hand-labelled.json').read_text(encoding='utf-8'))['cases']
PROFILES = {p['id']: p for p in json.loads((FIXTURES / 'proposed-role-checks.json').read_text(encoding='utf-8'))['profiles']}


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
@pytest.mark.parametrize('on_interrupt', ['deliver', 'stop'])
def test_original_hand_labels_through_real_framework_context(case, on_interrupt):
    context = AgentContext(agent=SimpleNamespace(), messages=[])
    middleware = VincOutputGateMiddleware(OutputGate(PROFILES[case['role_id']], self_authored=case['self_authored']),
                                          on_interrupt=on_interrupt)
    calls = []
    async def next_step():
        calls.append(1)
        context.result = AgentResponse(messages=[Message('assistant', [case['answer']])])
    if case['expected'] == 'pass':
        asyncio.run(middleware.process(context, next_step))
        assert context.metadata['vinc_output_gate']['verdict'] == 'pass'
    elif case['expected'] == 'interrupt' and on_interrupt == 'deliver':
        asyncio.run(middleware.process(context, next_step))
        assert context.metadata['vinc_output_gate']['verdict'] == 'interrupt'
        assert context.result.text.startswith(case['answer'].rstrip()) and PENDING_HEADER in context.result.text
    else:
        with pytest.raises(OutputGateStopped) as exc:
            asyncio.run(middleware.process(context, next_step))
        assert exc.value.result.verdict == case['expected']
        assert case['answer'] not in str(exc.value)
    assert len(calls) == 1


def test_stream_is_rejected_before_calling_agent():
    context = AgentContext(agent=SimpleNamespace(), messages=[], stream=True)
    middleware = VincOutputGateMiddleware(OutputGate(PROFILES['concept:agent-dev-core']))
    async def next_step():
        pytest.fail('The streaming model must not run')
    with pytest.raises(OutputGateStopped):
        asyncio.run(middleware.process(context, next_step))


@pytest.mark.parametrize('text,expected', [('Okay', 'pass'), ('Merge this PR.', 'interrupt'),
                                         ("I've merged PR #9.", 'block')])
def test_real_agent_pipeline_never_retries_a_gate_failure(text, expected):
    class RecordedClient(BaseChatClient):
        def __init__(self):
            super().__init__()
            self.calls = 0
        async def _inner_get_response(self, *, messages, stream, options, **kwargs):
            self.calls += 1
            return ChatResponse(messages=Message('assistant', [text]))
    client = RecordedClient()
    middleware = VincOutputGateMiddleware(OutputGate(PROFILES['concept:agent-reviewer-judgement']))
    agent = Agent(client=client, name='reviewer', middleware=[middleware])
    if expected == 'pass':
        assert asyncio.run(agent.run('Review')).text == text
    elif expected == 'interrupt':
        assert asyncio.run(agent.run('Review')).text == text + '\n\n' + PENDING_HEADER + '\n- merge: merge'
    else:
        with pytest.raises(OutputGateStopped) as exc:
            asyncio.run(agent.run('Review'))
        assert exc.value.result.verdict == expected
    assert client.calls == 1
