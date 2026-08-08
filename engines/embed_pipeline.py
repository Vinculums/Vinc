#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""마크다운 파일을 청킹하고 임베딩을 생성해 임베디드 스토어에 적재한다. (Chonkie 기반)

사용:
    python embed_pipeline.py --product <product_name> [--dry-run]
    python embed_pipeline.py --product <product_name> --model local-en
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

# 스토어 추상화(설계 §4) — 드라이버 취득은 store.backend.open_store 단일 지점.
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store.backend import open_store  # noqa: E402
from store.config import (  # noqa: E402
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDING_DIM_BY_MODEL,
    load_config,
)
from api import docstore as ds  # noqa: E402  — 정본 쓰기 단일 지점(D45 P2)
from engines.concepts_ingest import _default_owner  # noqa: E402  — owner 기본값 공유

# ── Chonkie 임포트 ────────────────────────────────────────────────────────────
# Chonkie is loaded lazily (see _load_chonkie) so this module imports cleanly
# without the heavy chunking/embedding stack installed — engines stay testable
# and the actual dependency is only required when a pipeline run executes.
RecursiveChunker = None  # type: ignore[assignment]
EmbeddingsRefinery = None  # type: ignore[assignment]
CHONKIE_VERSION = "unknown"


class EmbeddingStackMissing(RuntimeError):
    """임베딩 스택(chonkie · sentence-transformers)이 설치돼 있지 않다.

    ⚠ **이 모듈의 라이브러리 경로는 절대 `sys.exit()` 를 부르지 않는다.** 종료를
    결정하는 것은 `main()` 뿐이다. 이유는 2026-07-31 실측 사고다: `SystemExit` 은
    `Exception` 이 아니라 `BaseException` 이라 `except Exception` 을 **전부 통과**한다.
    사이드카의 `POST /doc/reembed` 가 이 함수를 잡 워커 스레드에서 부르는데, 그
    SystemExit 이 핸들러의 `except Exception` 도 잡 워커의 `except Exception` 도
    지나쳐 **워커 스레드를 죽였다**. 워커가 하나뿐이라 그 뒤 모든 쓰기 요청이 큐에
    쌓인 채 영구 무응답이 됐다(읽기는 별도 커넥션이라 멀쩡해서 더 오진하기 쉬웠다).
    라이브러리가 프로세스의 생사를 결정하면 그 라이브러리를 부르는 모든 서버가
    같은 방식으로 죽는다."""


def _load_chonkie() -> None:
    """Import chonkie on demand, binding module globals.

    미설치면 :class:`EmbeddingStackMissing` 을 올린다(종료하지 않는다 — 위 참조)."""
    global RecursiveChunker, EmbeddingsRefinery, CHONKIE_VERSION
    if RecursiveChunker is not None:
        return
    try:
        import chonkie as _chonkie_pkg
        from chonkie import RecursiveChunker as _RC, EmbeddingsRefinery as _ER
    except ImportError as exc:
        raise EmbeddingStackMissing(
            'chonkie 미설치 — pip install "chonkie[tiktoken,voyageai,st]"') from exc
    RecursiveChunker = _RC
    EmbeddingsRefinery = _ER
    CHONKIE_VERSION = getattr(_chonkie_pkg, "__version__", "unknown")


