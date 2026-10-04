"""Vinc for LangGraph and LangChain agents.

Design and scope: [[vinc.plan.framework-integration-packages-2026-09-28]].
``VincContextMiddleware`` needs the ``agents`` extra and is imported lazily.
"""
from __future__ import annotations

from typing import Any

__version__ = "0.1.0"

from .helpers import (aget_vinc_context, arecord_episode, build_system_message,  # noqa: E402
                      create_vinc_tools, get_vinc_context, last_user_text, record_episode)
from .output_gate import (create_output_gate_middleware, evaluate_output_node,  # noqa: E402
                          require_output_pass)


def __getattr__(name: str) -> Any:
    # 미들웨어는 langchain(agents 추가 설치)이 있어야 한다. 노드 헬퍼만 쓰는 사람에게
    # 그 의존성을 강요하지 않으려고 이름을 처음 쓸 때 가져온다.
    if name == "VincContextMiddleware":
        from .middleware import VincContextMiddleware
        return VincContextMiddleware
    raise AttributeError(name)


__all__ = ["__version__", "get_vinc_context", "aget_vinc_context", "build_system_message",
           "create_vinc_tools", "record_episode", "arecord_episode", "last_user_text",
           "VincContextMiddleware", "create_output_gate_middleware", "evaluate_output_node",
           "require_output_pass"]
