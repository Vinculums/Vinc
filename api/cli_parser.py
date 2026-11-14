# -*- coding: utf-8 -*-
"""`vinc` CLI 의 인자 파서: 루트 파서(:func:`build_parser`)와 동사마다 하위 파서를 붙이는 함수
(`add_<동사>(sub)`). 인자를 해석하고 동사를 실행하는 것은 `api.cli` 다.
"""
from __future__ import annotations

import argparse

from vinc_mcp import __version__

from store import run_state as _run_state


#: `--owner` 의 도움말은 값의 모양을 말한다. 모양을 모르면 사람 이름이나 계정 이름을 넣고
#: 전부 0건을 받아 "스토어가 비었다"로 오진한다.
#:
#: 응답에 실재 owner 목록을 붙이면 **CLI 응답만** 달라져 HTTP==CLI 등가
#: (`tests/core/test_server.py` EquivalenceTests)가 깨진다. 테스트된 불변식을 편의 메시지와
#: 맞바꾸지 않는다.
_OWNER_HELP = ("local owner_id (default tenant:local; linked accounts use user:<uuid>). "
               "Use domains to inspect topics in this store")


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _UsageError(message)


def _add_output_options(parser: argparse.ArgumentParser, *, root: bool = False) -> None:
    default = None if root else argparse.SUPPRESS
    parser.add_argument("--target", choices=("local", "account"), default=default,
                        help="graph target (local or account)")
    parser.add_argument("--space", default=default,
                        help="account graph space (personal or team id)")
    if not parser.prog.endswith(" export"):
        parser.add_argument("--format", choices=("human", "json", "ndjson"),
                            dest="console_format", default=default,
                            help="console result format")
    parser.add_argument("--json", action="store_const", const="json",
                        dest="console_format", default=default,
                        help="alias for --format json")
    parser.add_argument("--debug", action="store_true", default=False if root else argparse.SUPPRESS,
                        help='trace sanitized request routes and unexpected errors on stderr')
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                _add_output_options(child)


def _add_time_options(parser) -> None:
    """`search`·`brief` 의 시간 축 옵션."""
    parser.add_argument("--mode", choices=["general", "current", "historical"], default=None)
    parser.add_argument("--historical", action="store_true", default=None,
                        help="include expired evidence (legacy option)")
    parser.add_argument("--as-of", default=None, help="ISO date or period overlap")
    parser.add_argument("--known-at", default=None,
                        help="initial creation cutoff; does not restore past revisions")


def _add_operation_id(parser) -> None:
    """같은 재시도와 결과 조회를 받는 쓰기 동사의 `--operation-id`."""
    parser.add_argument("--operation-id", default=None, help="UUID for identical retry and result lookup")


def _add_mutation_options(parser) -> None:
    """미리보기를 읽고 그 상태만 적용하는 쓰기 동사(doc rm·delete·edge delete·merge nodes)의 옵션."""
    parser.add_argument("--dry-run", action="store_true", default=None,
                        help="read a complete bounded preview without applying")
    parser.add_argument("--preview-digest", default=None, help="apply only the state in a complete preview")
    _add_operation_id(parser)


def add_recall(sub) -> None:
    r = sub.add_parser("recall", help='read recent episodes in the graph')
    r.add_argument("--query", default="")
    r.add_argument("--owner", default=None, help=_OWNER_HELP)
    r.add_argument("--domain", default=None, help='learning domain (free/user-defined: physics, math, art, ...)')
    r.add_argument("--tenant", default=None, help="tenant_id scope (governance boundary)")
    r.add_argument("--workspace", default=None)
    r.add_argument("--rank", choices=["recency", "preference"], default="recency",
                   help="recency (default) or owner preference-aligned ranking")
    r.add_argument("--tags", default=None, help="comma-separated tags to boost (rank=preference)")
    r.add_argument("--limit", type=int, default=10)


def add_domains(sub) -> None:
    dm = sub.add_parser("domains", help='list topics present in the graph')
    dm.add_argument("--owner", default=None, help='local owner scope (omit for all local owners)')


def add_domain_backfill(sub) -> None:
    # ⚠ **평시에는 부를 일이 없다** — 파생은 부팅 데이터 스텝이 하고 이후는 쓰기 경로가
    # 유지한다([[vinc.spec.dataplane §7]]). 이 명령의 존재 이유는 `--rename` 뿐이다:
    # 평면 도메인을 계층 아래로 옮기는 것은 사람의 결정이라 부팅이 대신하면 안 된다.
    # ⚠ 스토어가 다른 프로세스에 잠겨 있으면 안 된다(사이드카·앱 실행 중 금지).
    dh_ = sub.add_parser("domain-backfill",
                         help="move topics under a parent and re-derive the hierarchy "
                              "(--rename design=vinc/design). Store must not be locked "
                              "by a running sidecar or app.")
    dh_.add_argument("--owner", default=None, help="owner_id scope (omit for every owner)")
    dh_.add_argument("--rename", default=None, metavar="OLD=NEW",
                     action="append",
                     help="also move a topic, column and edges together "
                          "(e.g. --rename design=vinc/design). Repeatable.")
    dh_.add_argument("--dry-run", action="store_true",
                     help="report what would change without writing")