# ── 상수 ─────────────────────────────────────────────────────────────────────
# 임베딩 차원은 모델별로 다르다 — store.config 가 단일 출처(§8, local-en=768 등).
EMBEDDING_DIM = EMBEDDING_DIM_BY_MODEL.get(DEFAULT_EMBEDDING_MODEL, 768)  # main 에서 재설정
CHUNK_SIZE = 500
# ⚠ CHUNK_OVERLAP 은 chonkie 1.6.8 이 인자를 제거해 죽은 상수였다(F1) → 제거.
# parquet 체크포인트(구 증분 원천)도 D45 로 폐지 — 증분은 이제 _DocMeta.content_hash
# (내용 기반)가 판정한다(F5 해소). doc 단위 적재는 api/docstore.put_doc 이 정본.
# 임베딩 모델 어휘는 store.config(§8) 와 동일하다 — 파이프라인이 독자 명칭을 두지
# 않도록 식별자·차원은 config 단일 출처를 따른다. 로컬(오프라인) 모델은 §8 영어 1차:
#   local-en = bge-base-en-v1.5(기본·768) · local-en-large = bge-large-en-v1.5(1024).
#   anthropic = voyage-3(1024, API 키 옵션) · local = legacy 다국어 mpnet(back-compat).
VOYAGE_MODEL = "voyage-3"
LOCAL_EN_MODEL = "BAAI/bge-base-en-v1.5"
LOCAL_EN_LARGE_MODEL = "BAAI/bge-large-en-v1.5"
LOCAL_MODEL = "paraphrase-multilingual-mpnet-base-v2"
# sentence-transformers 로 로드하는 오프라인 모델의 (식별자 → HF 모델명) 매핑.
ST_MODEL_BY_KEY = {
    "local-en": LOCAL_EN_MODEL,
    "local-en-large": LOCAL_EN_LARGE_MODEL,
    "local": LOCAL_MODEL,
}
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
        description="마크다운 청킹 + 임베딩 → 임베디드 스토어 적재 파이프라인 (Chonkie 기반)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python embed_pipeline.py --product my-product
  python embed_pipeline.py --product my-product --dry-run
  python embed_pipeline.py --product my-product --model local-en
  python embed_pipeline.py --product my-product --force
        """,
    )
    p.add_argument("--product", required=True,
                   help="WORKSPACES/planning/{product}/drafts/ 경로 결정")
    p.add_argument("--model", choices=list(EMBEDDING_DIM_BY_MODEL),
                   default=DEFAULT_EMBEDDING_MODEL,
                   help="임베딩 모델(store.config §8 단일 어휘). local-en=bge-base-en(기본·오프라인) · "
                        "local-en-large=bge-large-en · anthropic=voyage-3(API 키 필요) · "
                        f"local=legacy 다국어. 기본값: {DEFAULT_EMBEDDING_MODEL}(오프라인·키 불필요)")
    p.add_argument("--owner", default=None,
                   help="정본 소유자(owner_id) — 미지정 시 MEMORY_DEFAULT_OWNER 또는 tenant:shared")
    p.add_argument("--force", action="store_true",
                   help="내용 해시 증분 무시하고 전체 재적재(doc 단위 재청킹)")
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


# ── 임베딩 모델 팩토리 ────────────────────────────────────────────────────────

def build_embeddings_model(model_type: str):
    if model_type == "anthropic":
        api_key = (
            os.environ.get("VOYAGE_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
        )
        if not api_key:
            # F5·§1: 키 없이도 기동해야 한다 → 오프라인 기본 모델(local-en)로 전환.
            print(f"[임베딩 API 키 없음 — 오프라인 기본 모델(--model {DEFAULT_EMBEDDING_MODEL})로 전환합니다]")
            return build_embeddings_model(DEFAULT_EMBEDDING_MODEL)
        try:
            from chonkie.embeddings import VoyageAIEmbeddings
            return VoyageAIEmbeddings(model=VOYAGE_MODEL, api_key=api_key)
        except ImportError as exc:
            raise EmbeddingStackMissing(
                'chonkie voyageai 의존성 없음 — pip install "chonkie[voyageai]"') from exc
    else:
        st_name = ST_MODEL_BY_KEY.get(model_type, LOCAL_EN_MODEL)
        try:
            from chonkie.embeddings import SentenceTransformerEmbeddings
            return SentenceTransformerEmbeddings(model=st_name)
        except ImportError as exc:
            raise EmbeddingStackMissing(
                'sentence-transformers 미설치 — pip install "chonkie[st]"') from exc


# ── 적재 ─────────────────────────────────────────────────────────────────────

def model_dim(model_type: str) -> int:
    """모델별 임베딩 차원 — store.config 단일 출처(§8). anthropic=1024 / local=768."""
    return EMBEDDING_DIM_BY_MODEL.get(model_type, EMBEDDING_DIM)


def _chonkie_boundaries(raw_chunks: list) -> list[int]:
    """chonkie 청크의 시작 오프셋 목록 → docstore 무손실 경계 후보(P2). chonkie 가
    좋은 검색 분할(토큰 기준)을 주면 그 경계를 쓰고, docstore 가 정규화해 무손실을
    봉합한다. start_index 미노출 빌드면 빈 목록(→ docstore 내장 경계로 폴백)."""
    starts = [getattr(c, "start_index", None) for c in raw_chunks]
    return [int(s) for s in starts if s is not None]


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    global EMBEDDING_DIM
    _load_dotenv()
    args = build_parser().parse_args()

    # 임베딩 차원을 선택한 모델에 맞게 설정 (벡터 인덱스 차원 정합)
    EMBEDDING_DIM = EMBEDDING_DIM_BY_MODEL.get(args.model, EMBEDDING_DIM)

    start_time = time.time()
    prefix = detect_prefix()

    # 1. 파일 수집(파일 = **입력 편의**일 뿐, 정본은 스토어의 Chunk 다 — D45 P2)
    files = collect_files(args.product)
    if not files:
        print("[ERROR] 처리할 마크다운 파일이 없습니다.", file=sys.stderr)
        sys.exit(1)
    print(f"[INFO] 수집된 파일: {len(files)}개")

    owner = args.owner or _default_owner()

    # 2. 스토어 열기 + 증분 판정(내용 해시 — parquet 폐지, F5 해소)
    store = open_store(load_config())
    if store is None:
        print("[ERROR] 스토어 연결 실패 (backend/자격 확인).", file=sys.stderr)
        sys.exit(1)
    store.provision(dim=model_dim(args.model))   # 멱등 — v5 마이그레이션 포함

    # origin = collect_files 가 돌려주는 경로(대개 CWD 상대) — 파일별 안정 식별자.
    plan: list[tuple[Path, str, str, str]] = []   # (file, text, doc_id, layer)
    skip_count = 0
    for f in files:
        text = f.read_text(encoding="utf-8")
        origin = str(f)
        did = ds.doc_id_for_origin(owner, origin)
        if not args.force and ds.doc_hash(store, did) == hashlib.sha256(
                text.encode("utf-8")).hexdigest():
            skip_count += 1
            continue
        plan.append((f, text, did, _extract_layer(f, prefix)))
    print(f"[INFO] 처리 대상: {len(plan)}개 / 스킵(무변경): {skip_count}개")

    if args.dry_run:
        print("\n[DRY-RUN] 임베딩·적재 없이 종료합니다.")
        layer_counts: dict[str, int] = {}
        for _f, _t, _d, lyr in plan:
            layer_counts[lyr] = layer_counts.get(lyr, 0) + 1
        for lyr, cnt in sorted(layer_counts.items()):
            print(f"[DRY-RUN]   {lyr}: {cnt}문서")
        store.close()
        return

    # 3. 임베딩 모델 1회 구성 → put_doc 이 청크별로 호출할 콜러블로 감싼다.
    #    (모델은 한 번만 로드, 임베딩은 doc 최종 청크 기준으로 계산 = 무손실 정본과 정합.)
    model_name = VOYAGE_MODEL if args.model == "anthropic" else ST_MODEL_BY_KEY.get(
        args.model, LOCAL_EN_MODEL)
    embed_fn = None
    if plan:
        # CLI 만 종료를 결정한다 — 라이브러리는 예외를 올릴 뿐이다(EmbeddingStackMissing
        # 독스트링 참조). 여기서 종료 코드로 환원해 종전 CLI 동작을 그대로 유지한다.
        try:
            _load_chonkie()
            embeddings_model = build_embeddings_model(args.model)
        except EmbeddingStackMissing as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            sys.exit(1)
        chunker = RecursiveChunker.from_recipe(
            "markdown", lang="en", tokenizer="cl100k_base", chunk_size=CHUNK_SIZE)
        refinery = EmbeddingsRefinery(embedding_model=embeddings_model)
        model_name = getattr(embeddings_model, "model", model_name)

        def embed_fn(texts: list[str]) -> list[list[float]]:   # noqa: F811
            from chonkie.types import Chunk as _CK
            cks = [_CK(text=t, start_index=0, end_index=len(t), token_count=0) for t in texts]
            return [_to_list(e.embedding) for e in refinery(cks)]

    # 4. doc 단위 적재 — 정본 쓰기는 api/docstore.put_doc 단일 지점.
    stored_docs = 0
    stored_chunks = 0
    try:
        for f, text, did, layer in plan:
            origin = str(f)
            title = _extract_section_title(text, f)
            boundaries = _chonkie_boundaries(chunker(text))   # chunker: if plan 블록에서 정의
            res = ds.put_doc(store, owner=owner, text=text, doc_id=did, title=title,
                             origin=origin, layer=layer, boundaries=boundaries,
                             embed=embed_fn, link=False)
            if res.get("executed") and not res.get("skipped"):
                stored_docs += 1
                stored_chunks += int(res.get("chunks") or 0)
            print(f"       {f.name}: {res.get('chunks')}청크 (doc {did[:8]})")
        # 검색 인덱스 보장(적재 후 1회) + E-G1 :MENTIONS 결정론 링크.
        # ⚠ `build_search_indexes` 가 아니라 `ensure_` 다. `put_doc(embed=...)` 은
        # **벡터만** 드롭하므로 FTS 색인은 살아 있고, build 는 'already exists' 를
        # 흡수하지 않아 **두 번째 실행부터 터진다**(빈 스토어 첫 적재에서만 통했다).
        # 같은 함정이 `/doc/reembed` 에도 있었다 — 2026-08-09 에 둘 다 고쳤다.
        print(f"[INFO] 검색 인덱스 ensure (벡터 dim={model_dim(args.model)} + FTS)...")
        store.ensure_search_indexes(dim=model_dim(args.model))
        try:
            from engines.mentions_link import link_mentions
            stats = link_mentions(store, owner=owner)
            print(f"[INFO] :MENTIONS 링크 {stats['links']}개 {stats['by_method']}")
        except Exception as exc:   # 링크 실패가 적재를 무효화하지 않는다(증강).
            print(f"[WARN] :MENTIONS 링크 스킵: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        store.close()

    elapsed = time.time() - start_time
    print(f"\n[완료] 소요 시간: {elapsed:.1f}초")
    print(f"[완료] 적재 문서: {stored_docs}개 (스킵: {skip_count}개)")
    print(f"[완료] 적재 청크: {stored_chunks}개")
    print(f"[완료] 임베딩 모델: {model_name}")
    print(f"[완료] 청킹 라이브러리: chonkie {CHONKIE_VERSION}")


if __name__ == "__main__":
    main()
