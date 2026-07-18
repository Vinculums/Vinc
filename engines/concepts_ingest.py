#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""개인의 학습 노트를 (:Concept) 노드로 적재한다.

도메인은 강제하지 않는다 — ``WORKSPACES/learning/`` 아래의 **모든** 하위 디렉토리를
도메인으로 자동 발견한다(사용자가 무엇을 배우든). 번호형 개념 노트만 대상으로 하며
bridge/index/self-check 등은 제외한다.

관계 추출(F6 보수, 2026-07-19):
- **방향 관계(PREREQUISITE_OF·DERIVES_FROM)는 frontmatter 명시만** —
  ``prerequisite_of: [stem…]`` / ``derives_from: [stem…]`` (스칼라도 허용).
  구 키워드 라인 분류("다음/선행/한계" 등)는 일상어 오탐으로 폐기 — 화살촉(강한
  시각 주장)을 무검증 휴리스틱 위에 세우지 않는다.
- intra-domain ``[[wikilink]]`` 추론은 전부 RELATED_TO 로 강등. 끝점 정렬 정준화로
  A↔B 상호 링크를 1개로 접고, 같은 무순서쌍에 방향 엣지가 있으면 억제한다
  (degree 이중 계상 방지). MERGE 시 DB 역방향 존재도 검사한다(정준화 이전 스토어
  재적재 치유 — api/fragment.py 의 relates_to dedupe 와 동일 정책).
- 잔여: 과거 키워드 추론으로 이미 적재된 방향 엣지는 재적재로 삭제되지 않는다.

사용:
    python concepts_ingest.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

# 스토어 추상화(설계 §4) — 드라이버 취득은 store.backend.open_store 단일 지점.
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store.backend import open_store  # noqa: E402
from store.config import load_config  # noqa: E402

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
                "_fm": fm,   # F6 — 방향 관계의 frontmatter 명시 해석용
            })
    return out


# frontmatter 명시 방향 관계 키(F6) — 값은 같은 도메인의 대상 stem(리스트/스칼라).
_FM_REL_KEYS = {"prerequisite_of": "PREREQUISITE_OF", "derives_from": "DERIVES_FROM"}


def extract_edges(concepts: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """intra-domain 관계 추출(F6) → (src_id, rel_type, tgt_id) best-effort.

    방향 관계는 frontmatter 명시만(_FM_REL_KEYS), [[wikilink]] 추론은 전부
    RELATED_TO(끝점 정렬 정준화 + 방향 존재 쌍 억제). 반환은 정렬 리스트 —
    dry-run/테스트의 결정론."""
    by_stem: dict[tuple[str, str], str] = {(c["domain"], c["stem"]): c["concept_id"] for c in concepts}
    directed: set[tuple[str, str, str]] = set()
    related: set[tuple[str, str]] = set()   # 정준화된 무순서쌍 (min, max)
    for c in concepts:
        fm = c.get("_fm") or {}
        for key, rel in _FM_REL_KEYS.items():
            vals = fm.get(key)
            if vals is None:
                continue
            for stem in (vals if isinstance(vals, list) else [vals]):
                tid = by_stem.get((c["domain"], str(stem).strip()))
                if tid and tid != c["concept_id"]:
                    directed.add((c["concept_id"], rel, tid))
        body = _FM_RE.sub("", c["_text"], count=1)
        for line in body.splitlines():
            for raw in _WIKILINK.findall(line):
                target = raw.split("|")[0].split("#")[0].strip().split("/")[-1]
                key = (c["domain"], target)
                if key not in by_stem or by_stem[key] == c["concept_id"]:
                    continue
                a, b = sorted((c["concept_id"], by_stem[key]))
                related.add((a, b))
    directed_pairs = {tuple(sorted((s, t))) for s, _, t in directed}
    edges = list(directed) + [(a, "RELATED_TO", b)
                              for a, b in related if (a, b) not in directed_pairs]
    return sorted(edges)


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

    store = open_store(load_config())
    if store is None:
        print("[ERROR] 스토어 연결 실패 (backend/자격 확인).", file=sys.stderr)
        sys.exit(1)
    with store.session() as s:
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
        rel_loaded = rel_skipped = 0
        for src, rel, tgt in edges:
            if rel == "RELATED_TO":
                # F6 — DB 역방향 dedupe: 정준화 이전에 적재된 스토어는 (b)->(a) 방향을
                # 이미 들고 있을 수 있다. 존재하면 skip(api/fragment.py 정책 일치).
                res = s.run(
                    """
                    MATCH (b:Concept {concept_id:$tgt})-[:RELATED_TO]->(a:Concept {concept_id:$src})
                    RETURN count(*) AS c
                    """,
                    src=src, tgt=tgt,
                ).single()
                if res and res["c"]:
                    rel_skipped += 1
                    continue
            s.run(
                f"""
                MATCH (a:Concept {{concept_id:$src}}), (b:Concept {{concept_id:$tgt}})
                MERGE (a)-[:{rel}]->(b)
                """,
                src=src, tgt=tgt,
            )
            rel_loaded += 1
    store.close()
    print(f"\n[완료] :Concept {len(concepts)}개 / 관계 {rel_loaded}개 적재"
          + (f" (역방향 중복 skip {rel_skipped}개)" if rel_skipped else ""))


if __name__ == "__main__":
    main()