def add_learner(sub) -> None:
    lm = sub.add_parser("learner", help='read local activity patterns')
    lm.add_argument("--owner", required=True, help=_OWNER_HELP)


def add_graph(sub) -> None:
    gr = sub.add_parser("graph", help='read the local graph of concepts and relations')
    gr.add_argument("--owner", required=True, help=_OWNER_HELP)
    gr.add_argument("--domain", default=None, help="narrow to one open/user-defined domain")


def add_pin(sub) -> None:
    # ⚠ `pin`·`tag`·`touch` 셋은 오래도록 `--owner` 가 **없었다**. SEC-1 이전 유물인데, PK
    # 네임스페이싱이 켜지면 그것이 무음 고장이 된다: `pkns.key(None, id)` 는 논리
    # 슬러그를 그대로 돌려주고, 그 값은 물리 키가 실린 행을 못 짚어 SET 이 0행을
    # 맞춘다(오류 없이 아무 일도 안 일어난다). 근거: [[vinc.plan.node-pk-namespacing]] §7
    pn = sub.add_parser("pin", help="pin/unpin an Episode (preference signal)")
    pn.add_argument("--episode", required=True)
    pn.add_argument("--owner", default=None, help='local owner scope (required for namespaced stores)')
    pn.add_argument("--off", action="store_true", help="unpin instead of pin")


def add_tag(sub) -> None:
    tg = sub.add_parser("tag", help="add owner tags to an Episode (preference signal)")
    tg.add_argument("--episode", required=True)
    tg.add_argument("--owner", default=None, help='local owner scope (required for namespaced stores)')
    tg.add_argument("--tags", required=True, help="comma-separated tags")


def add_touch(sub) -> None:
    tc = sub.add_parser("touch", help="record a recall hit (access-frequency signal)")
    tc.add_argument("--episode", required=True)
    tc.add_argument("--owner", default=None, help='local owner scope (required for namespaced stores)')
    tc.add_argument("--now", default=None, help="ISO-8601 timestamp for last_access (optional)")


def add_activate(sub) -> None:
    av = sub.add_parser("activate", help="export owned memory as a portable context pack (+ MCP manifest)")
    av.add_argument("--query", default="")
    av.add_argument("--owner", required=True, help="owner_id to export ('my memory')")
    av.add_argument("--domain", default=None, help='learning domain (free/user-defined): omit to export all domains')
    av.add_argument("--tenant", default=None)
    av.add_argument("--workspace", default=None)
    av.add_argument("--rank", choices=["recency", "preference"], default="preference")
    av.add_argument("--tags", default=None, help="comma-separated tags to boost (rank=preference)")
    av.add_argument("--limit", type=int, default=20)
    av.add_argument("--now", default=None, help="ISO-8601 generated_at stamp (optional)")
    av.add_argument("--no-model", action="store_true", help="exclude the learning method (LearnerPattern) from the pack")
    av.add_argument("--out", default=None, help="dir to write pack.md/pack.json/mcp.json (else emit inline)")


def add_search(sub) -> None:
    s = sub.add_parser("search", help="hybrid vector+BM25 search")
    s.add_argument("--query", required=True)
    s.add_argument("--top-k", type=int, default=5)
    # GM-14 — HTTP `/search` 와 1:1(게이트① 동등성): 커서 페이지네이션 + 요약·상세.
    s.add_argument("--cursor", default=None, help="continue from a previous page's next_cursor")
    s.add_argument("--detail", choices=["summary", "full"], default="summary",
                   help="summary = snippet rows (default); full = whole chunk text")
    _add_time_options(s)


def add_brief(sub) -> None:
    b = sub.add_parser("brief", help="one question in, joined answer material out")
    b.add_argument("--query", required=True)
    b.add_argument("--owner", default=None)
    b.add_argument("--max-excerpts", type=int, default=3)
    _add_time_options(b)


def add_decide(sub) -> None:
    d = sub.add_parser("decide", help="decision-support graph traversal")
    d.add_argument("--topic", required=True)
    d.add_argument("--depth", type=int, default=2)


def add_plan(sub) -> None:
    p = sub.add_parser("plan", help="print a query plan without executing")
    p.add_argument("plankind", choices=["recall", "search", "decide"])
    p.add_argument("--query", default="")
    p.add_argument("--topic", default="")


