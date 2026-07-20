# -*- mode: python -*-
# Vinc 사이드카 단일 바이너리(D43) — 빌드: `pyinstaller packaging/sidecar.spec`
# (레포 루트에서). kuzu 네이티브 라이브러리는 PyInstaller 가 패키지 수집으로
# 동봉한다. 산출물: dist/vinc-sidecar(.exe) — Tauri externalBin 으로 번들.
import pathlib, sys, os

ROOT = pathlib.Path(SPECPATH).parent

# Windows 버전 리소스(§9 오탐 완화, 코드서명 전 중간 단계). 비-Windows 는 무시(None).
VERSION_FILE = str(ROOT / "packaging" / "version_info.txt") if sys.platform == "win32" else None

# ── 임베디드 드라이버 선택(빌드 시) ────────────────────────────────────────────
# 기본 kuzu(D14 — 벡터/FTS 정적 링크, 설치 즉시 오프라인). VINC_SIDECAR_DRIVER=ladybug
# 로 ladybug 변형 빌드: G0 확장(vector/fts/algo)을 datas 로 동봉해 런타임에 명시 경로
# LOAD(오프라인). 빌드 전 CI 가 `INSTALL vector/fts/algo` 로 ~/.lbdb/extension/ 를
# 채워야 한다(store/embedded_backend._bundled_ext_path 가 lbug_extensions/ 에서 로드).
# kuzu(기본) · ladybug(신규 전용) · both(kuzu+ladybug 동봉 — 안전 전환: 기존 kuzu
# 스토어는 kuzu 로, 신규는 ladybug 로 열어 강제 이관 없음. _resolve_driver 가 선택).
DRIVER = os.environ.get("VINC_SIDECAR_DRIVER", "kuzu")
DRIVER_HIDDEN = {"ladybug": ["ladybug"], "both": ["kuzu", "ladybug"]}.get(DRIVER, ["kuzu"])
DATAS = []
if DRIVER in ("ladybug", "both"):
    _extroot = pathlib.Path.home() / ".lbdb" / "extension"
    for _ext in ("vector", "fts", "algo"):
        _m = sorted(_extroot.glob(f"*/*/{_ext}/lib{_ext}.lbug_extension"))
        if _m:
            DATAS.append((str(_m[-1]), "lbug_extensions"))
    if len(DATAS) < 3:
        raise SystemExit(
            "VINC_SIDECAR_DRIVER=ladybug 인데 확장 번들 누락(G0). 빌드 전 파이썬으로 "
            "`import ladybug; c=ladybug.Connection(ladybug.Database('t')); "
            "[c.execute(f'INSTALL {e}') for e in ('vector','fts','algo')]` 실행해 "
            "~/.lbdb/extension 를 채우세요.")

a = Analysis(
    [str(ROOT / "packaging" / "run_sidecar.py")],
    pathex=[str(ROOT)],
    datas=DATAS,   # ladybug 변형: 확장 오프라인 번들(lbug_extensions/) — 기본 kuzu 는 빈 리스트
    # 서버가 함수 내부에서 지연 임포트하는 엔진들 — 정적 분석이 놓치므로 명시
    hiddenimports=[
        "engines.recall_ingest", "engines.concepts_ingest",
        "engines.bridges_ingest", "engines.learner_ingest",
        "engines.pack_ingest", *DRIVER_HIDDEN,
        # MCP 어댑터(P5) — cli 가 지연 임포트하므로 명시
        "vinc_mcp.bridge", "vinc_mcp.stdio", "vinc_mcp.http_server",
        "vinc_mcp.oauth", "vinc_mcp.install",
        # v0.1.9/v0.2.0 — 호출 시점 지연 임포트라 정적 분석이 놓칠 수 있다:
        #   remote      : tools.call() 안에서 target=remote 일 때만
        #   entitlement : cli 의 serve-http 분기 안에서만
        # store/edverify 는 oauth·http_server 가 최상위로 끌지만 명시해 못 박는다.
        "vinc_mcp.remote", "vinc_mcp.entitlement",
        "vinc_mcp.store", "vinc_mcp.edverify",
    ],
    # cryptography/OpenSSL 은 우리 의존이 아님(argon2 도 옵션 lazy) — 시스템
    # dist-packages 의 pyo3 훅 충돌을 피하려고 명시 제외한다.
    # torch 이하 블록: kuzu 의 선택 통합(torch_geometric·pandas·polars·pyarrow·
    # networkx)이 지연 임포트로만 참조하는 스택 — 사이드카는 해당 API 를 쓰지
    # 않으므로 제외(미제외 시 CUDA 동봉으로 바이너리 2.7GB). numpy 는 안전 유지.
    excludes=[
        "tkinter", "unittest", "pydoc", "cryptography", "OpenSSL",
        "torch", "torchvision", "torchaudio", "torch_geometric", "triton",
        "nvidia", "sympy", "onnx", "transformers", "sentence_transformers",
        "tokenizers", "safetensors", "sklearn", "scipy", "pandas", "polars",
        "pyarrow", "networkx", "matplotlib", "PIL", "IPython",
    ],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name="vinc-sidecar",
    console=True,          # 로그 = stdout(셸이 수집)
    upx=False,             # UPX 는 오탐을 오히려 키운다 — 끈 상태 유지(§9)
    version=VERSION_FILE,  # Windows 버전 리소스(메타데이터로 오탐 완화)
)
