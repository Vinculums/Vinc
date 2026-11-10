# -*- coding: utf-8 -*-
"""Store configuration — embedded (offline) store + embedding settings.

기본은 임베디드다(설계 §1 — 외부 인프라·API 키 없이 오프라인). 외부 엔진 접속 설정은
아직 없다. 해석 순서: 인자 > 환경변수 > 기본값.
"""
from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass, field

# Embedding dimensions are model-dependent.
# 영어 우선(v0.4, design §8): 기본은 로컬 오프라인 retrieval 모델.
#   local-en = bge-base-en-v1.5 (768), local-en-large = bge-large-en-v1.5 (1024).
#   anthropic (voyage-3, 1024) 은 API 키 필요 — 옵션(F5: 키 없이 기동 가능해야 함).
#   "local" (legacy multilingual mpnet 768) 은 back-compat 만.
#
# ⚠ **이 표의 키는 이름 공간이 둘이다.** 여기 별칭("local-en")은 `MEMORY_EMBEDDING_MODEL`
# 이 주는 것이고 **스토어 벡터 열의 차원**만 정한다. 실제로 벡터를 만드는 주체는
# `vinc_rag/embed/worker.py` 이고 그쪽은 `VINC_EMBED_MODEL` 로 **HF id**
# ("intfloat/multilingual-e5-base")를 받는다. 두 값을 잇는 코드가 **없다** — 오늘
# 둘이 맞는 것은 기본값이 우연히 둘 다 768 이기 때문이다.
# 그래서 **양쪽 철자를 다 싣는다**: 어느 쪽 이름으로 설정해도 차원이 맞는다.
# 근거는 그래프의 record:embed-dim-namespace-split-2026-08-30
# (이중대괄호 인용을 안 쓴 이유는 `vinc_rag/embed/worker.py::prefix` 주석과 같다 —
#  매니페스트 갱신에 원격 그래프 키가 필요하다.)
EMBEDDING_DIM_BY_MODEL = {
    # ── 별칭(MEMORY_EMBEDDING_MODEL · profile.yml) ──────────────────────────
    "local-en": 768,          # bge-base-en-v1.5 — 기본(오프라인, design §8·D10)
    "local-en-large": 1024,   # bge-large-en-v1.5 — 품질 옵션
    "anthropic": 1024,        # voyage-3 — API 키 옵션
    "local": 768,             # legacy multilingual mpnet (back-compat)
    # ── HF id(VINC_EMBED_MODEL · 워커가 실제로 드는 이름) ────────────────────
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-large-en-v1.5": 1024,
    "intfloat/multilingual-e5-base": 768,
    "intfloat/multilingual-e5-large": 1024,
    "BAAI/bge-m3": 1024,
    "dragonkue/BGE-m3-ko": 1024,             # 허브 현행. bge-m3 를 한국어로 더 학습한 판, 폭이 같다
}
DEFAULT_EMBEDDING_MODEL = "local-en"
#: 사람이 고르는 별칭만. HF id 는 위 표에 차원 조회용으로만 실려 있다 —
#: 모델 선택지가 그것까지 받으면 `ST_MODEL_BY_KEY.get(...)` 폴백이
#: 조용히 bge-base-en 을 물어 온다(차원은 맞고 모델만 딴것인 무음 고장).
EMBEDDING_MODEL_ALIASES = ("local-en", "local-en-large", "anthropic", "local")