def add_share(sub) -> None:
    sh = sub.add_parser("share", help='build a share pack: hand your data to someone else')
    sh.add_argument("--owner", required=True, help="origin owner scope (whose data to pack)")
    sh.add_argument("--domain", default=None, help="scope: one dataset partition")
    sh.add_argument("--tags", default=None, help="scope: comma-separated tag filter (any-overlap)")
    sh.add_argument("--seed", default=None, help="scope: pack only the seed's neighborhood (View L2)")
    sh.add_argument("--depth", type=int, default=2, help='seed neighborhood depth 1-4 (default 2)')
    sh.add_argument("--no-records", action="store_true", help="concepts/edges only (skip Episode records)")
    sh.add_argument("--out", required=True, help="pack file to write (JSON)")


def add_import(sub) -> None:
    im = sub.add_parser("import", help="import a share pack under YOUR owner scope (idempotent MERGE)")
    im.add_argument("--pack", required=True, help="pack file (JSON) to import")
    im.add_argument("--owner", required=True, help="recipient owner scope to stamp")


def add_serve(sub) -> None:
    sv = sub.add_parser("serve", help='run the loopback sidecar HTTP server')
    sv.add_argument("--host", default="127.0.0.1", help='bind host (loopback only)')
    sv.add_argument("--port", type=int, default=0, help="bind port (0 = ephemeral)")
    sv.add_argument("--parent-pid", type=int, default=None,
                    help='watch this PID; clean-shutdown when it exits')
    sv.add_argument("--token", default=None, help="session token (default: generate)")
    sv.add_argument("--profile", default=None, help='profile.yml path to include in backups')
    sv.add_argument("--data", default=None,
                    help='database file path (default: MEMORY_DATA_DIR or ~/.vinc/db); not a directory')
    sv.add_argument("--packs", default=None, help='exported packs directory to include in backups')


def add_store_probe(sub) -> None:
    sp = sub.add_parser("store-probe",
                        help='maintenance: open and close a store to check startup')
    sp.add_argument("--data", required=True, help='database file path')


def add_migrate_dim(sub) -> None:
    md = sub.add_parser("migrate-dim",
                        help='maintenance: rebuild vector columns when model dimensions differ')
    md.add_argument("--data", default=None, help='database file path (same default as serve)')


def add_migrate_lex(sub) -> None:
    ml = sub.add_parser("migrate-lex",
                        help='maintenance: migrate the text index on an offline store copy')
    ml.add_argument("--data", required=True, help='database file path of an offline copy')
    ml.add_argument("--batch", type=int, default=200, help='text index backfill batch size')


def add_migrate_body_mentions(sub) -> None:
    mb = sub.add_parser("migrate-body-mentions",
                        help='maintenance: add body span columns to mentions (opt-in schema change)')
    mb.add_argument("--data", required=True, help='database file path')
    mb.add_argument("--approve", action="store_true",
                    help='confirm the schema change; without it nothing is written')


def add_store_rebuild(sub) -> None:
    # 재구축의 진입점은 이 동사다(`python -m store.migrate rebuild` 는 이리로 안내한다). 재큐 정책은
    # api 층(`docstore.switch_embed_model`)의 것이라 store 가 그것을 import 할 수 없다.
    sr = sub.add_parser("store-rebuild",
                        help='maintenance: rebuild a read-only source into an empty destination')
    sr.add_argument("--out", required=True, help='empty destination data directory')
    sr.add_argument("--model", required=True, help='embedding model identifier for the rebuilt store')


def add_backup(sub) -> None:
    bk = sub.add_parser("backup", help='consistent snapshot + packs/profile archive')
    bk.add_argument("--out", required=True, help="archive output dir")
    bk.add_argument("--data", default=None,
                    help="data home to back up (default: MEMORY_DATA_DIR or ~/.vinc/db)")
    bk.add_argument("--packs", default=None, help="exported packs dir to include")
    bk.add_argument("--profile", default=None, help="domain profile.yml to include")


