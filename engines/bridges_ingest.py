#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""도메인 간 결합 노트를 (:Bridge) 노드로 적재한다.

도메인을 강제하지 않는다 — 결합의 source 도메인은 ``MEMORY_BRIDGE_SOURCE``(기본
``math``, 하위호환)로 지정하고, 그 도메인 아래 ``*-bridges`` 디렉토리를 모두 자동
발견한다. target 도메인은 디렉토리명(``econ-bridges`` → ``econ``)·frontmatter·
파일명에서 추론한다.

(:Bridge)-[:CROSSES {role}]->(:Domain)
(:Bridge)-[:CONNECTS {role}]->(:Concept)   # frontmatter 의 임의 ``{domain}_inputs`` 가 개념 stem 과 정합
선행: concepts_ingest.py (CONNECTS 해석에 :Concept.stem 필요)

사용:
    python bridges_ingest.py [--dry-run]
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

# hub-root 우선순위: --hub-root > env MEMORY_HUB_ROOT > 레거시 weg9000-hub(존재 시) > CWD.
# (독립 레포로 추출되면서 v1 의 parents[2]/weg9000-hub 가정이 깨진다 — recall/embed 처럼
#  CWD/명시 인자 기반으로 정렬한다. 레거시 경로는 존재 시 하위호환 폴백.)
_LEGACY_HUB = Path(__file__).resolve().parents[2] / "weg9000-hub"


def _default_hub() -> Path:
    env = os.environ.get("MEMORY_HUB_ROOT")
    if env:
        return Path(env)
    if _LEGACY_HUB.is_dir():
        return _LEGACY_HUB
    return Path.cwd()


# source(허브) 도메인 — 강제 아님. 기본 math(하위호환), env 로 임의 도메인 지정 가능.
SOURCE_DOMAIN = os.environ.get("MEMORY_BRIDGE_SOURCE", "math")


def _source_dir(hub: Path) -> Path:
    return hub / "WORKSPACES" / "learning" / SOURCE_DOMAIN


def _discover_bridge_dirs(source_dir: Path):
    """source 도메인 아래 ``*-bridges`` 디렉토리를 모두 발견. 디렉토리명 prefix 가
    target 힌트(``cross-domain``/``cross`` 는 힌트 없음 → frontmatter/파일명 추론)."""
    dirs = []
    if source_dir.is_dir():
        for p in sorted(source_dir.iterdir()):
            if p.is_dir() and p.name.endswith("-bridges"):
                prefix = p.name[: -len("-bridges")]
                target = None if prefix in ("cross-domain", "cross", "domain") else prefix
                dirs.append((p, target))
    return dirs


HUB = _default_hub()
SOURCE_DIR = _source_dir(HUB)
BRIDGE_DIRS = _discover_bridge_dirs(SOURCE_DIR)


def _set_hub(root: str | None) -> None:
    global HUB, SOURCE_DIR, BRIDGE_DIRS
    if root:
        HUB = Path(root)
    HUB = HUB.resolve()
    SOURCE_DIR = _source_dir(HUB)
    BRIDGE_DIRS = _discover_bridge_dirs(SOURCE_DIR)
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


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


def _cross_domain_key(s: str) -> str:
    """target 도메인 추론의 *마지막 폴백 휴리스틱* (디렉토리명·frontmatter 신호가 모두
    없을 때만 사용). 레거시 cross-domain-bridges 파일명 관용어(econ/art/cs-ai)를 인식.
    'economics' 가 'cs' 를 포함하므로 econ·art 를 cs-ai 보다 먼저 검사한다."""
    low = s.lower()
    if "econ" in low or "경제" in s:
        return "econ"
    if "art" in low or "예술" in s or "음악" in s:
        return "art"
    if "cs-ai" in low or "cs/ai" in low or "ai" in low or "cs" in low:
        return "cs-ai"
    return "other"


def _collect_inputs(fm: dict[str, Any]) -> dict[str, list[str]]:
    """frontmatter 의 임의 ``{domain}_inputs: [stem, …]`` 키를 {domain: stems} 로.
    도메인을 열거하지 않는다 — 어떤 도메인이든 ``X_inputs`` 이면 잡는다."""
    inputs: dict[str, list[str]] = {}
    for key, val in fm.items():
        if key.endswith("_inputs") and isinstance(val, list):
            dom = key[: -len("_inputs")]
            if dom:
                inputs[dom] = [str(x) for x in val]
    return inputs


