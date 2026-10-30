import asyncio
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from vinc_client.output_gate import PENDING_HEADER, OutputGate, OutputGateStopped
from vinc_langgraph import create_output_gate_middleware, evaluate_output_node

FIXTURES = Path(__file__).resolve().parents[3] / 'fixtures' / 'output_gate'
CASES = json.loads((FIXTURES / 'hand-labelled.json').read_text(encoding='utf-8'))['cases']
PROFILES = {p['id']: p for p in json.loads((FIXTURES / 'proposed-role-checks.json').read_text(encoding='utf-8'))['profiles']}


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_frozen_hand_labels_through_sync_async_middleware_and_node(case):
    state = {'messages': [AIMessage(case['answer'], id='m1')]}
    gate = OutputGate(PROFILES[case['role_id']], self_authored=case['self_authored'])
    result = evaluate_output_node(state, gate)['vinc_output_gate']
    assert result['verdict'] == case['expected']
    for middleware in (create_output_gate_middleware(gate),
                       create_output_gate_middleware(gate, on_interrupt='stop')):
        if case['expected'] == 'pass':
            assert middleware.after_model(state, None) is None
            assert asyncio.run(middleware.aafter_model(state, None)) is None
            continue
        for run in (lambda: middleware.after_model(state, None),
                    lambda: asyncio.run(middleware.aafter_model(state, None))):
            if case['expected'] == 'interrupt' and middleware.on_interrupt == 'deliver':
                update = run()
                message = update['messages'][0]
                assert message.id == 'm1' and message.content.startswith(case['answer'].rstrip())
                assert PENDING_HEADER in message.content
            else:
                with pytest.raises(OutputGateStopped) as exc:
                    run()
                assert exc.value.result.verdict == case['expected']


def test_an_interrupted_message_without_an_id_stops_instead_of_adding_a_second_answer():
    gate = OutputGate(PROFILES['concept:agent-reviewer-judgement'])
    with pytest.raises(OutputGateStopped) as exc:
        create_output_gate_middleware(gate).after_model({'messages': [AIMessage('Merge this PR.')]}, None)
    assert exc.value.result.verdict == 'interrupt'


def test_on_interrupt_takes_deliver_or_stop():
    with pytest.raises(ValueError):
        create_output_gate_middleware(OutputGate(PROFILES['concept:agent-dev-core']), on_interrupt='later')


def test_tools_continue_but_missing_or_unchecked_output_cannot_pass():
    gate = OutputGate(PROFILES['concept:agent-dev-core'])
    state = {'messages': [AIMessage('', tool_calls=[{'name':'vinc_brief', 'args':{}, 'id':'call1'}])]}
    middleware = create_output_gate_middleware(gate)
    assert middleware.after_model(state, None) is None
    assert evaluate_output_node(state, gate)['vinc_output_gate']['verdict'] == 'block'
    assert evaluate_output_node({}, gate)['vinc_output_gate']['verdict'] == 'block'


def test_model_state_cannot_supply_trusted_proof():
    gate = OutputGate(PROFILES['concept:agent-dev-core'])
    state = {'messages': [AIMessage('Tests passed.')], 'proofs': [{'success': True, 'action':'tests'}]}
    assert evaluate_output_node(state, gate)['vinc_output_gate']['verdict'] == 'block'


@pytest.mark.parametrize('async_mode', [False, True])
@pytest.mark.parametrize('text,expected', [('Okay', 'pass'), ('Merge this PR.', 'interrupt'),
                                         ("I've merged PR #9.", 'block')])
def test_real_agent_graph_stops_without_repeating_model(text, expected, async_mode):
    class RecordedModel(BaseChatModel):
        calls: int = 0
        @property
        def _llm_type(self):
            return 'recorded-output'
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls += 1
            return ChatResult(generations=[ChatGeneration(message=AIMessage(text))])
    model = RecordedModel()
    middleware = create_output_gate_middleware(OutputGate(PROFILES['concept:agent-reviewer-judgement']))
    agent = create_agent(model, middleware=[middleware])
    def run():
        data = {'messages':[{'role':'user','content':'Review'}]}
        return asyncio.run(agent.ainvoke(data)) if async_mode else agent.invoke(data)
    if expected == 'pass':
        assert run()['messages'][-1].content == text
    elif expected == 'interrupt':
        # 사람에게 가는 답은 나가고, 기다리는 결정이 이름과 사유로 붙는다. 답이 둘이 되지 않는다.
        messages = run()['messages']
        assert sum(isinstance(m, AIMessage) for m in messages) == 1
        assert messages[-1].content == text + '\n\n' + PENDING_HEADER + '\n- merge: merge'
    else:
        with pytest.raises(OutputGateStopped) as exc:
            run()
        assert exc.value.result.verdict == expected
    assert model.calls == 1