def add_doc(sub) -> None:
    dc = sub.add_parser("doc", help='document SSoT: put/get/rm/ls raw text as chunks')
    dsub = dc.add_subparsers(dest="doccmd", required=True)
    dp = dsub.add_parser("put", help="ingest/update a document from raw text (stdin or --file)")
    dp.add_argument("--owner", default=None,
                    help="local owner_id (account target uses the API key)")
    dp.add_argument("--id", default=None, help='doc_id (omit on create: returned; pass to edit)')
    dp.add_argument("--title", default=None,
                    help="document title (omit to preserve an existing title; pass --title '' to clear)")
    dp.add_argument("--origin", default=None,
                    help="source label for display; not an identity key "
                         "(omit to preserve; pass --origin '' to clear)")
    dp.add_argument("--topic", default=None, help="authored topic path; omit to preserve")
    dp.add_argument("--valid-from", default=None, help="effective start YYYY[-MM[-DD]]; omit to preserve")
    dp.add_argument("--valid-until", default=None, help="effective end (half-open); empty string clears")
    dp.add_argument("--superseded-by", default=None, help="successor document id; omit to preserve")
    dp.add_argument("--expected-content-hash", default=None, help="require the document hash read before editing")
    dp.add_argument("--create-only", action="store_true", default=None, help="refuse an existing document id")
    dp.add_argument("--operation-id", default=None, help="UUID for result lookup and identical retry")
    dp.add_argument("--file", default=None,
                    help="read .md/.txt/.rst/.pdf; on create (no --id, or --create-only) "
                         "title and origin default to the filename, on edit they are kept")
    dp.add_argument("--data", default=None, help="data home (default: MEMORY_DATA_DIR or ~/.vinc/db)")
    dg = dsub.add_parser("get", help="reconstruct a document from its chunks (+hash verify)")
    dg.add_argument("--id", required=True)
    dg.add_argument("--owner", default=None, help='local owner scope')
    dg.add_argument("--data", default=None)
    dr = dsub.add_parser("rm", help="delete a document (chunks + identity)")
    dr.add_argument("--id", required=True)
    dr.add_argument("--owner", default=None, help='local owner scope')
    dr.add_argument("--data", default=None)
    dr.add_argument("--yes", action="store_true", help="confirm deletion")
    _add_mutation_options(dr)
    dl = dsub.add_parser("ls", help="list documents")
    dl.add_argument("--owner", default=None)
    dl.add_argument("--data", default=None)
    dx = dsub.add_parser("extract", help="extract nodes from a canonical document")
    dx.add_argument("id")
    dx.add_argument("--apply", action="store_true")
    dx.add_argument("--owner", default=None)
    _add_operation_id(dx)


def add_whoami(sub) -> None:
    wi = sub.add_parser("whoami", help='show graph identity, target and accessible spaces')
    wi.add_argument("--owner", default=None)


def add_operation(sub) -> None:
    op = sub.add_parser("operation", help="check a mutation receipt without applying again")
    ops = op.add_subparsers(dest="operationcmd", required=True)
    ost = ops.add_parser("status")
    ost.add_argument("operation_id")
    ost.add_argument("--owner", default=None)


def add_conventions(sub) -> None:
    cv = sub.add_parser("conventions", help="read graph conventions")
    cv.add_argument("--lang", default=None)
    cv.add_argument("--owner", default=None)


def add_node(sub) -> None:
    nd = sub.add_parser("node", help="read a graph node")
    nds = nd.add_subparsers(dest="nodecmd", required=True)
    for verb in ("get", "neighbors", "graph"):
        child = nds.add_parser(verb)
        child.add_argument("id")
        child.add_argument("--owner", default=None)
        if verb == "neighbors":
            child.add_argument("--limit", type=int, default=50)
        if verb == "graph":
            child.add_argument("--depth", type=int, default=2)
            child.add_argument("--review-mirror", action="store_true",
                               help="include read-only provenance for one document mirror")


def add_edges(sub) -> None:
    eg = sub.add_parser("edges", help="read directed incident edges with evidence")
    eg.add_argument("id", nargs="?", default=None, help="exact node id or domain:<name>")
    eg.add_argument("--nodes", nargs="+", default=None, metavar="ID",
                    help="read 1..100 exact ids in one call instead of one node")
    eg.add_argument("--type", default=None, help="relation label or authored subtype")
    eg.add_argument("--dir", choices=("in", "out", "both"), default=None,
                    help="edge direction relative to the node (default both)")
    eg.add_argument("--limit", type=int, default=None, help="max edges returned (default 500)")
    eg.add_argument("--owner", default=None)


def add_role(sub) -> None:
    rl = sub.add_parser("role", help="read a role contract or check an output against it")
    rls = rl.add_subparsers(dest="rolecmd", required=True)
    rlg = rls.add_parser("get", help="read one role contract")
    rlg.add_argument("id", help="role slug (agent-maker) or exact concept:agent- id")
    rlg.add_argument("--compact", action="store_true", default=None,
                     help="leave relation evidence sentences out (for a role over the read limit)")
    rlg.add_argument("--owner", default=None)
    rle = rls.add_parser("export", help="export one role as a file for an agent host")
    rle.add_argument("id", help="role slug (agent-maker) or exact concept:agent- id")
    rle.add_argument("--format", default="skill",
                     choices=("skill", "claude", "gpt", "agents-md", "vincpack", "mcp"),
                     help="skill (SKILL.md), claude, gpt, agents-md, vincpack or mcp (default skill)")
    rle.add_argument("--out", default=None, metavar="DIR",
                     help="write the file into DIR instead of printing the result")
    rle.add_argument("--owner", default=None)
    rlc = rls.add_parser("check", help="check one final output against the role's structured checks")
    rlc.add_argument("id", help="role slug (agent-maker) or exact concept:agent- id")
    rlc.add_argument("--output", required=True, metavar="FILE",
                     help="file holding the complete output, or - for stdin")
    rlc.add_argument("--person-only-requests", default=None, metavar="JSON",
                     help='JSON array of {"action": ..., "reason": ...} requests for a person')
    rlc.add_argument("--self-authored", action="store_true", default=None,
                     help="the output is a diff this role wrote; removed lines are not proposals")
    rlc.add_argument("--owner", default=None)


