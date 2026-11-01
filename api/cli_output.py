"""CLI result formatting and process status."""
from __future__ import annotations

import contextvars
import json
import os
import sys
from typing import Any


_format: contextvars.ContextVar[str | None] = contextvars.ContextVar("vinc_cli_format", default=None)
_command: contextvars.ContextVar[str] = contextvars.ContextVar("vinc_cli_command", default="")

# 목록 필드는 도구별 공개 응답 계약이다. 첫 행을 보고 추측하지 않는다.
LIST_FIELDS = {
    "recall": "rows", "domains": "rows", "search": "rows",
    "doc ls": "docs", "graph": "nodes", "decide": "nodes",
    "node neighbors": "neighbors", "merge candidates": "candidates",
    "merge rejections": "rejections", "diagnose": "components", "edges": "edges",
    "gate check": "paths",
    "shim ls": "shims", "shim watch": "drafts",
}


def configure(fmt: str | None, command: str) -> None:
    _format.set(fmt)
    _command.set(command)


def chosen_format() -> str:
    value = _format.get() or os.environ.get("VINC_FORMAT")
    if value not in (None, "human", "json", "ndjson"):
        raise ValueError("VINC_FORMAT must be human, json, or ndjson")
    return value or ("human" if sys.stdout.isatty() else "json")


def exit_code(result: Any) -> int:
    if not isinstance(result, dict):
        return 0
    if result.get("executed") is False or result.get("isError") is True or result.get("error"):
        return 1
    if result.get("status") == "none":
        return 1
    # 역할 검사는 verdict 가 곧 결과다. pass 가 아니면 실패로 끝내 스크립트가 넘겨주기를 멈춘다.
    if result.get("kind") == "role_check" and result.get("verdict", "pass") != "pass":
        return 1
    return 0


def _rows(rows, *, limit=None) -> list[str]:
    values = rows if isinstance(rows, list) else []
    lines = []
    for row in values[:limit]:
        if isinstance(row, dict):
            fields = ("id", "kind", "title", "label", "domain", "reason", "score")
            lines.append("\t".join(str(row[k]) for k in fields if row.get(k) not in (None, ""))
                         or "; ".join(f"{k}: {v}" for k, v in row.items()))
        else:
            lines.append(str(row))
        if limit is not None and len(lines[-1]) > 240:
            lines[-1] = lines[-1][:237] + "..."
    if limit is not None and len(values) > limit:
        lines.append(f"... {len(values) - limit} more")
    return lines


