# -*- coding: utf-8 -*-
"""문서 한 건을 그래프로 만드는 데 도구 호출이 몇 번 드는지 재는 하네스. 모델은 부르지 않는다.

세션(모델 비용이 드는 부분)은 사람이 돌리고, 이 스크립트는 그 앞뒤만 맡는다:

    seed        고정 문서 열 건을 시험용 사이드카에 넣는다. 앱 가져오기와 같이
                vinc_doc_put 다음 제목 추출(vinc_doc_extract apply:true)까지 한다.
    prompt      세션에 줄 지시문. plain 은 일괄 안내가 없고, builtin 은 내장 프롬프트 본문이다.
    mcp-config  Claude Code 용 MCP 설정 파일. 지정한 체크아웃의 stdio 서버를 시험 사이드카에 붙인다.
    count       세션 기록(JSONL)에서 vinc 도구 호출을 세어 기록별·문서별로 낸다.

문서별 귀속은 마지막으로 본 doc_id(또는 vinc_ingest 엣지의 concept:doc.<id>)를 따른다.
문서 하나를 보기 전의 호출은 (session) 으로 따로 센다.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

try:
    from scripts._root import ROOT
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _root import ROOT

DOCS_DIR = Path(__file__).resolve().with_name("graph_build_docs")
SERVER = "vinceval"

#: 파일 번호 → 주제. 제목 추출 반영은 주제가 없는 문서를 거절하므로 seed 가 함께 싣는다.
TOPICS = {
    "01": "harbor/engineering/reliability",
    "02": "harbor/design/tokens",
    "03": "harbor/engineering/api",
    "04": "harbor/engineering/reliability",
    "05": "harbor/design/accessibility",
    "06": "harbor/product/mobile",
    "07": "harbor/engineering/data",
    "08": "harbor/engineering/frontend",
    "09": "harbor/engineering/tooling",
    "10": "harbor/product/search",
}

#: 비교 기준 지시문. 목표는 내장 프롬프트와 같고, 호출을 어떻게 나눌지는 말하지 않는다.
PLAIN = ("Build a useful knowledge graph in Vinc from the stored document `{doc}`: its key "
         "concepts, the relations between them, and records for the facts it states. Use the "
         "vinc tools. At the end, report what you wrote.")


def fixtures() -> list[dict]:
    """고정 문서 목록. doc_id 는 파일 번호에서 만든다(gbc-01 ...)."""
    out = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), "")
        num = path.name[:2]
        out.append({"doc_id": f"gbc-{num}", "title": title, "topic": TOPICS[num],
                    "text": text, "file": path.name})
    return out


def _call(dispatcher, name: str, args: dict) -> dict:
    reply = dispatcher.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": name, "arguments": args}})
    if "error" in reply:
        raise SystemExit(f"{name}: {reply['error'].get('message')}")
    result = reply["result"]
    text = (result.get("content") or [{}])[0].get("text") or ""
    if result.get("isError"):
        raise SystemExit(f"{name}: {text}")
    return json.loads(text)


def seed(base: str, token: str, owner: str | None) -> list[dict]:
    """호스트가 쓰는 디스패처를 그대로 거쳐 넣는다. 사이드카는 사람이 띄운 것만 쓴다."""
    from vinc_mcp.bridge import Bridge
    from vinc_mcp.rpc import Dispatcher

    dispatcher = Dispatcher(Bridge(base=base, token=token, owner=owner))
    rows = []
    for doc in fixtures():
        put = _call(dispatcher, "vinc_doc_put", {"doc_id": doc["doc_id"], "title": doc["title"],
                                                 "text": doc["text"], "topic": doc["topic"]})
        got = _call(dispatcher, "vinc_doc_extract", {"doc_id": doc["doc_id"], "apply": True})
        if not got.get("executed"):
            raise SystemExit(f"{doc['doc_id']}: {got.get('error')}")
        applied = got.get("applied") or {}
        rows.append({"doc_id": doc["doc_id"], "chunks": put.get("chunks"),
                     "heading_concepts": len(got.get("candidates") or []),
                     "created": applied.get("created"), "matched": applied.get("matched")})
    return rows


def prompt(kind: str, doc: str) -> str:
    if kind == "plain":
        return PLAIN.format(doc=doc)
    from vinc_mcp import builtin_prompts
    got = builtin_prompts.get(builtin_prompts.BUILD_GRAPH, {"doc_ids": doc})
    return got["messages"][0]["content"]["text"]


def mcp_config(base: str, token: str, checkout: str, python: str) -> dict:
    """비교할 체크아웃의 서버를 띄운다. 토큰은 `=` 로 붙인다(대시로 시작해도 옵션이 아니다)."""
    return {"mcpServers": {SERVER: {
        "command": python,
        "args": ["-m", "api.cli", "mcp", "serve", "--base", base, "--token=" + token,
                 "--no-spawn"],
        "env": {"PYTHONPATH": str(Path(checkout).resolve())}}}}


# ── 세션 기록 세기 ────────────────────────────────────────────────────────────

def _blocks(value):
    """기록의 모든 dict 를 훑는다. Claude Code stream-json 과 세션 JSONL 이 모양만 다르다."""
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _blocks(v)
    elif isinstance(value, list):
        for v in value:
            yield from _blocks(v)


def _records(path: Path):
    text = path.read_text(encoding="utf-8")
    try:
        yield json.loads(text)
        return
    except json.JSONDecodeError:
        pass
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def tool_calls(path: Path) -> list[dict]:
    """기록 순서대로 도구 호출 하나씩. 같은 id 는 한 번만 센다(스트림이 같은 블록을 되풀이한다)."""
    calls: dict[str, dict] = {}
    errors: dict[str, bool] = {}
    for record in _records(path):
        for block in _blocks(record):
            kind = block.get("type")
            if kind == "tool_use" and block.get("id") and block.get("name"):
                calls.setdefault(block["id"], block)
            elif kind == "tool_result" and block.get("tool_use_id"):
                errors[block["tool_use_id"]] = bool(block.get("is_error"))
    out = []
    for cid, block in calls.items():
        name = str(block["name"])
        tool = name.rsplit("__", 1)[-1] if name.startswith("mcp__") else name
        out.append({"tool": tool, "input": block.get("input") or {},
                    "is_error": errors.get(cid, False)})
    return out


_DOC_MIRROR = re.compile(r"^concept:doc\.(.+)$")


def _doc_of(call: dict) -> str | None:
    args = call["input"] if isinstance(call["input"], dict) else {}
    if args.get("doc_id"):
        return str(args["doc_id"])
    for edge in args.get("edges") or []:
        for end in (edge.get("from"), edge.get("to")) if isinstance(edge, dict) else ():
            m = _DOC_MIRROR.match(str(end or ""))
            if m:
                return m.group(1)
    return None


def count(paths: list[Path]) -> dict:
    files = []
    per_doc: dict[str, int] = {}
    for path in paths:
        calls = tool_calls(path)
        vinc = [c for c in calls if c["tool"].startswith("vinc_")]
        by_tool: dict[str, int] = {}
        by_doc: dict[str, int] = {}
        current = "(session)"
        for c in vinc:
            current = _doc_of(c) or current
            by_tool[c["tool"]] = by_tool.get(c["tool"], 0) + 1
            by_doc[current] = by_doc.get(current, 0) + 1
        for doc, n in by_doc.items():
            if doc != "(session)":
                per_doc[doc] = per_doc.get(doc, 0) + n
        files.append({"file": str(path), "vinc_calls": len(vinc),
                      "vinc_errors": sum(1 for c in vinc if c["is_error"]),
                      "other_calls": len(calls) - len(vinc),
                      "by_tool": dict(sorted(by_tool.items())), "by_document": by_doc})
    return {"files": files,
            "per_file": _stats([f["vinc_calls"] for f in files]),
            "per_document": _stats(list(per_doc.values()))}


def _stats(values: list[int]) -> dict:
    if not values:
        return {"n": 0}
    return {"n": len(values), "median": statistics.median(values),
            "mean": round(statistics.fmean(values), 2), "min": min(values), "max": max(values)}


def _print_count(result: dict) -> None:
    for f in result["files"]:
        tools = ", ".join(f"{k} {v}" for k, v in f["by_tool"].items())
        docs = ", ".join(f"{k} {v}" for k, v in f["by_document"].items())
        print(f"{f['file']}: vinc calls {f['vinc_calls']} (errors {f['vinc_errors']}), "
              f"other tools {f['other_calls']}")
        print(f"  by tool: {tools or '-'}")
        print(f"  by document: {docs or '-'}")
    for key in ("per_file", "per_document"):
        print(f"{key}: {json.dumps(result[key])}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sd = sub.add_parser("seed", help="store the fixed documents in a test sidecar")
    sd.add_argument("--base", required=True, help="test sidecar URL, e.g. http://127.0.0.1:8799")
    sd.add_argument("--token", required=True, help="that sidecar's session token")
    sd.add_argument("--owner", default=None)
    pr = sub.add_parser("prompt", help="print the instruction for one session")
    pr.add_argument("--kind", choices=("plain", "builtin"), required=True)
    pr.add_argument("--doc", required=True, help="doc id, e.g. gbc-01")
    mc = sub.add_parser("mcp-config", help="write a Claude Code MCP config for one checkout")
    mc.add_argument("--base", required=True)
    mc.add_argument("--token", required=True)
    mc.add_argument("--checkout", default=str(ROOT), help="repository checkout to serve from")
    mc.add_argument("--python", default=sys.executable)
    mc.add_argument("--out", required=True)
    ct = sub.add_parser("count", help="count vinc tool calls in session transcripts")
    ct.add_argument("paths", nargs="+")
    ct.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "seed":
        for row in seed(args.base, args.token, args.owner):
            print(json.dumps(row))
        return 0
    if args.cmd == "prompt":
        print(prompt(args.kind, args.doc))
        return 0
    if args.cmd == "mcp-config":
        out = Path(args.out)
        out.write_text(json.dumps(mcp_config(args.base, args.token, args.checkout, args.python),
                                  indent=2) + "\n", encoding="utf-8")
        print(out)
        return 0
    result = count([Path(p) for p in args.paths])
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_count(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
