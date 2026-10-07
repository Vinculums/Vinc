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
