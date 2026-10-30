"""Names, wording and argument schemas of the Vinc tools, shared by every framework package.

The three read tools are always offered. The two write tools (``vinc_ingest``,
``vinc_doc_put``) are offered only to a role that may write (:func:`vinc_client.may_write`)
and only with a write key.

Declarations only: each package builds its tools from these, so the model sees
the same tool schema whichever framework runs it.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

BRIEF_NAME = "vinc_brief"
BRIEF_DESCRIPTION = ("Look up one node in the user's Vinc knowledge graph: a decision, record "
                     "or concept with its summary, document excerpts and relations.")
SEARCH_NAME = "vinc_search"
SEARCH_DESCRIPTION = ("Search the user's Vinc knowledge graph by text and meaning; returns "
                      "matching nodes and document passages with their ids.")
SEARCH_LIMIT_DEFAULT = 5
DOC_NAME = "vinc_doc_get"
DOC_DESCRIPTION = ("Read a canonical document from the user's Vinc graph by its document id, such "
                   "as a required reading your context names but does not include in full. Give "
                   "start and end to read a character range, or section to read under one heading.")
#: 문서 도구 한 번이 돌려주는 본문 글자 수의 상한. 넘으면 next_start 로 이어 읽게 한다.
DOC_MAX_CHARS = 12000


def _no_titles(schema: dict[str, Any]) -> None:
    # 모델에 가는 스키마에 title 을 싣지 않는다. LangChain 은 지우고 Agent Framework 는
    # 남기므로, 여기서 지워야 두 패키지의 스키마가 같아진다.
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)


class TemporalArgs(BaseModel):
    model_config = ConfigDict(json_schema_extra=_no_titles)
    mode: Literal["general", "current", "historical"] | None = Field(
        default=None, description="General preserves the default; current checks current validity; historical includes expired evidence.")
    historical: bool | None = Field(default=None, description="Include expired evidence (legacy option).")
    as_of: str | None = Field(default=None, description="ISO date or period overlap; incompatible with current mode.")
    known_at: str | None = Field(default=None, description="Initial creation cutoff, not restoration of an old revision.")


def temporal_options(mode=None, historical=None, as_of=None, known_at=None) -> dict[str, Any]:
    """Forward only supplied options, preserving existing client call shapes."""
    return {key: value for key, value in zip(
        ("mode", "historical", "as_of", "known_at"), (mode, historical, as_of, known_at))
        if value is not None}


class BriefArgs(TemporalArgs):
    query: str = Field(description="A question, or an exact node id such as decision:some-slug.")


class SearchArgs(TemporalArgs):
    query: str = Field(description="What to look for.")
    limit: int = Field(default=SEARCH_LIMIT_DEFAULT, ge=1, le=20,
                       description="How many matches of each kind.")


class DocArgs(BaseModel):
    model_config = ConfigDict(json_schema_extra=_no_titles)
    doc_id: str = Field(description="The document id, for example vinc.spec.example; a concept:doc. prefix is accepted.")
    start: int | None = Field(default=None, ge=0, description="First character to read.")
    end: int | None = Field(default=None, ge=1, description="Character to stop before.")
    section: str | None = Field(default=None, description="A heading of the document; reads from it to the next heading of the same or a higher level.")


INGEST_NAME = "vinc_ingest"
INGEST_DESCRIPTION = ("Save to the user's Vinc graph: merge nodes (a record, decision, episode or "
                      "concept) and edges by exact id. Search first and reuse ids you find; "
                      "props you send replace the stored props whole.")
DOC_PUT_NAME = "vinc_doc_put"
DOC_PUT_DESCRIPTION = ("Save a canonical document to the user's Vinc graph, or edit one by its "
                       "document id. When editing, pass the content_hash you read with "
                       "vinc_doc_get as expected_content_hash so a newer edit is not overwritten.")
#: 역할이 쓸 수 있을 때만 붙는 쓰기 도구. 지우기·병합은 기본 표면에 넣지 않는다.
WRITE_TOOL_NAMES = (INGEST_NAME, DOC_PUT_NAME)


class IngestArgs(BaseModel):
    model_config = ConfigDict(json_schema_extra=_no_titles)
    nodes: list[dict[str, Any]] = Field(
        description="Nodes to merge by exact id, each with id (type-prefixed, such as "
                    "record:some-slug) and type, plus title and props as needed.")
    edges: list[dict[str, Any]] | None = Field(
        default=None, description="Edges between existing or sent nodes: from, to, type, "
                                  "and evidence where the relation needs it.")


class DocPutArgs(BaseModel):
    model_config = ConfigDict(json_schema_extra=_no_titles)
    text: str = Field(description="The full document text; it replaces the stored text.")
    doc_id: str | None = Field(default=None, description="The document to edit; omit to create one.")
    title: str | None = Field(default=None, description="The document title.")
    valid_from: str | None = Field(default=None, description="When the content became true, YYYY-MM-DD.")
    expected_content_hash: str | None = Field(
        default=None, description="The content_hash you read; a newer edit makes the write refuse.")


# 한 런에서 읽기 도구(brief·search·문서)를 합쳐 부를 수 있는 횟수. 두 패키지가 같은 수와 같은 문장을 쓴다.
# 근거: [[decision:vin318-parity-is-judged-on-three-runs-with-a-shared-tool-call-cap-2026-10-07]]
TOOL_CALLS_PER_RUN = 4
VINC_TOOL_NAMES = (BRIEF_NAME, SEARCH_NAME, DOC_NAME)
CAP_MESSAGE = "Vinc tool call cap reached ({limit} per run); answer from what you already have."


def cap_message(limit: int | None) -> str:
    return CAP_MESSAGE.format(limit=limit)


class ToolCallCap:
    """Counts ``vinc_brief``/``vinc_search``/``vinc_doc_get`` calls for ONE run; past ``limit`` the tool
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
