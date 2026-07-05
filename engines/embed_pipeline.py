#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""마크다운 파일을 청킹하고 임베딩을 생성해 Neo4j에 적재한다. (Chonkie 기반)

사용:
    python embed_pipeline.py --product <product_name> [--dry-run]
    python embed_pipeline.py --product <product_name> --model local
    python embed_pipeline.py --product <product_name> --force
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# ── Chonkie 임포트 ────────────────────────────────────────────────────────────
# Chonkie is loaded lazily (see _load_chonkie) so this module imports cleanly
# without the heavy chunking/embedding stack installed — engines stay testable
# and the actual dependency is only required when a pipeline run executes.
RecursiveChunker = None  # type: ignore[assignment]
EmbeddingsRefinery = None  # type: ignore[assignment]
CHONKIE_VERSION = "unknown"


def _load_chonkie() -> None:
    """Import chonkie on demand, binding module globals. Exits if missing."""
    global RecursiveChunker, EmbeddingsRefinery, CHONKIE_VERSION
    if RecursiveChunker is not None:
        return
    try:
        import chonkie as _chonkie_pkg
        from chonkie import RecursiveChunker as _RC, EmbeddingsRefinery as _ER
    except ImportError:
        print("[chonkie 미설치]")
        print('pip install "chonkie[tiktoken,voyageai,st]"')
        sys.exit(1)
    RecursiveChunker = _RC
    EmbeddingsRefinery = _ER
    CHONKIE_VERSION = getattr(_chonkie_pkg, "__version__", "unknown")


# ── 상수 ─────────────────────────────────────────────────────────────────────
PARQUET_FILENAME = "chunks.parquet"
# 임베딩 차원은 모델별로 다르다: voyage-3=1024, mpnet(local)=768.
# main 에서 args.model 에 따라 EMBEDDING_DIM_BY_MODEL 로 선택한다.
EMBEDDING_DIM_BY_MODEL = {"anthropic": 1024, "local": 768}
EMBEDDING_DIM = 1024  # 기본값(anthropic). main 에서 모델에 맞게 재설정.
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
VOYAGE_MODEL = "voyage-3"
LOCAL_MODEL = "paraphrase-multilingual-mpnet-base-v2"
BATCH_SIZE = 64

_DOC_ID_RE = re.compile(r"[A-Z]\d+-[A-Z](?:-\d+)?")


# ── dotenv 로드 (recall/skill_tracker 와 동일 규약) ───────────────────────────

def _load_dotenv() -> None:
    """weg9000-hub/.env 를 환경변수로 로드한다."""
    for env_path in (Path(".env"), Path("../weg9000-hub/.env"), Path("../../weg9000-hub/.env")):
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
            return


