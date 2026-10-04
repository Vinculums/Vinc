import asyncio
import json
from pathlib import Path

import pytest
from agent_framework import (Agent, BaseChatClient, ChatResponse, Executor, Message,
                             WorkflowBuilder, WorkflowContext, handler)
from vinc_client import ApprovalDecision, OutputGate, RoleOutput
from vinc_agent_framework import VincPersonOnlyExecutor

PROFILES = {p['id']:p for p in json.loads((Path(__file__).parents[3] /
    'fixtures/output_gate/proposed-role-checks.json').read_text(encoding='utf-8'))['profiles']}


@pytest.mark.parametrize('structured', [False, True])
@pytest.mark.parametrize('approved', [False, True])
@pytest.mark.parametrize('role,action,text', [
    ('concept:agent-reviewer-judgement','merge','Merge the PR.'),
    ('concept:agent-copy-owner','sign_off','Sign off the copy.'),
    ('concept:agent-marketing','pricing','Please decide pricing.'),
])
def test_native_request_info_resume_does_not_rerun_the_agent(structured, approved, role, action, text):
    draft = {'text':'Ready.' if structured else text,
             'person_only_requests':[{'action':action,'reason':'Decide.'}] if structured else []}
    class RecordedClient(BaseChatClient):
        def __init__(self):
            super().__init__()
            self.calls = 0
        async def _inner_get_response(self, *, messages, stream, options, **kwargs):
            self.calls += 1
            assert options['response_format'] is RoleOutput
            return ChatResponse(messages=Message('assistant',[json.dumps(draft)]))
    client = RecordedClient()
    agent = Agent(client=client, name='reviewer')
    class ModelExecutor(Executor):
        @handler
        async def run_model(self, question: str, ctx: WorkflowContext[RoleOutput]) -> None:
            result = await agent.run(question, tools=[], options={
                'response_format':RoleOutput, 'tool_choice':'none'})
            await ctx.send_message(result.value)
    model = ModelExecutor(id='model')
    approval = VincPersonOnlyExecutor(OutputGate(PROFILES[role]), role_id=role, run_id='recorded-1')
    workflow = WorkflowBuilder(start_executor=model).add_edge(model,approval).build()
    async def run():
        events = [event async for event in workflow.run('Review the package.', stream=True)]
        requests = [event for event in events if event.type == 'request_info']
        assert len(requests) == 1 and client.calls == 1
        request = requests[0]
        assert request.data.actions == [action] and request.data.output.model_dump() == draft
        reply = ApprovalDecision(request_id=request.data.request_id, approved=approved)
        events = [event async for event in workflow.run(
            responses={request.request_id:reply}, stream=True)]
        outputs = [event.data for event in events if event.type == 'output']
        assert len(outputs) == 1 and client.calls == 1
        assert outputs[0].status == ('approved' if approved else 'rejected')
        assert (outputs[0].output.model_dump() if outputs[0].output else None) == (draft if approved else None)
    asyncio.run(run())