def add_gate(sub) -> None:
    gt = sub.add_parser("gate", help="list recorded decisions that govern the files a change touches")
    gts = gt.add_subparsers(dest="gatecmd", required=True)
    gtc = gts.add_parser("check", help="decision gate over a diff or a list of paths (no model)")
    gtc.add_argument("--diff", default=None, metavar="REF",
                     help="read touched files with git diff --name-only REF...HEAD")
    gtc.add_argument("--paths", nargs="+", default=None, metavar="PATH",
                     help="touched files as repo:path or path, instead of --diff")
    gtc.add_argument("--repo", default=None,
                     help="repository for paths without one (default: the origin remote's name)")
    gtc.add_argument("--member-limit", type=int, default=None, dest="member_limit",
                     help="most items listed per path (default 50)")
    gtc.add_argument("--summary", default=None, metavar="FILE",
                     help="append a Markdown summary to FILE (for example $GITHUB_STEP_SUMMARY)")
    gtc.add_argument("--trace", default=None, metavar="FILE",
                     help="write the full result to FILE; a run record cites it by hash")
    gtc.add_argument("--run-id", default=None, dest="run_id",
                     help="record this verdict as one run of concept:agent-decision-gate "
                          "(account target with a write key only)")
    gtc.add_argument("--task-ref", default=None, dest="task_ref",
                     help="what the run checked, for the run record (for example a PR URL)")
    gtc.add_argument("--host", default=None, help="where the run ran, for the run record")
    gtc.add_argument("--owner", default=None)


def add_events(sub) -> None:
    ev = sub.add_parser("events", help="read change events for roles, for hosts without MCP events")
    evs = ev.add_subparsers(dest="eventscmd", required=True)
    evt = evs.add_parser("tail", help="print one JSON line per event as changes arrive")
    evt.add_argument("--since", default=None, metavar="CURSOR",
                     help="cursor to resume from, or 0 for the beginning (default: from now)")
    evt.add_argument("--role", default=None, help="only events for this role (slug or concept:agent- id)")
    evt.add_argument("--kind", action="append", default=None, dest="kind",
                     choices=("role.reading_changed", "run.citation_superseded", "role.lesson_added",
                              "run.answered"),
                     help="only this event name; repeat for more")
    evt.add_argument("--limit", type=int, default=None, help="changed rows read per page (1..500)")
    evt.add_argument("--interval", type=float, default=30.0,
                     help="seconds between polls when nothing is pending (default 30, at least 5)")
    evt.add_argument("--once", action="store_true",
                     help="read one page, print it as one result and exit")
    evt.add_argument("--cursor-file", default=None, metavar="FILE",
                     help="start from the cursor in this file and write each new cursor to it")
    evt.add_argument("--forward", default=None, metavar="URL",
                     help="POST each event as JSON to this https URL (a Grok Bot routine webhook, "
                          "for example); the bearer token is read from VINC_FORWARD_TOKEN")
    evt.add_argument("--owner", default=None)