@dataclass
class StoreConfig:
    # backend selects the GraphStore implementation (store/backend.py):
    #   "embedded" — 인프로세스 LadybugDB(Kùzu 계보) — Vinc 기본·유일 배포 백엔드.
    backend: str = "embedded"
    data_dir: str | None = None          # 임베디드 스토어 데이터 디렉토리(§3-2)
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    # retention tiers (days) for the 3-tier recall memory model
    core_days: int = 30
    archival_days: int = 90
    tenant_id: str | None = None         # 단일 사용자: 상수 tenant:local (§3-3)
    # ── 드라이버 자원 상한(Windows 다중 오픈 대응) ─────────────────────────────
    # LadybugDB(Kùzu 계보)는 **Database 하나마다 가상주소 8 TiB 를 예약**한다
    # (실측: `VirtualAlloc for size 8796093022208 failed`). 리눅스는 오버커밋으로
    # 넘어가지만 Windows 는 커밋 한도가 있어 한 프로세스에서 14~15개까지만 열리고,
    # 그 다음 오픈부터 `MemoryError: bad allocation` 이 난다. 앱은 스토어를 하나만
    # 열어 무관하지만, **테스트 스위트는 수십 개를 연다** — 그래서 로컬 Windows 에서만
    # 10여 건이 무너져 왔다(스코프 격리 실패처럼 보였지만 실제로는 오픈 실패였다).
    # 0/None = 드라이버 기본값(무제한 예약). 값을 주면 그만큼만 예약한다.
    max_db_size: int = 0                 # bytes. VINC_MAX_DB_SIZE
    buffer_pool_mb: int = 0              # MB.    VINC_BUFFER_POOL_MB

    # WAL 이 이만큼 자라면 **엔진이 스스로** 체크포인트한다(쓰기 트랜잭션 끝에서
    # 판정, 활성 트랜잭션이 없을 때). 드라이버 기본은 16MB.
    #
    # 왜 낮추나(2026-08-08 사고). 16MB 기본으로는 WAL 이 11.9MB 까지 자란 채
    # 5시간 반을 버텼고, 그 상태에서 프로세스가 죽자 WAL 이 재생 불가여서
    # **그 5시간 반이 통째로 사라졌다**. 임계값은 곧 **최대 손실 창**이다.
    # 4MB 면 잃는 것이 그만큼으로 묶이고 재생 비용도 줄어든다.
    #
    # ⚠ 공짜가 아니다. 체크포인트는 WAL 을 본 파일에 반영하는 순간이라 **손상
    # 노출이 최대인 연산**이다(SQLite 계열의 알려진 성질: sync 실패가 무결성을
    # 깨는 것은 체크포인트 중뿐이고 커밋 중이면 내구성만 잃는다). 저장 계층이
    # 미덥지 않으면 자주 하는 것이 되레 해롭다 — 그래서 이 값은 **저장 계층을
    # 로컬로 옮기는 것과 한 벌**이고, 그것이 정본 처방이다.
    checkpoint_threshold_mb: int = 4     # MB. VINC_CHECKPOINT_THRESHOLD_MB (0=드라이버 기본)
    extra: dict = field(default_factory=dict)

    @property
    def embedding_dim(self) -> int:
        """이 모델의 벡터 차원. **모르는 모델이면 죽는다.**

        전에는 `.get(model, 768)` 이라 미지 모델이 조용히 768 로 떨어졌다. 그게 왜
        나쁘냐면: 768 로 스토어를 프로비저닝해 놓고 워커가 1024 를 만들면, 그 사실은
        **스토어를 이미 비운 뒤** 첫 쓰기에서야 `Unsupported casting ... Expected: 768,
        Actual: 1024` 로 드러난다(실측). 설정 시점에 조용하고 실행 시점에 파괴적인
        조합이라, 조용한 쪽을 없앤다 — 차원은 **한 번 정하면 되돌리는 데 재구축이
        필요한** 값이라 기본값으로 때울 자리가 아니다.
        기존 설치는 안 깨진다: 위 표가 별칭·HF id 양쪽을 다 실어서, 실제로 쓰이는
        값은 전부 들어 있다. 남는 것은 오타·미지 모델뿐이고 그건 원래 고장이었다.
        """
        try:
            return EMBEDDING_DIM_BY_MODEL[self.embedding_model]
        except KeyError:
            raise ValueError(
                f"unknown embedding model {self.embedding_model!r} — vector dimension "
                f"cannot be guessed. Known: {sorted(EMBEDDING_DIM_BY_MODEL)}"
            ) from None

    def redacted(self) -> dict:
        d = self.__dict__.copy()
        d["embedding_dim"] = self.embedding_dim
        return d


def load_config(overrides: dict | None = None) -> StoreConfig:
    """Build a StoreConfig from env + overrides (overrides win)."""
    overrides = overrides or {}

    def pick(key: str, env: str, default):
        if key in overrides and overrides[key] is not None:
            return overrides[key]
        return os.environ.get(env, default)

    cfg = StoreConfig(
        backend=pick("backend", "MEMORY_STORE_BACKEND", "embedded"),
        data_dir=pick("data_dir", "MEMORY_DATA_DIR", None) or None,
        # 모델 이름의 문은 하나다(2026-09-11). 별칭(MEMORY_EMBEDDING_MODEL)이 없으면 워커가
        # 실제로 드는 HF id(VINC_EMBED_MODEL)를 그대로 쓴다 — 표가 양쪽 철자를 다 실으므로
        # 차원은 어느 쪽으로도 맞다. 이 줄이 없으면 허브는 Dockerfile 이 bge-m3 를 선언해도
        # 스토어 열을 768 로 잡고, 그 어긋남은 스토어를 비운 뒤 첫 쓰기에서야 드러난다.
        # 근거: [[record:embed-dim-namespace-split-2026-08-30]]
        embedding_model=(pick("embedding_model", "MEMORY_EMBEDDING_MODEL", None)
                         or os.environ.get("VINC_EMBED_MODEL")
                         or DEFAULT_EMBEDDING_MODEL),
        tenant_id=overrides.get("tenant_id"),
    )
    cfg.max_db_size = int(pick("max_db_size", "VINC_MAX_DB_SIZE", 0) or 0)
    cfg.buffer_pool_mb = int(pick("buffer_pool_mb", "VINC_BUFFER_POOL_MB", 0) or 0)
    cfg.checkpoint_threshold_mb = int(
        pick("checkpoint_threshold_mb", "VINC_CHECKPOINT_THRESHOLD_MB",
             StoreConfig.checkpoint_threshold_mb) or 0)
    cfg.core_days = int(pick("core_days", "MEMORY_CORE_DAYS", 30))
    cfg.archival_days = int(pick("archival_days", "MEMORY_ARCHIVAL_DAYS", 90))
    return cfg

