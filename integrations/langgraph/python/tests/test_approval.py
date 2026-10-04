import asyncio
import json
from pathlib import Path
from typing import TypedDict

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from vinc_client import OutputGate, RoleOutput
from vinc_langgraph import create_person_only_node

PROFILES = {p['id']:p for p in json.loads((Path(__file__).parents[3] /
    'fixtures/output_gate/proposed-role-checks.json').read_text(encoding='utf-8'))['profiles']}


class State(TypedDict, total=False):
    role_output: dict
    vinc_approval: dict


@pytest.mark.parametrize('structured', [False, True])
@pytest.mark.parametrize('approved', [False, True])
@pytest.mark.parametrize('async_mode', [False, True])
@pytest.mark.parametrize('role,action,text', [
    ('concept:agent-reviewer-judgement','merge','Merge the PR.'),
    ('concept:agent-copy-owner','sign_off','Sign off the copy.'),
    ('concept:agent-marketing','pricing','Please decide pricing.'),
])
def test_real_graph_resume_uses_the_saved_model_output_once(structured, approved, async_mode, role, action, text):
    draft = {'text':'Ready.' if structured else text,
             'person_only_requests':[{'action':action,'reason':'Decide.'}] if structured else []}
    class RecordedModel(BaseChatModel):
        calls: int = 0
        @property
        def _llm_type(self):
            return 'recorded-role-output'
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls += 1
            return ChatResult(generations=[ChatGeneration(message=AIMessage(json.dumps(draft)))])
    model = RecordedModel()
    def model_node(state):
        response = model.invoke('Review the package.')
        return {'role_output':RoleOutput.model_validate_json(response.content).model_dump()}
    graph = StateGraph(State)
    graph.add_node('model', model_node)
    graph.add_node('approval', create_person_only_node(
        OutputGate(PROFILES[role]), role_id=role, run_id='recorded-1'))
    graph.add_edge(START, 'model')
    graph.add_edge('model','approval')
    graph.add_edge('approval', END)
    app = graph.compile(checkpointer=InMemorySaver())
    config = {'configurable':{'thread_id':'recorded-1'}}
    def invoke(value):
        return asyncio.run(app.ainvoke(value, config)) if async_mode else app.invoke(value, config)
    paused = invoke({})
    assert model.calls == 1 and 'vinc_approval' not in paused
    request = paused['__interrupt__'][0].value
    assert request['actions'] == [action] and request['output'] == draft
    resumed = invoke(Command(resume={'request_id':request['request_id'],'approved':approved}))
    assert model.calls == 1
    assert resumed['vinc_approval']['status'] == ('approved' if approved else 'rejected')
    assert resumed['vinc_approval']['output'] == (draft if approved else None)