# ── 인수 파싱 ────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="마크다운 청킹 + 임베딩 → Neo4j 적재 파이프라인 (Chonkie 기반)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python embed_pipeline.py --product my-product
  python embed_pipeline.py --product my-product --dry-run
  python embed_pipeline.py --product my-product --model local
  python embed_pipeline.py --product my-product --force \\
      --neo4j-uri bolt://db.internal:7687 \\
      --neo4j-user admin
        """,
    )
    p.add_argument("--product", required=True,
                   help="WORKSPACES/planning/{product}/drafts/ 경로 결정")
    p.add_argument("--neo4j-uri", default="neo4j://127.0.0.1:7687",
                   help="Neo4j URI (기본값: neo4j://127.0.0.1:7687, .env NEO4J_URI 우선)")
    p.add_argument("--neo4j-user", default="neo4j",
                   help="Neo4j 사용자명 (기본값: neo4j)")
    p.add_argument("--neo4j-password", default=None,
                   help="Neo4j 비밀번호. NEO4J_PASSWORD 환경변수 우선 적용")
    p.add_argument("--model", choices=["anthropic", "local"], default="anthropic",
                   help="임베딩 모델 선택: anthropic(voyage-3) 또는 local(sentence-transformers). "
                        "기본값: anthropic")
    p.add_argument("--force", action="store_true",
                   help="체크포인트(chunks.parquet) 무시하고 전체 재처리")
    p.add_argument("--dry-run", action="store_true",
                   help="청킹 결과만 출력하고 임베딩·적재는 수행하지 않음")
    return p


# ── PREFIX 감지 ───────────────────────────────────────────────────────────────

def detect_prefix() -> str | None:
    """CONTEXT/layer-config.md에서 PREFIX(예: G2) 패턴을 추출한다."""
    layer_config = Path("CONTEXT") / "layer-config.md"
    if not layer_config.exists():
        return None
    content = layer_config.read_text(encoding="utf-8")
    m = _DOC_ID_RE.search(content)
    if m:
        return m.group(0).split("-")[0]
    return None


# ── 파일 수집 ────────────────────────────────────────────────────────────────

def collect_files(product: str) -> list[Path]:
    sources: list[Path] = []

    # ── PM_PLANNING 원본 대상 ───────────────────────────────────────────────
    drafts_dir = Path("WORKSPACES/planning") / product / "drafts"
    if drafts_dir.exists():
        sources.extend(sorted(drafts_dir.glob("*.md")))
    else:
        print(f"[WARN] drafts 디렉터리 없음: {drafts_dir}", file=sys.stderr)

    ref_dir = Path("CONTEXT") / "reference-docs"
    if ref_dir.exists():
        sources.extend(sorted(ref_dir.rglob("*.md")))
    else:
        print(f"[WARN] reference-docs 디렉터리 없음: {ref_dir}", file=sys.stderr)

    # ── Memory / Cognitive 대상 (Stage 4 추가) ─────────────────────────────
    recall_dir = Path("CONTEXT") / "memory" / "recall"
    if recall_dir.exists():
        sources.extend(sorted(recall_dir.rglob("*.md")))  # Episodic 검색용
    else:
        print(f"[WARN] recall 디렉터리 없음: {recall_dir}", file=sys.stderr)

    thinking_dir = Path("WORKSPACES") / "thinking"
    if thinking_dir.exists():
        sources.extend(sorted(thinking_dir.rglob("*.md")))  # 사고 구조화 결과

    learning_dir = Path("WORKSPACES") / "learning"
    if learning_dir.exists():
        sources.extend(sorted(learning_dir.rglob("*.md")))  # 학습 노트

    decisions_dir = Path("WORKSPACES") / "decisions"
    if decisions_dir.exists():
        sources.extend(sorted(decisions_dir.rglob("*.md")))  # 의사결정 기록

    return sources


# ── 메타데이터 헬퍼 ───────────────────────────────────────────────────────────

def _extract_doc_id(file_path: Path) -> str:
    m = _DOC_ID_RE.search(file_path.stem)
    return m.group(0) if m else file_path.stem


def _extract_layer(file_path: Path, prefix: str | None) -> str:
    pfx = prefix or "G"
    parts = file_path.parts
    # PM_PLANNING 계층
    if "reference-docs" in parts:
        idx = list(parts).index("reference-docs")
        if idx + 1 < len(parts):
            sub = parts[idx + 1].upper()
            if sub in ("A", "B", "C"):
                return f"{pfx}-{sub}"
    if "drafts" in parts:
        return f"{pfx}-D"
    # Memory / Cognitive 계층 (Stage 4 추가)
    if "recall" in parts:
        return "recall"
    if "thinking" in parts:
        return "cognitive"
    if "learning" in parts:
        return "learning"
    if "decisions" in parts:
        return "decisions"
    return pfx


def _extract_node_type(file_path: Path) -> str:
    """파일 경로에서 node_type 메타데이터를 결정한다 (Stage 4 추가)."""
    parts = file_path.parts
    if "recall" in parts:
        return "episode"
    if "thinking" in parts:
        return "thinking"
    if "learning" in parts:
        return "concept"
    if "decisions" in parts:
        return "procedure"
    return "Chunk"


def _extract_section_title(text: str, file_path: Path) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("## ") or s.startswith("### "):
            return s.lstrip("#").strip()
    return file_path.stem


def _to_list(vec: Any) -> list[float]:
    if hasattr(vec, "tolist"):
        return vec.tolist()
    return list(vec)


def _make_chunk_id(file_path: Path, index: int, text: str) -> str:
    return hashlib.sha256(
        f"{file_path}::{index}::{text[:200]}".encode()
    ).hexdigest()[:16]


# ── 체크포인트 ────────────────────────────────────────────────────────────────

def _parquet_path(product: str) -> Path:
    return Path("WORKSPACES/planning") / product / "graph" / PARQUET_FILENAME


def load_processed_files(product: str) -> set[str]:
    """chunks.parquet에서 이미 처리된 source_file 집합 반환."""
    cp = _parquet_path(product)
    if not cp.exists():
        return set()
    try:
        import pandas as pd
        df = pd.read_parquet(cp)
        return set(df["source_file"].unique()) if "source_file" in df.columns else set()
    except ImportError:
        print("[ERROR] pandas/pyarrow 패키지가 없습니다. pip install pandas pyarrow 를 실행하세요.",
              file=sys.stderr)
        sys.exit(1)


def load_all_records(product: str) -> list[dict]:
    cp = _parquet_path(product)
    if not cp.exists():
        return []
    try:
        import pandas as pd
        return pd.read_parquet(cp).to_dict("records")
    except ImportError:
        print("[ERROR] pandas/pyarrow 패키지가 없습니다. pip install pandas pyarrow 를 실행하세요.",
              file=sys.stderr)
        sys.exit(1)


def save_checkpoint(product: str, records: list[dict]) -> None:
    cp = _parquet_path(product)
    cp.parent.mkdir(parents=True, exist_ok=True)
    try:
        import pandas as pd
        pd.DataFrame(records).to_parquet(cp, index=False)
    except ImportError:
        print("[ERROR] pandas/pyarrow 패키지가 없습니다. pip install pandas pyarrow 를 실행하세요.",
              file=sys.stderr)
        sys.exit(1)
    print(f"[INFO] 체크포인트 저장: {cp} ({len(records)}건)")


# ── 임베딩 모델 팩토리 ────────────────────────────────────────────────────────

def build_embeddings_model(model_type: str):
    if model_type == "anthropic":
        api_key = (
            os.environ.get("VOYAGE_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
        )
        if not api_key:
            print("[임베딩 API 키 없음 — --model local 로 전환합니다]")
            return build_embeddings_model("local")
        try:
            from chonkie.embeddings import VoyageAIEmbeddings
            return VoyageAIEmbeddings(model=VOYAGE_MODEL, api_key=api_key)
        except ImportError:
            print("[ERROR] chonkie voyageai 의존성이 없습니다.", file=sys.stderr)
            print('pip install "chonkie[voyageai]"', file=sys.stderr)
            sys.exit(1)
    else:
        try:
            from chonkie.embeddings import SentenceTransformerEmbeddings
            return SentenceTransformerEmbeddings(model=LOCAL_MODEL)
        except ImportError:
            print("[sentence-transformers 미설치]")
            print('pip install "chonkie[st]"')
            sys.exit(1)


# ── Neo4j ────────────────────────────────────────────────────────────────────

def get_driver(uri: str, user: str, password: str):
    try:
        from neo4j import GraphDatabase
    except ImportError:
        print("[ERROR] neo4j 패키지가 없습니다. pip install neo4j 를 실행하세요.",
              file=sys.stderr)
        sys.exit(1)
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        driver.verify_connectivity()
        return driver
    except Exception as e:
        print(f"[ERROR] Neo4j 연결 실패: {e}", file=sys.stderr)
        print(f"        URI: {uri}, 사용자: {user}", file=sys.stderr)
        sys.exit(1)


def model_dim(model_type: str) -> int:
    """모델별 임베딩 차원. anthropic(voyage-3)=1024 / local(mpnet)=768."""
    return 1024 if model_type == "anthropic" else 768


def ensure_vector_index(session, dims: int = EMBEDDING_DIM) -> None:
    session.run(
        """
        CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS
        FOR (c:Chunk) ON (c.embedding)
        OPTIONS {indexConfig: {
            `vector.dimensions`: $dims,
            `vector.similarity_function`: 'cosine'
        }}
        """,
        dims=dims,
    )


def merge_chunks(session, records: list[dict]) -> int:
    count = 0
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        for rec in batch:
            session.run(
                """
                MERGE (c:Chunk {chunk_id: $chunk_id})
                SET c.source_file   = $source_file,
                    c.doc_id        = $doc_id,
                    c.section_title = $section_title,
                    c.chunk_index   = $chunk_index,
                    c.layer         = $layer,
                    c.char_count    = $char_count,
                    c.token_count   = $token_count,
                    c.text          = $text,
                    c.embedding     = $embedding
                """,
                chunk_id=rec["chunk_id"],
                source_file=rec["source_file"],
                doc_id=rec["doc_id"],
                section_title=rec["section_title"],
                chunk_index=rec["chunk_index"],
                layer=rec["layer"],
                char_count=rec["char_count"],
                token_count=rec["token_count"],
                text=rec["text"],
                embedding=rec["embedding"],
            )
            count += 1
    return count


def link_chunks_to_nodes(session) -> None:
    """doc_id로 Policy/Reference 노드와 [:HAS_CHUNK] 관계 연결."""
    session.run(
        """
        MATCH (c:Chunk)
        WHERE c.doc_id IS NOT NULL
        OPTIONAL MATCH (p:Policy {doc_id: c.doc_id})
        OPTIONAL MATCH (r:Reference {doc_id: c.doc_id})
        WITH c, coalesce(p, r) AS parent
        WHERE parent IS NOT NULL
        MERGE (parent)-[:HAS_CHUNK]->(c)
        """
    )


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    global EMBEDDING_DIM
    _load_dotenv()
    args = build_parser().parse_args()

    # 임베딩 차원을 선택한 모델에 맞게 설정 (벡터 인덱스 차원 정합)
    EMBEDDING_DIM = EMBEDDING_DIM_BY_MODEL.get(args.model, EMBEDDING_DIM)

    # 연결 정보: 환경변수(.env) 우선 → 인수 폴백
    args.neo4j_uri = os.environ.get("NEO4J_URI") or args.neo4j_uri
    args.neo4j_user = os.environ.get("NEO4J_USER") or args.neo4j_user
    password = os.environ.get("NEO4J_PASSWORD") or args.neo4j_password
    if not password and not args.dry_run:
        print("[ERROR] Neo4j 비밀번호가 없습니다.", file=sys.stderr)
        print("        NEO4J_PASSWORD 환경변수 또는 --neo4j-password 인수를 사용하세요.",
              file=sys.stderr)
        sys.exit(1)

    start_time = time.time()
    prefix = detect_prefix()

    # 1. 파일 수집
    files = collect_files(args.product)
    if not files:
        print("[ERROR] 처리할 마크다운 파일이 없습니다.", file=sys.stderr)
        sys.exit(1)
    print(f"[INFO] 수집된 파일: {len(files)}개")

    # 2. 체크포인트 확인 — source_file 기준 스킵
    if args.force:
        processed: set[str] = set()
        cached_records: list[dict] = []
    else:
        processed = load_processed_files(args.product)
        cached_records = load_all_records(args.product)

    new_files = [f for f in files if str(f) not in processed]
    skip_count = len(files) - len(new_files)
    print(f"[INFO] 처리 대상: {len(new_files)}개 / 스킵: {skip_count}개")

    # 3. 청킹 — RecursiveChunker(recipe="markdown")
    # chonkie 1.6.8: recipe 는 from_recipe 로, overlap 인자는 제거됨.
    # tokenizer 는 tiktoken 인코딩명(cl100k_base)을 사용한다.
    _load_chonkie()
    chunker = RecursiveChunker.from_recipe(
        "markdown",
        lang="en",
        tokenizer="cl100k_base",
        chunk_size=CHUNK_SIZE,
    )

    file_chunk_map: list[tuple[Path, list]] = []
    for f in new_files:
        raw = chunker(f.read_text(encoding="utf-8"))
        file_chunk_map.append((f, raw))
        print(f"       {f.name}: {len(raw)}청크")

    total_new = sum(len(c) for _, c in file_chunk_map)
    total_chunks = len(cached_records) + total_new
    print(f"[INFO] 총 청크: {total_chunks}개 (신규: {total_new}개)")

    if args.dry_run:
        print("\n[DRY-RUN] 임베딩·적재 없이 종료합니다.")
        layer_counts: dict[str, int] = {}
        for f, raw in file_chunk_map:
            lyr = _extract_layer(f, prefix)
            layer_counts[lyr] = layer_counts.get(lyr, 0) + len(raw)
        for lyr, cnt in layer_counts.items():
            print(f"[DRY-RUN]   {lyr}: {cnt}청크")
        return

    # 4. 임베딩 — EmbeddingsRefinery
    model_name = VOYAGE_MODEL if args.model == "anthropic" else LOCAL_MODEL
    all_raw_chunks = [c for _, chunks in file_chunk_map for c in chunks]

    if all_raw_chunks:
        print(f"[INFO] 임베딩 생성 중 ({args.model}, {len(all_raw_chunks)}건)...")
        embeddings_model = build_embeddings_model(args.model)
        refinery = EmbeddingsRefinery(embedding_model=embeddings_model)
        embedded = refinery(all_raw_chunks)
        model_name = getattr(embeddings_model, "model", model_name)
    else:
        embedded = []

    # 5. 메타데이터 부착 → 레코드 생성
    new_records: list[dict] = []
    emb_idx = 0
    for f, raw_chunks in file_chunk_map:
        doc_id = _extract_doc_id(f)
        layer = _extract_layer(f, prefix)
        node_type = _extract_node_type(f)
        for i in range(len(raw_chunks)):
            ec = embedded[emb_idx]
            emb_idx += 1
            new_records.append({
                "chunk_id": _make_chunk_id(f, i, ec.text),
                "source_file": str(f),
                "doc_id": doc_id,
                "section_title": _extract_section_title(ec.text, f),
                "chunk_index": i,
                "layer": layer,
                "phase": "",
                "node_type": node_type,
                "char_count": len(ec.text),
                "token_count": getattr(ec, "token_count", 0),
                "text": ec.text,
                "embedding": _to_list(ec.embedding),
            })

    # 6. 체크포인트 저장
    final_records = cached_records + new_records
    save_checkpoint(args.product, final_records)

    # 7. Neo4j 적재
    driver = get_driver(args.neo4j_uri, args.neo4j_user, password)
    neo4j_count = 0
    try:
        with driver.session() as session:
            print(f"[INFO] 벡터 인덱스 확인/생성 (dim={model_dim(args.model)})...")
            ensure_vector_index(session, model_dim(args.model))
            print("[INFO] :Chunk 노드 MERGE 시작...")
            neo4j_count = merge_chunks(session, final_records)
            print("[INFO] [:HAS_CHUNK] 관계 연결 시작...")
            link_chunks_to_nodes(session)
    finally:
        driver.close()

    elapsed = time.time() - start_time
    print(f"\n[완료] 소요 시간: {elapsed:.1f}초")
    print(f"[완료] 처리 파일: {len(new_files)}개 (스킵: {skip_count}개)")
    print(f"[완료] 총 청크: {total_chunks}개")
    print(f"[완료] 임베딩 모델: {model_name}")
    print(f"[완료] Neo4j (:Chunk) 노드: {neo4j_count}개")
    print(f"[완료] 청킹 라이브러리: chonkie {CHONKIE_VERSION}")


if __name__ == "__main__":
    main()
