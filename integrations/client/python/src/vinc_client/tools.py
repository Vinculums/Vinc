"""Names, wording and argument schemas of the read tools, shared by every framework package.

Declarations only: each package builds its tools from these, so the model sees
the same tool schema whichever framework runs it.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

BRIEF_NAME = "vinc_brief"
BRIEF_DESCRIPTION = ("Look up one node in the user's Vinc knowledge graph: a decision, record "
                     "or concept with its summary, document excerpts and relations.")
SEARCH_NAME = "vinc_search"
SEARCH_DESCRIPTION = ("Search the user's Vinc knowledge graph by text and meaning; returns "
                      "matching nodes and document passages with their ids.")
SEARCH_LIMIT_DEFAULT = 5


def _no_titles(schema: dict[str, Any]) -> None:
    # 모델에 가는 스키마에 title 을 싣지 않는다. LangChain 은 지우고 Agent Framework 는
    # 남기므로, 여기서 지워야 두 패키지의 스키마가 같아진다.
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)


class BriefArgs(BaseModel):
    model_config = ConfigDict(json_schema_extra=_no_titles)
    query: str = Field(description="A question, or an exact node id such as decision:some-slug.")


class SearchArgs(BaseModel):
    model_config = ConfigDict(json_schema_extra=_no_titles)
    query: str = Field(description="What to look for.")
    limit: int = Field(default=SEARCH_LIMIT_DEFAULT, ge=1, le=20,
                       description="How many matches of each kind.")


# 한 런에서 두 읽기 도구를 합쳐 부를 수 있는 횟수. 두 패키지가 같은 수와 같은 문장을 쓴다.
# 근거: [[decision:vin318-parity-is-judged-on-three-runs-with-a-shared-tool-call-cap-2026-10-07]]
TOOL_CALLS_PER_RUN = 4
VINC_TOOL_NAMES = (BRIEF_NAME, SEARCH_NAME)
CAP_MESSAGE = "Vinc tool call cap reached ({limit} per run); answer from what you already have."


def cap_message(limit: int | None) -> str:
    return CAP_MESSAGE.format(limit=limit)


class ToolCallCap:
    """Counts ``vinc_brief``/``vinc_search`` calls for ONE run; past ``limit`` the tool
    answers with :attr:`message` instead of calling Vinc. ``limit=None`` never caps.

    Make a new one per run. Agent Framework builds its tools around a fresh cap in
    ``before_run``; LangGraph does not use this class and counts the run's tool calls
    from graph state instead, so concurrent runs never share a counter.
    """

    def __init__(self, limit: int | None = TOOL_CALLS_PER_RUN) -> None:
        self.limit = limit
        self.used = 0

    def spend(self) -> bool:
        """Count one call. ``False`` means the cap is spent and the call must not run."""
        if self.limit is not None and self.used >= self.limit:
            return False
        self.used += 1
        return True

    @property
    def message(self) -> str:
        return cap_message(self.limit)
