# -*- mode: python -*-
# Vinc 사이드카 단일 바이너리(D43) — 빌드: `pyinstaller packaging/sidecar.spec`
# (레포 루트에서). ladybug 네이티브 라이브러리는 PyInstaller 가 패키지 수집으로
# 동봉한다. 산출물: dist/vinc-sidecar(.exe) — Tauri externalBin 으로 번들.
import pathlib, sys

from PyInstaller.utils.hooks import collect_all

ROOT = pathlib.Path(SPECPATH).parent

# Windows 버전 리소스(§9 오탐 완화, 코드서명 전 중간 단계). 비-Windows 는 무시(None).
VERSION_FILE = str(ROOT / "packaging" / "version_info.txt") if sys.platform == "win32" else None

# ── 임베디드 드라이버 = ladybug 단일(v0.5.3, kuzu 제거) ─────────────────────────
# 빌드 전 CI 가 `INSTALL vector/fts/algo` 로 ~/.lbdb/extension/ 를 채워야 한다
# (store/embedded_backend._bundled_ext_path 가 lbug_extensions/ 에서 로드, G0).
#
# v0.5.1/v0.5.2 회귀(실측 확정, 2026-07-21): ladybug 는 네이티브 서브모듈(`._lbug`
# pybind, `._lbug_capi` ctypes)을 `importlib.import_module("._lbug", __package__)`
# 로 **동적** 로드한다(`ladybug/_backend.py`) — PyInstaller 의 정적 AST 분석이 못 보는
# 패턴이라 `hiddenimports=["ladybug"]` 만으로는 두 서브모듈이 통째로 빠진다. 그 결과
# `import ladybug` 는 성공하지만 `ladybug.Database()` 호출이 `ModuleNotFoundError:
# No module named 'ladybug._lbug_capi'` 로 실패 → `EmbeddedBackend.open` 이 조용히
# `except Exception: return None` 삼켜 스토어가 통째로 안 열림(/health 는 "no
# reachable store"). 이 동적 import 코드는 OS 분기 없이 동일 실행되므로 3-OS 전부
# 동일 영향(§CI 스모크 테스트로 실증). **`collect_all("ladybug")`** 은 pkgutil 로
# 서브모듈을 열거하고 네이티브 바이너리를 파일시스템에서 직접 찾으므로 정적 분석에
# 의존하지 않는다 — 재현·수정 모두 로컬 PyInstaller 빌드로 실측(frozen exe 안에서
# Database open→schema→write→read 성공 확인).
DATAS, BINARIES, DRIVER_HIDDEN = collect_all("ladybug")

# PDF 추출기(D45 입력 어댑터 · api/textextract). 순수 파이썬이라 정적 분석으로도
# 대개 잡히지만, `_codecs/*`·`_crypt_providers/*` 를 조건부·지연 import 하므로
# ladybug 와 같은 규율로 통째 수집한다 — 여기서 아끼면 "빌드는 초록, PDF 만 조용히
# 강등"이 된다. **미설치는 빌드 실패가 아니다**: extra 이므로 없으면 그냥 안 싣고
# 런타임이 pdf_support_missing 으로 강등한다(텍스트 경로는 무영향).
# ⚠ excludes 의 cryptography 제외는 그대로 둔다 — 암호화 PDF 는 지원 범위 밖이고
# (textextract 가 pdf_encrypted 로 강등), 그거 하나 때문에 pyo3 훅 충돌을 되살릴
# 이유가 없다.
try:
    PDF_DATAS, PDF_BINARIES, PDF_HIDDEN = collect_all("pypdf")
except Exception:
    PDF_DATAS, PDF_BINARIES, PDF_HIDDEN = [], [], []
DATAS += PDF_DATAS
BINARIES += PDF_BINARIES
DRIVER_HIDDEN = list(DRIVER_HIDDEN) + list(PDF_HIDDEN)

_extroot = pathlib.Path.home() / ".lbdb" / "extension"
for _ext in ("vector", "fts", "algo"):
    _m = sorted(_extroot.glob(f"*/*/{_ext}/lib{_ext}.lbug_extension"))
    if _m:
        DATAS.append((str(_m[-1]), "lbug_extensions"))
if sum(1 for d in DATAS if d[1] == "lbug_extensions") < 3:
    raise SystemExit(
        "확장 번들 누락(G0). 빌드 전 파이썬으로 "
        "`import ladybug; c=ladybug.Connection(ladybug.Database('t')); "
        "[c.execute(f'INSTALL {e}') for e in ('vector','fts','algo')]` 실행해 "
        "~/.lbdb/extension 를 채우세요.")

a = Analysis(
    [str(ROOT / "packaging" / "run_sidecar.py")],
    pathex=[str(ROOT)],
    datas=DATAS,        # collect_all 데이터 + 확장 오프라인 번들(lbug_extensions/)
    binaries=BINARIES,  # collect_all 이 찾은 네이티브 서브모듈(.pyd/.so/.dylib) — 정적분석 사각 보완
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
    # torch 이하 블록: Kùzu 계보의 선택 통합(torch_geometric·pandas·polars·pyarrow·
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
