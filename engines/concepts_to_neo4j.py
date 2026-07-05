#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""개인의 학습 노트를 (:Concept) 노드로 적재한다.

도메인은 강제하지 않는다 — ``WORKSPACES/learning/`` 아래의 **모든** 하위 디렉토리를
도메인으로 자동 발견한다(사용자가 무엇을 배우든). 번호형 개념 노트만 대상으로 하며
bridge/index/self-check 등은 제외한다.
intra-domain [[wikilink]] 을 (:Concept)-[:PREREQUISITE_OF|:DERIVES_FROM|:RELATED_TO]->(:Concept) 로 best-effort 연결.

사용:
    python concepts_to_neo4j.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

# ── 경로 해석 ────────────────────────────────────────────────────────────────
# hub-root 우선순위: --hub-root > env MEMORY_HUB_ROOT > 레거시 weg9000-hub(존재 시) > CWD.
# v1 은 __file__ 기준 weg9000-hub 를 가정했으나, 독립 레포로 추출되면서 그 가정이
# 깨진다(parents[2] 가 더 이상 hub 가 아님). recall/embed 처럼 CWD/명시 인자 기반으로
# 정렬해 이식 가능하게 만든다(레거시 경로는 존재 시 하위호환 폴백).
_LEGACY_HUB = Path(__file__).resolve().parents[2] / "weg9000-hub"


def _default_hub() -> Path:
    env = os.environ.get("MEMORY_HUB_ROOT")
    if env:
        return Path(env)
    if _LEGACY_HUB.is_dir():
        return _LEGACY_HUB
    return Path.cwd()


HUB = _default_hub()
LEARNING = HUB / "WORKSPACES" / "learning"


def _set_hub(root: str | None) -> None:
    global HUB, LEARNING
    if root:
        HUB = Path(root)
    HUB = HUB.resolve()
    LEARNING = HUB / "WORKSPACES" / "learning"


# Domains are NOT a fixed list — any directory under learning/ is a domain the
# person chose to study. Only *non-concept* subdirs are excluded (bridges,
# self-checks, …); everything else with numbered notes is fair game.
EXCLUDE_SUBDIRS = {
    "bridges", "physics-bridges", "cross-domain-bridges", "domain-bridges",
    "self-checks", "checkpoints", "index", "_meta", "audit",
}


def discover_domains(learning: Path) -> list[str]:
    """Every learning domain = a subdirectory of ``learning/`` (data-driven)."""
    if not learning.is_dir():
        return []
    return sorted(p.name for p in learning.iterdir()
                  if p.is_dir() and not p.name.startswith((".", "_")))


def _default_owner() -> str:
    return os.environ.get("MEMORY_DEFAULT_OWNER", "tenant:shared")

_NUMBERED = re.compile(r"^\d{2,3}-")
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


def _load_dotenv() -> None:
    env_path = HUB / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv  # type: ignore
            load_dotenv(env_path, override=False)
        except ImportError:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _parse_fm(text: str) -> dict[str, Any]:
    m = _FM_RE.match(text)
    if not m:
        return {}
    fm: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "-")):
            k, _, v = line.partition(":")
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                fm[k.strip()] = [x.strip().strip('"').strip("'")
                                 for x in v[1:-1].split(",") if x.strip()]
            else:
                fm[k.strip()] = v.strip('"').strip("'")
    return fm


def _h1(text: str) -> str:
    for line in _FM_RE.sub("", text, count=1).splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _one_liner(text: str) -> str:
    body = _FM_RE.sub("", text, count=1).splitlines()
    for i, line in enumerate(body):
        s = line.strip()
        if s.startswith("## ") and ("한 줄 핵심" in s or "한줄" in s):
            for nxt in body[i + 1:]:
                if nxt.strip():
                    return nxt.strip().lstrip("*").strip()[:400]
    return ""


def collect_concepts() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    owner = _default_owner()
    domains = discover_domains(LEARNING)
    if not domains:
        print(f"[WARN] 학습 도메인 없음: {LEARNING}", file=sys.stderr)
    for domain in domains:
        ddir = LEARNING / domain
        for md in sorted(ddir.rglob("*.md")):
            parent = md.parent.name
            if parent in EXCLUDE_SUBDIRS:
                continue
            if not _NUMBERED.match(md.stem):
                continue
            if "README" in md.stem or md.stem == "index":
                continue
            text = md.read_text(encoding="utf-8")
            fm = _parse_fm(text)
            stem = md.stem
            out.append({
                "concept_id": f"{domain}-{parent}-{stem}",
                "stem": stem,
                "owner_id": fm.get("owner") or owner,
                "domain": domain,
                "topic": parent,
                "title": _h1(text) or (fm.get("aliases") or [stem])[0],
                "one_liner": _one_liner(text),
                "status": fm.get("status", ""),
                "created": fm.get("created", ""),
                "tags": fm.get("tags", []) if isinstance(fm.get("tags"), list) else [],
                "source_file": str(md.relative_to(HUB)),
                "_text": text,
            })
    return out


