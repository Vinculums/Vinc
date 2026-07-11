# -*- mode: python -*-
# Vinc 사이드카 단일 바이너리(D43) — 빌드: `pyinstaller packaging/sidecar.spec`
# (레포 루트에서). kuzu 네이티브 라이브러리는 PyInstaller 가 패키지 수집으로
# 동봉한다. 산출물: dist/vinc-sidecar(.exe) — Tauri externalBin 으로 번들.
import pathlib

ROOT = pathlib.Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "packaging" / "run_sidecar.py")],
    pathex=[str(ROOT)],
    # 서버가 함수 내부에서 지연 임포트하는 엔진들 — 정적 분석이 놓치므로 명시
    hiddenimports=[
        "engines.recall_ingest", "engines.concepts_ingest",
        "engines.bridges_ingest", "engines.learner_ingest",
        "engines.pack_ingest", "kuzu",
        # MCP 어댑터(P5) — cli 가 지연 임포트하므로 명시
        "vinc_mcp.bridge", "vinc_mcp.stdio", "vinc_mcp.http_server",
        "vinc_mcp.oauth", "vinc_mcp.install",
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
    upx=False,
)