def collect_bridges() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for bdir, fixed_target in BRIDGE_DIRS:
        if not bdir.exists():
            print(f"[WARN] 결합 디렉토리 없음: {bdir}", file=sys.stderr)
            continue
        for md in sorted(bdir.glob("*.md")):
            if md.stem in ("index",) or "README" in md.stem:
                continue
            text = md.read_text(encoding="utf-8")
            fm = _parse_fm(text)
            inputs = _collect_inputs(fm)
            # target 도메인 추론: 디렉토리명 힌트 > frontmatter > 파일명 휴리스틱
            explicit = fm.get("target_domain") or fm.get("cross_domain") or fm.get("domain") or ""
            explicit = explicit if isinstance(explicit, str) else " ".join(explicit)
            target_domain = fixed_target or (explicit.strip() or None) or _cross_domain_key(md.stem)
            out.append({
                "bridge_id": md.stem,
                "title": _h1(text),
                "status": fm.get("status", ""),
                "created": fm.get("created", ""),
                "source_domain": SOURCE_DOMAIN,
                "target_domain": target_domain,
                "inputs": inputs,
                "source_file": str(md.relative_to(HUB)),
            })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="결합 노트 → :Bridge 적재")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--hub-root", default=None,
                    help="hub 루트(기본: env MEMORY_HUB_ROOT 또는 CWD)")
    args = ap.parse_args()
    _set_hub(args.hub_root)
    _load_dotenv()

    bridges = collect_bridges()
    print(f"[INFO] 결합 노드: {len(bridges)}개 (source={SOURCE_DOMAIN})")
    for b in bridges:
        n_in = sum(len(v) for v in b["inputs"].values())
        print(f"       {b['bridge_id']} → {b['source_domain']}↔{b['target_domain']} "
              f"(inputs={n_in} across {sorted(b['inputs'])})")

    if args.dry_run:
        print("\n[DRY-RUN] 적재 없이 종료.")
        return

    store = open_store(load_config())
    if store is None:
        print("[ERROR] 스토어 연결 실패 (backend/자격 확인).", file=sys.stderr)
        sys.exit(1)
    connects = 0
    # F5 — Bridge 소유 스탬프(v2): env MEMORY_DEFAULT_OWNER(기본 tenant:shared,
    # concepts/recall/learner 관례 동형). 재실행이 레거시 ''(v2 마이그레이션 백필)
    # 행의 자연 치유 경로다. ⚠ MCP/fragment 경로와 파이프라인을 섞는 허브는
    # MEMORY_DEFAULT_OWNER 를 MCP owner 와 정렬할 것 — 어긋나면 owner 스코프
    # 조회(atlas L0 결합 등)에서 서로의 브릿지가 갈라져 보인다.
    owner = os.environ.get("MEMORY_DEFAULT_OWNER", "tenant:shared")
    with store.session() as s:
        for b in bridges:
            s.run(
                """
                MERGE (n:Bridge {bridge_id:$bid})
                SET n.title=$title, n.status=$status, n.created=$created,
                    n.source_domain=$sd, n.target_domain=$td, n.source_file=$src,
                    n.owner_id=$owner
                """,
                bid=b["bridge_id"], title=b["title"], status=b["status"], created=b["created"],
                sd=b["source_domain"], td=b["target_domain"], src=b["source_file"],
                owner=owner,
            )
            # CROSSES → Domain (양쪽)
            for dom in (b["source_domain"], b["target_domain"]):
                s.run(
                    """
                    MERGE (d:Domain {name:$dom})
                    WITH d
                    MATCH (n:Bridge {bridge_id:$bid})
                    MERGE (n)-[:CROSSES]->(d)
                    """,
                    dom=dom, bid=b["bridge_id"],
                )
            # CONNECTS → Concept (stem 정합). 임의 도메인의 {domain}_inputs 전수.
            triples = [
                (t, dom, f"{dom}_input")
                for dom, stems in b["inputs"].items()
                for t in stems
            ]
            for token, dom, role in triples:
                res = s.run(
                    """
                    MATCH (n:Bridge {bridge_id:$bid}), (c:Concept {stem:$stem, domain:$dom})
                    MERGE (n)-[r:CONNECTS {role:$role}]->(c)
                    RETURN count(r) AS c
                    """,
                    bid=b["bridge_id"], stem=token, dom=dom, role=role,
                ).single()
                connects += res["c"] if res else 0
    store.close()
    print(f"\n[완료] :Bridge {len(bridges)}개 / CONNECTS(개념 정합) {connects}개 적재")


if __name__ == "__main__":
    main()