def extract_edges(concepts: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """intra-domain [[link]] → (src_id, rel_type, tgt_id) best-effort."""
    by_stem: dict[tuple[str, str], str] = {(c["domain"], c["stem"]): c["concept_id"] for c in concepts}
    edges: list[tuple[str, str, str]] = []
    for c in concepts:
        body = _FM_RE.sub("", c["_text"], count=1)
        for line in body.splitlines():
            for raw in _WIKILINK.findall(line):
                target = raw.split("|")[0].split("#")[0].strip().split("/")[-1]
                key = (c["domain"], target)
                if key not in by_stem or by_stem[key] == c["concept_id"]:
                    continue
                if any(w in line for w in ("다음", "prereq", "선행")):
                    rel = "PREREQUISITE_OF"
                elif any(w in line for w in ("한계", "이전", "한 단계 전")):
                    rel = "DERIVES_FROM"
                else:
                    rel = "RELATED_TO"
                edges.append((c["concept_id"], rel, by_stem[key]))
    # 중복 제거
    return list({e for e in edges})


def get_driver():
    from neo4j import GraphDatabase  # type: ignore
    uri = os.environ.get("NEO4J_URI", "neo4j://127.0.0.1:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    pw = os.environ.get("NEO4J_PASSWORD", "")
    drv = GraphDatabase.driver(uri, auth=(user, pw))
    drv.verify_connectivity()
    return drv


def main() -> None:
    ap = argparse.ArgumentParser(description="학습 노트 → :Concept 적재")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--hub-root", default=None,
                    help="hub 루트(기본: env MEMORY_HUB_ROOT 또는 CWD)")
    args = ap.parse_args()
    _set_hub(args.hub_root)
    _load_dotenv()

    concepts = collect_concepts()
    edges = extract_edges(concepts)
    by_domain: dict[str, int] = {}
    for c in concepts:
        by_domain[c["domain"]] = by_domain.get(c["domain"], 0) + 1

    print(f"[INFO] 개념 노드: {len(concepts)}개")
    for d, n in by_domain.items():
        print(f"       {d}: {n}개")
    print(f"[INFO] intra-domain 관계: {len(edges)}개")

    if args.dry_run:
        print("\n[DRY-RUN] 적재 없이 종료. 샘플:")
        for c in concepts[:8]:
            print(f"  {c['concept_id']} | {c['title'][:40]}")
        rel_counts: dict[str, int] = {}
        for _, r, _ in edges:
            rel_counts[r] = rel_counts.get(r, 0) + 1
        print(f"  관계 타입별: {rel_counts}")
        return

    if not os.environ.get("NEO4J_PASSWORD"):
        print("[ERROR] NEO4J_PASSWORD 없음.", file=sys.stderr); sys.exit(1)

    drv = get_driver()
    with drv.session(database=os.environ.get("NEO4J_DATABASE", "neo4j")) as s:
        for c in concepts:
            s.run(
                """
                MERGE (n:Concept {concept_id: $cid})
                SET n.stem=$stem, n.owner_id=$owner, n.domain=$domain, n.topic=$topic,
                    n.title=$title, n.one_liner=$one_liner, n.status=$status,
                    n.created=$created, n.tags=$tags, n.source_file=$src
                """,
                cid=c["concept_id"], stem=c["stem"], owner=c["owner_id"], domain=c["domain"],
                topic=c["topic"], title=c["title"], one_liner=c["one_liner"], status=c["status"],
                created=c["created"], tags=c["tags"], src=c["source_file"],
            )
        rel_loaded = 0
        for src, rel, tgt in edges:
            s.run(
                f"""
                MATCH (a:Concept {{concept_id:$src}}), (b:Concept {{concept_id:$tgt}})
                MERGE (a)-[:{rel}]->(b)
                """,
                src=src, tgt=tgt,
            )
            rel_loaded += 1
    drv.close()
    print(f"\n[완료] :Concept {len(concepts)}개 / 관계 {rel_loaded}개 적재")


if __name__ == "__main__":
    main()