def add_run(sub) -> None:
    rn = sub.add_parser("run", help="run an Agent role step by step, or list recorded runs")
    rns = rn.add_subparsers(dest="runcmd", required=True)
    rnl = rns.add_parser("ls", help="list runs, newest first; --cited keeps the ones that cited a claim")
    rnl.add_argument("--cited", default=None, metavar="ID",
                     help="graph id with its type prefix (record:, decision: or doc:); the result "
                          "says whether that claim was retired")
    rnl.add_argument("--role", default=None, help="only runs of this role (slug or concept:agent- id)")
    rnl.add_argument("--limit", type=int, default=None, help="most runs to list (1..200)")
    rnl.add_argument("--owner", default=None)
    rns_start = rns.add_parser("start", help="open a run of a role; prints the first step")
    rns_start.add_argument("role", help="agent slug or concept:agent- id")
    rns_start.add_argument("--task", required=True, help="what this run is for")
    rns_start.add_argument("--subject", default=None, help="what it works on, such as a PR URL")
    rns_start.add_argument("--run-id", default=None, dest="run_id",
                           help="your own id; a retry with the same id returns the same run")
    rns_start.add_argument("--host", default=None,
                           help="the agent host that runs the role, with its version")
    rns_start.add_argument("--owner", default=None)
    rns_sub = rns.add_parser("submit", help="submit one step: output, an answer, or abandon")
    rns_sub.add_argument("run", help="the run id `vinc run start` printed")
    rns_sub.add_argument("--output", default=None, metavar="FILE",
                         help="the step's output; - reads stdin")
    rns_sub.add_argument("--answer", default=None, choices=("approve", "decline"),
                         help="answer the question of a waiting run")
    rns_sub.add_argument("--abandon", action="store_true", help="end the run as abandoned")
    rns_sub.add_argument("--person-only-requests", default=None, metavar="JSON",
                         dest="person_only_requests",
                         help='JSON array of {"action","reason"} you are about to take')
    rns_sub.add_argument("--cited", action="append", default=None, dest="cited",
                         metavar="ID", help="a graph id the output rests on; repeat for more")
    rns_sub.add_argument("--usage", default=None, metavar="JSON",
                         help='JSON like {"tool_calls":3,"tokens_in":900,"cost_usd":0.02}')
    rns_sub.add_argument("--verdict", default=None, choices=_run_state.VERDICTS,
                         help="with the final output: what the work concluded")
    rns_sub.add_argument("--verdict-note", default=None, dest="verdict_note", metavar="TEXT",
                         help="one line; required with blocked: what blocked it")
    rns_sub.add_argument("--owner", default=None)
    rns_show = rns.add_parser("show", help="print one run with its state and question")
    rns_show.add_argument("run", help="episode id or the run id")
    rns_show.add_argument("--owner", default=None)


def add_eval(sub) -> None:
    evc = sub.add_parser("eval", help="read the evaluation cards of an Agent role")
    evcs = evc.add_subparsers(dest="evalcmd", required=True)
    evcc = evcs.add_parser("cards", help="print a role's evaluation cards, newest first")
    evcc.add_argument("role", help="agent slug or concept:agent- id")
    evcc.add_argument("--limit", type=int, default=None, help="most cards to list (1..100)")
    evcc.add_argument("--owner", default=None)


def add_shim(sub) -> None:
    sh = sub.add_parser("shim", help="list harness shims or draft the retirement of ones that stopped firing")
    shs = sh.add_subparsers(dest="shimcmd", required=True)
    shl = shs.add_parser("ls", help="list current shims with their mechanism and reason")
    shl.add_argument("--role", default=None, help="one role slug or concept:agent- id (default every role)")
    shl.add_argument("--model-family", dest="model_family", default=None,
                     help="keep only shims that apply to this model family")
    shw = shs.add_parser("watch", help="draft retirement decisions for shims with no firings in a window")
    shw.add_argument("--window-days", dest="window_days", type=int, required=True,
                     help="read runs from this many days back")
    shw.add_argument("--min-runs", dest="min_runs", type=int, required=True,
                     help="runs needed on every model family before a shim is proposed")
    shw.add_argument("--families", default=None,
                     help="comma separated model families in use (default the families in the window)")
    shw.add_argument("--write", action="store_true",
                     help="write the drafts (needs a full-access key); without it nothing is written")
    for child in (shl, shw):
        child.add_argument("--today", default=None, help="evaluate on this day, YYYY-MM-DD (default today, UTC)")
        child.add_argument("--owner", default=None)


def add_path(sub) -> None:
    pa = sub.add_parser("path", help="find a graph path")
    pa.add_argument("a")
    pa.add_argument("b")
    pa.add_argument("--max-depth", type=int, default=6)
    pa.add_argument("--owner", default=None)


def add_ties(sub) -> None:
    ti = sub.add_parser("ties", help="suggest graph ties")
    ti.add_argument("id")
    ti.add_argument("--limit", type=int, default=5)
    ti.add_argument("--owner", default=None)


def add_diagnose(sub) -> None:
    di = sub.add_parser("diagnose", help="diagnose graph structure")
    di.add_argument("--kind", default="components")
    di.add_argument("--node", default=None)
    di.add_argument("--owner", default=None)


def add_ingest(sub) -> None:
    ig = sub.add_parser("ingest", help="ingest a graph fragment")
    ig.add_argument("--file", required=True)
    ig.add_argument("--owner", default=None)
    ig.add_argument("--review-claims", action="store_true", help="request bounded comparable-claim review")
    _add_operation_id(ig)
    ig.add_argument("--create-only", action="store_true", default=None, help="refuse existing node ids")