def _human(result: Any, command: str) -> str:
    if not isinstance(result, dict):
        return str(result)
    if command in ("doc get", "conventions") and result.get("text") is not None:
        return str(result["text"])
    if command == "refs check":
        return f"{result.get('cited', 0)} citations checked; {len(result.get('missing') or [])} missing\nmanifest: {result.get('manifest', '')}"
    if command == "refs refresh":
        return f"Graph citations refreshed.\nmanifest: {result.get('manifest', '')}"
    if command == "whoami":
        lines = [f"{key}: {result[key]}" for key in
                 ("target", "owner", "scope", "key_scope", "spaces_source", "data_file") if key in result]
        for space in result.get("spaces") or []:
            lines.append(f"space: {space.get('id', '')}\t{space.get('name', '')}\t{space.get('role', '')}")
        return "\n".join(lines)
    if command == "node get":
        node = result.get("node") or {}
        return "\n".join([*_rows([node]), *[
            f"{key}: {node[key]}" for key in ("name", "one_liner", "tags", "valid_from", "valid_until")
            if node.get(key)], *[f"{key}: {value}" for key, value in (node.get("props") or {}).items()]])
    if command in ("node graph", "graph", "decide"):
        nodes, edges = result.get("nodes") or [], result.get("edges") or []
        lines = [f"seed: {result.get('seed', result.get('topic', ''))}; depth: {result.get('depth', '')}; "
                 f"nodes: {len(nodes)}; edges: {len(edges)}", *_rows(nodes, limit=20)]
        lines.extend((f"{e.get('source', '')} -> {e.get('target', '')}\t{e.get('type', '')}")[:240]
                     for e in edges[:20])
        if len(edges) > 20:
            lines.append(f"... {len(edges) - 20} more edges")
        return "\n".join(lines)
    if command == "edges":
        # 줄 수는 사용자가 --limit 으로 정한다. 잘린 것은 count 와 함께 말한다.
        edges = result.get("edges") or []
        lines = [f"edges: {len(edges)}; count: {result.get('count', len(edges))}"]
        for edge in edges:
            kind = str(edge.get("type", "")) + (f"/{edge['subtype']}" if edge.get("subtype") else "")
            confidence = [str(edge["confidence"])] if edge.get("confidence") is not None else []
            line = "\t".join([f"{edge.get('from', '')} -> {edge.get('to', '')}", kind, *confidence])
            lines.append(line if len(line) <= 240 else line[:237] + "...")
        if result.get("edges_truncated"):
            lines.append(f"... {result.get('count', 0) - len(edges)} more; raise --limit")
        if result.get("missing_nodes"):
            lines.append("missing: " + ", ".join(str(n) for n in result["missing_nodes"]))
        return "\n".join(lines)
    if command == "role get":
        header = _rows([{key: result.get(key) for key in ("id", "title", "domain")}])
        return "\n".join([*header, str(result.get("rendered") or "")])
    if command == "role export":
        # 사람용 출력은 파일 내용 그대로다(`vinc role export x > SKILL.md`). 팩은 바이너리라 경로만 말한다.
        if result.get("written"):
            return f"wrote {result['written']} ({result.get('bytes', 0)} bytes, contract hash {result.get('hash', '')})"
        if result.get("encoding") == "base64":
            return "This format is binary; pass --out DIR to write the file."
        return str(result.get("content") or "")
    if command == "role check":
        lines = [f"verdict: {result.get('verdict', '')}"]
        for key, label in (("failed_checks", "failed"), ("person_only_actions", "person only")):
            if result.get(key):
                lines.append(f"{label}: " + ", ".join(str(item) for item in result[key]))
        lines.extend("evidence: " + str(item) for item in result.get("evidence_sentences") or [])
        if result.get("note"):
            lines.append("note: " + str(result["note"]))
        return "\n".join(lines)
    if command == "gate check":
        # 결정 문은 사람용 출력이 곧 잡 요약(Markdown)이다. 같은 글을 두 벌 두지 않는다.
        from .cli_gate import markdown
        return markdown(result)
    if command == "path":
        lines = [f"{result.get('a', '')} -> {result.get('b', '')}; "
                 f"found: {result.get('found', False)}; length: {result.get('length')}"]
        for path in (result.get("paths") or [])[:20]:
            if isinstance(path, dict):
                nodes = path.get("nodes") or path.get("ids") or []
            else:
                nodes = path
            lines.append(" -> ".join(str(n.get("id", n.get("title", ""))) if isinstance(n, dict) else str(n)
                                     for n in nodes))
        return "\n".join(lines)
    if command == "ties":
        return "\n".join(_rows(result.get("suggestions")))
    if command == "diagnose" and not isinstance(result.get("components"), list):
        lines = []
        for key, value in result.items():
            if isinstance(value, list):
                lines.append(f"{key}: {len(value)}")
                lines.extend(_rows(value, limit=20))
            elif key not in ("executed", "kind"):
                lines.append(f"{key}: {value}")
        return "\n".join(lines)
    if command == "brief":
        status = result.get("status", "")
        lines = [str(result.get("one_liner") or status)]
        if status == "choose":
            lines.extend(_rows(result.get("candidates")))
        lines.extend(str(row.get("excerpt", "")) for row in result.get("excerpts", []) if isinstance(row, dict))
        if result.get("query"):
            lines.append("query: " + str(result["query"]))
        if result.get("note"):
            lines.append("note: " + str(result["note"]))
        if result.get("unreadable"):
            lines.append("unreadable: " + ", ".join(str(item) for item in result["unreadable"]))
        return "\n".join(line for line in lines if line)
    if command == "search":
        return "\n".join(_rows(result.get("rows")) + _rows(result.get("node_hits")))
    if command == "shim ls":
        lines = [f"shims: {result.get('count', 0)}; today: {result.get('today', '')}"]
        for row in result.get("shims") or []:
            lines.append("\t".join([str(row.get("id", "")), str(row.get("mechanism", "")),
                                    ",".join(row.get("applies_to") or []) or "all families",
                                    "because " + str(row.get("installed_because", "")),
                                    "since " + str(row.get("valid_from", "")),
                                    ",".join(row.get("attached_to") or [])]))
        return "\n".join(lines)
    if command == "shim watch":
        lines = [f"runs: {result.get('runs', 0)} since {result.get('window_start', '')}; "
                 f"families in use: {', '.join(result.get('families_in_use') or []) or 'none'}; "
                 f"shims: {len(result.get('shims') or [])}; drafts: {len(result.get('drafts') or [])}"]
        for draft in result.get("drafts") or []:
            node = (draft.get("nodes") or [{}])[0]
            lines.append(f"{node.get('id', '')}\t{node.get('title', '')}")
        if "written" in result:
            lines.append(f"written: {len(result['written'])}; kept as decided: "
                         + (", ".join(k["id"] for k in result.get("kept") or []) or "none"))
            lines.extend("warning: " + str(w) for w in result.get("warnings") or [])
        else:
            lines.append("nothing written; pass --write to write the drafts")
        return "\n".join(lines)
    field = LIST_FIELDS.get(command)
    rows = result.get(field) if field else None
    if isinstance(rows, list):
        return "\n".join(_rows(rows))
    return json.dumps(result, ensure_ascii=False, indent=2)