def vinc_home() -> pathlib.Path:
    """설치형 데이터 홈 `~/.vinc`. 세션 파일·확장·MCP 상태·설정은 늘 여기 산다."""
    return pathlib.Path.home() / ".vinc"


#: 데스크톱 저장 설정(VIN-130). 앱이 쓰고, 사이드카와 로컬 MCP 가 읽는다.
#: `{"mode": "account"|"local", "mode_chosen": bool, "data_path": "<폴더>"}`. 폴더를 고르면 **DB 만** 그리로
#: 간다(`<폴더>/db`). 세션 파일·확장·MCP 상태·packs 는 `~/.vinc` 에 남는다.
LOCAL_CONFIG = "config.json"


def read_local_config(home: pathlib.Path | None = None) -> dict:
    """`~/.vinc/config.json`. 없거나 깨졌으면 빈 dict(기본 경로로 간다)."""
    path = (home or vinc_home()) / LOCAL_CONFIG
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_local_config(updates: dict, home: pathlib.Path | None = None) -> dict:
    """설정을 합쳐 원자적으로 쓴다(tmp → replace). 값이 None 인 키는 지운다."""
    root = home or vinc_home()
    root.mkdir(parents=True, exist_ok=True)
    cur = read_local_config(root)
    for k, v in updates.items():
        if v is None:
            cur.pop(k, None)
        else:
            cur[k] = v
    path = root / LOCAL_CONFIG
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return cur


def configured_data_file(home: pathlib.Path | None = None) -> str | None:
    """설정이 고른 폴더의 DB 파일(`<폴더>/db`). 없거나 상대경로면 None.

    상대경로를 받지 않는 이유: 사이드카의 작업 디렉터리는 띄우는 쪽마다 다르다(셸·
    MCP 호스트·CLI). 같은 설정이 프로세스마다 다른 폴더를 가리키면 안 된다."""
    folder = read_local_config(home).get("data_path")
    if not isinstance(folder, str) or not folder.strip():
        return None
    folder = os.path.expanduser(folder.strip())
    if not os.path.isabs(folder):
        return None
    return os.path.join(folder, "db")


def resolve_data_dir(cli_value: str | None = None, *, create: bool = True) -> str:
    """앱 진입점(cli serve·데스크톱 셸·로컬 MCP)용 데이터 경로 해석(D43, VIN-130):
    ``--data`` > ``MEMORY_DATA_DIR`` > **``~/.vinc/config.json`` 의 data_path** >
    **``~/.vinc/db``**(설치형 기본 홈).

    라이브러리 기본값(load_config 의 data_dir=None)은 그대로 둔다 — 검증
    (verify.py 무 DB 강등)의 순수성을 앱 기본 경로가 오염하지 않게, 홈 폴더
    적용은 진입점만의 결정이다. 표준 하위 구조는 늘 `~/.vinc` 에 만든다:
    ``~/.vinc/{packs/, inbox/, backups/}``.

    ⚠ 고른 폴더를 못 만들면(빠진 외장 디스크 등) **기본 경로로 물러서지 않는다.**
    그러면 다른 스토어가 조용히 열려 빈 그래프가 "데이터가 사라졌다"로 읽힌다. 그 경로를
    그대로 돌려주고, 여는 쪽이 사유를 들고 실패하게 둔다(설정에서 폴더를 다시 고를 수
    있다: `/config` 는 스토어 없이도 답한다)."""
    explicit = cli_value or os.environ.get("MEMORY_DATA_DIR")
    if explicit:
        return explicit
    root = vinc_home()
    if create:
        for sub in ("packs", "inbox", "backups"):
            (root / sub).mkdir(parents=True, exist_ok=True)
    chosen = configured_data_file(root)
    if chosen:
        if create:
            try:
                os.makedirs(os.path.dirname(chosen), exist_ok=True)
            except OSError:
                pass
        return chosen
    return str(root / "db")