def add_delete(sub) -> None:
    de = sub.add_parser("delete", help="delete one node")
    de.add_argument("id")
    de.add_argument("--owner", default=None)
    de.add_argument("--yes", action="store_true")
    _add_mutation_options(de)


def add_edge(sub) -> None:
    ed = sub.add_parser("edge", help="graph edge operations")
    eds = ed.add_subparsers(dest="edgecmd", required=True)
    edel = eds.add_parser("delete")
    edel.add_argument("--from", dest="from_id", required=True)
    edel.add_argument("--to", dest="to_id", required=True)
    edel.add_argument("--type", required=True)
    edel.add_argument("--owner", default=None)
    edel.add_argument("--yes", action="store_true")
    _add_mutation_options(edel)


def add_merge(sub) -> None:
    mg = sub.add_parser("merge", help="merge and review graph nodes")
    mgs = mg.add_subparsers(dest="mergecmd", required=True)
    mgo = mgs.add_parser("nodes")
    mgo.add_argument("keep")
    mgo.add_argument("drop")
    mgo.add_argument("--owner", default=None)
    mgo.add_argument("--yes", action="store_true")
    _add_mutation_options(mgo)
    mgc = mgs.add_parser("candidates")
    mgc.add_argument("--limit", type=int, default=20)
    mgc.add_argument("--owner", default=None)
    mgr = mgs.add_parser("reject")
    mgr.add_argument("a")
    mgr.add_argument("b")
    mgr.add_argument("--note", default="")
    mgr.add_argument("--undo", action="store_true")
    mgr.add_argument("--owner", default=None)
    _add_operation_id(mgr)
    mgx = mgs.add_parser("rejections")
    mgx.add_argument("--owner", default=None)


def add_call(sub) -> None:
    ca = sub.add_parser("call", help='call a registered MCP tool')
    ca.add_argument("tool")
    source = ca.add_mutually_exclusive_group()
    source.add_argument("--args", default=None, help="JSON object")
    source.add_argument("--args-file", default=None, help="JSON file or - for stdin")
    ca.add_argument("--yes", action="store_true")


def add_export(sub) -> None:
    ex = sub.add_parser("export", help="export a local graph as Markdown or CSV")
    ex.add_argument("--format", dest="file_format", choices=("md", "csv"), required=True,
                    help="file format; the root --format controls console output")
    ex.add_argument("--out", required=True, help="output directory")
    ex.add_argument("--owner", required=True, help="owner scope")
    ex.add_argument("--domain", default=None, help="topic and its descendants")
    ex.add_argument("--excel", action="store_true", help="write a UTF-8 BOM to CSV files")


def add_refs(sub) -> None:
    rf = sub.add_parser("refs", help="check or refresh graph citations")
    rfsub = rf.add_subparsers(dest="refscmd", required=True)
    for verb in ("check", "refresh"):
        child = rfsub.add_parser(verb)
        child.add_argument("--manifest", default=None, help="manifest path (default: <root>/scripts/graph-refs.json)")
        child.add_argument("--root", dest="roots", action="append", default=None)


def add_graph_md(sub) -> None:
    gm = sub.add_parser("graph-md", help='assistant writing conventions: build/check GRAPH.md')
    gmsub = gm.add_subparsers(dest="gmcmd", required=True)
    gb = gmsub.add_parser("build", help="write the generated region from the store (prose untouched)")
    gb.add_argument("--owner", default=None, help="owner scope for the domain census")
    gb.add_argument("--file", default=None,
                    help="existing GRAPH.md to refresh in place; omit to print a new one")
    gb.add_argument("--out", default=None, help="write to this path instead of stdout")
    gb.add_argument("--doc", action="store_true",
                    help='read/write the conventions document in the store (its doc_id is derived from the owner) instead of a file: needs --owner')
    gb.add_argument("--lang", choices=("en", "ko"), default=None,
                    help="template language for a new file (default en; refresh keeps the file's own)")
    gb.add_argument("--data", default=None)
    gc = gmsub.add_parser("check", help='check shape rules (0 pass, 1 fix, 2 blocked)')
    gc.add_argument("--file", default=None)
    gc.add_argument("--doc", action="store_true",
                    help="check the conventions document in the store instead of a file")
    gc.add_argument("--owner", default=None)
    gc.add_argument("--no-store", action="store_true",
                    help='shape only: skip the rules that compare against the store')
    gc.add_argument("--data", default=None)


def add_onboard(sub) -> None:
    ob = sub.add_parser("onboard", help="create the local credential (argon2id passphrase)")
    ob.add_argument("--profile", required=True, help="credential file path (auth.json)")
    ob.add_argument("--passphrase", default=None,
                    help='passphrase (omit to read from stdin/getpass: non-echoing)')
    ob.add_argument("--force", action="store_true", help="overwrite an existing credential")