def emit(result: Any) -> int:
    fmt = chosen_format()
    code = exit_code(result)
    if code == 0 and fmt in ("human", "ndjson") and _command.get() == "search" and isinstance(result, dict):
        state = result.get("node_semantic") or {}
        if state.get("state") in ("degraded", "partial"):
            print("vinc: node search " + state["state"] +
                  (": " + state["error"] if state.get("error") else "; stale or missing candidates excluded"),
                  file=sys.stderr)
        elif result.get("node_error"):
            print("vinc: node search degraded; use JSON for diagnostics", file=sys.stderr)
        elif state.get("state") == "empty_mirror":
            print("vinc: node semantic search has no stored vectors; lexical matches remain available", file=sys.stderr)
        elif state.get("state") == "model_mismatch":
            print("vinc: node semantic search is off until the node mirror is rebuilt for the current model; "
                  "lexical matches remain available", file=sys.stderr)
        if result.get("vector_note"):
            print("vinc: chunk semantic search has a diagnostic; use JSON for vector_note", file=sys.stderr)
        if fmt == "ndjson" and result.get("node_hits"):
            print("vinc: node matches are available on this first page; use JSON to read node_hits", file=sys.stderr)
    if fmt == "human" and code:
        if _command.get() == "brief" and result.get("status") == "none" and not result.get("error"):
            print("vinc: " + _human(result, "brief"), file=sys.stderr)
            return code
        if _command.get() == "role check" and result.get("verdict") and not result.get("error"):
            print("vinc: " + _human(result, "role check"), file=sys.stderr)
            return code
        error = result.get("error") if isinstance(result, dict) else None
        message = error.get("message", "") if isinstance(error, dict) else error
        print("vinc: " + str(message or "operation failed").removeprefix("vinc: "), file=sys.stderr)
        # decide 가 토픽을 못 찾으면 MCP 도구는 후보와 힌트로 다음 행동을 준다. 사람용
        # 출력이 그 둘을 버려서 읽는 쪽에 다음 수가 없었다(--json 은 전체를 이미 싣는다).
        if _command.get() == "decide" and isinstance(result, dict):
            for cand in result.get("candidates") or []:
                if isinstance(cand, dict):
                    print(f"  candidate: {cand.get('id', '')}\t{cand.get('title', '')}".rstrip(),
                          file=sys.stderr)
            if result.get("hint"):
                print("  hint: " + str(result["hint"]), file=sys.stderr)
        return code
    if fmt == "human":
        rendered = _human(result, _command.get())
        sys.stdout.write(rendered + ("\n" if rendered and not rendered.endswith("\n") else ""))
    elif fmt == "ndjson" and code == 0:
        field = LIST_FIELDS.get(_command.get())
        rows = result.get(field) if field and isinstance(result, dict) else None
        if isinstance(rows, list):
            for row in rows:
                sys.stdout.write(json.dumps(row, ensure_ascii=False) + "\n")
        else:
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    elif fmt == "ndjson":
        # 실패도 한 줄이다 — ndjson 소비자는 줄 단위로만 읽는다.
        sys.stderr.write(json.dumps(result, ensure_ascii=False) + "\n")
    else:
        # `json` 형식(파이프일 때 기본)은 0.9.6 이전 CLI 와 같은 바이트다: 들여쓰기 2 의 객체 하나.
        # 제거 조건: 없음. 출력을 읽는 스크립트의 공개 계약이라 모양을 바꾸려면 새 형식 이름을 연다.
        stream = sys.stderr if code else sys.stdout
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return code


def usage_error(message: str) -> int:
    emit({"kind": "error", "executed": False,
          "error": {"code": "invalid_argument", "message": message}})
    return 2


def internal_error(exc: BaseException, *, debug: bool = False) -> int:
    result = {"kind": "error", "executed": False,
              "error": {"code": "internal", "message": f"{type(exc).__name__}: {exc}"}}
    if debug or os.environ.get("VINC_DEBUG") == "1":
        import traceback
        traceback.print_exception(exc, file=sys.stderr)
    return emit(result)
