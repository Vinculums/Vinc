"""A review graph that reads Vinc, stops for a person, and records only what was approved.

    context -> review -> approve (interrupt) -> record

Run it against your own graph with two keys: a read-only key for the reading
nodes and a write key for the recording node only.

    VINC_API_KEY=vinc_ro_...  VINC_WRITE_KEY=vinc_sk_...  python approve_then_record.py
"""
from __future__ import annotations

import os
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from vinc_client import VincClient

from vinc_langgraph import get_vinc_context, record_episode


class ReviewState(TypedDict, total=False):
    question: str
    context: str | None
    draft: str
    approved: bool
    about: list[str]
    written: dict[str, Any] | None


def build_graph(reader: VincClient, writer: VincClient, review=None):
    """``review`` turns (question, context) into a draft; a model call in real use."""
    review = review or (lambda question, context: f"Reviewed: {question}")

    def context_node(state: ReviewState) -> ReviewState:
        return {"context": get_vinc_context(reader, state["question"])}

    def review_node(state: ReviewState) -> ReviewState:
        return {"draft": review(state["question"], state.get("context"))}

    def approve_node(state: ReviewState) -> ReviewState:
        # 사람이 재개할 때까지 여기서 멈춘다. 재개 값이 참일 때만 기록한다.
        return {"approved": bool(interrupt({"draft": state["draft"]}))}

    def record_node(state: ReviewState) -> ReviewState:
        if not state.get("approved"):
            return {"written": None}
        return {"written": record_episode(writer, state["draft"], about=state.get("about") or [])}

    graph = StateGraph(ReviewState)
    graph.add_node("context", context_node)
    graph.add_node("review", review_node)
    graph.add_node("approve", approve_node)
    graph.add_node("record", record_node)
    graph.add_edge(START, "context")
    graph.add_edge("context", "review")
    graph.add_edge("review", "approve")
    graph.add_edge("approve", "record")
    graph.add_edge("record", END)
    return graph.compile(checkpointer=InMemorySaver())


if __name__ == "__main__":
    app = build_graph(VincClient(), VincClient(os.environ["VINC_WRITE_KEY"]))
    config = {"configurable": {"thread_id": "review-1"}}
    paused = app.invoke({"question": "Does the new secondary button meet contrast on dark?",
                         "about": []}, config)
    print("waiting for approval:", paused["__interrupt__"][0].value)
    print(app.invoke(Command(resume=input("approve? [y/N] ").lower() == "y"), config))