def add_mcp(sub) -> None:
    mc = sub.add_parser("mcp", help="connect an AI assistant to this store (MCP)")
    msub = mc.add_subparsers(dest="mcpcmd", required=True)
    for name, hint in (("serve", "stdio transport for MCP hosts"),
                       ("serve-http", "Streamable HTTP transport for a hosted profile")):
        mp = msub.add_parser(name, help=hint)
        mp.add_argument("--base", default=None, help="sidecar base URL (default 127.0.0.1:8787)")
        mp.add_argument("--token", default=None, help="sidecar session token")
        mp.add_argument("--owner", default=None, help="default owner scope (default tenant:local)")
        mp.add_argument("--no-spawn", action="store_true",
                        help="fail instead of starting a sidecar when none is running")
        if name == "serve-http":
            mp.add_argument("--host", default="127.0.0.1", help='bind host (loopback only)')
            mp.add_argument("--port", type=int, default=8790)
            mp.add_argument("--auth-token", default=None,
                            help="static bearer token (default: env VINC_MCP_TOKEN)")
            mp.add_argument("--oauth", action="store_true",
                            help="enable the OAuth gate (admin key from env VINC_OAUTH_ADMIN_KEY)")
            mp.add_argument("--issuer", default=None,
                            help="public issuer URL for OAuth metadata (e.g. https://mcp.example.com)")
    from vinc_mcp.install import CLIENTS as _MCP_CLIENTS
    for name, hint in (("install", "register this server with a local MCP host"),
                       ("uninstall", "remove this server from a local MCP host")):
        mx = msub.add_parser(name, help=hint)
        mx.add_argument("--client", choices=list(_MCP_CLIENTS), required=True)
        mx.add_argument("--config-path", dest="config_path",
                        default=None,
                        help="override the host config path (tests/portable installs)")
    msub.add_parser("repair",
                    help="point host configs back at this binary after it moved or was renamed")
    msub.add_parser("manual",
                    help="print a paste-ready stdio entry for hosts without an installer")


def build_parser() -> _Parser:
    """루트 파서와 모든 동사·하위 동사를 조립해 돌려준다. 인자를 해석하거나 동사를 실행하지 않는다.

    데스크톱 바이너리의 `CLI_VERBS`(`app/src-tauri/src/main.rs`)는 이 파서의 최상위 동사를 따라야 하고,
    `tests/app/test_cli_verbs_match.py` 가 둘을 대조한다.

    동사의 등록 순서가 곧 `vinc --help` 와 사용 오류 문장의 동사 순서다."""
    ap = _Parser(description='Vinc command line interface')
    ap.add_argument("--version", "-V", action="version", version=f"vinc {__version__}",
                    help="show the installed version and exit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    add_recall(sub)
    add_domains(sub)
    add_domain_backfill(sub)
    add_learner(sub)
    add_graph(sub)
    add_pin(sub)
    add_tag(sub)
    add_touch(sub)
    add_activate(sub)
    add_search(sub)
    add_brief(sub)
    add_decide(sub)
    add_plan(sub)
    # ── 사이드카(M4 · P2) — HTTP 레이어·백업·온보딩 ──────────────────────────
    add_share(sub)
    add_import(sub)
    from . import cli_storage
    cli_storage.add_parser(sub)
    from . import cli_selfhost
    cli_selfhost.add_parser(sub)
    add_serve(sub)
    add_store_probe(sub)
    add_migrate_dim(sub)
    add_migrate_lex(sub)
    add_migrate_body_mentions(sub)
    add_store_rebuild(sub)
    add_backup(sub)
    # ── 문서 정본(Chunk=SSoT, D45) — 파일 없는 doc 단위 CRUD ────────────────
    add_doc(sub)
    # MCP 도구와 같은 결과 봉투를 쓰는 로컬 동사. 오래된 동사는 C6에서 옮긴다.
    add_whoami(sub)
    add_operation(sub)
    add_conventions(sub)
    add_node(sub)
    add_edges(sub)
    add_role(sub)
    add_gate(sub)
    add_events(sub)
    add_run(sub)
    add_eval(sub)
    add_shim(sub)
    add_path(sub)
    add_ties(sub)
    add_diagnose(sub)
    add_ingest(sub)
    add_delete(sub)
    add_edge(sub)
    add_merge(sub)
    add_call(sub)
    add_export(sub)
    add_refs(sub)
    # ── GRAPH.md — 어시스턴트 쓰기 관례 ([[vinc.canon.graph-conventions]]) ──────────────────────
    add_graph_md(sub)
    add_onboard(sub)
    # ── MCP 어댑터(D11 · P5 선행) — stdio/HTTP 전송 + 클라이언트 등록 ────────
    add_mcp(sub)
    _add_output_options(ap, root=True)
    return ap
