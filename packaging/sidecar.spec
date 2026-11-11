# -*- mode: python -*-
# Vinc 사이드카 단일 바이너리(D43) — 빌드: `pyinstaller packaging/sidecar.spec`
# (레포 루트에서). ladybug 네이티브 라이브러리는 PyInstaller 가 패키지 수집으로
# 동봉한다. 산출물: dist/vinc(.exe) — Tauri externalBin 으로 번들.
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

# 사용자 데이터베이스(Neo4j) 드라이버. 순수 파이썬이고 `store/neo4j_backend.py` 가 함수 안에서만
# import 하므로 pypdf 와 같이 통째 수집한다. release.yml 이 핀으로 깐다. 빌드 기계에 없으면 싣지 않고,
# 그 빌드는 데이터베이스 위치를 "드라이버가 없다"는 사유로 열지 않는다(폴더 스토어는 무영향).
try:
    NEO_DATAS, NEO_BINARIES, NEO_HIDDEN = collect_all("neo4j")
except Exception:
    NEO_DATAS, NEO_BINARIES, NEO_HIDDEN = [], [], []
DATAS += NEO_DATAS
BINARIES += NEO_BINARIES
DRIVER_HIDDEN = list(DRIVER_HIDDEN) + list(NEO_HIDDEN)

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

# ── OpenSSL 3 을 사이드카와 함께 싣는다(ladybug 0.19.0 부터 휠이 안 싣는다) ─────────
# 상류 `19c62d1` 이후 휠은 OpenSSL 3 을 빼고 "사용자가 런타임을 제공하라"고 한다. 그래서
# - Windows: `_lbug.pyd` 와 확장 셋(algo·fts·vector)이 `libssl-3-x64.dll`·
#   `libcrypto-3-x64.dll` 을 **그 이름 그대로** 요구한다. 못 찾으면 네이티브 모듈 import 가
#   실패해 C API 폴백의 "Could not find lbug C API shared library" 로 떨어진다(v0.7.0 의
#   Windows 릴리스 고장이 이것이었다).
# - macOS: `_lbug` 가 `/opt/homebrew/opt/openssl@3/lib/libssl.3.dylib` 를 절대 경로로 링크한다.
#   PyInstaller 가 의존성으로 따라 싣지만, 빌드 머신에 없으면 조용히 빠지므로 여기서 확인한다.
# - Linux: 시스템 `libssl.so.3` 를 PyInstaller 가 의존성 분석으로 싣는다(여기서 할 일 없음).
# 못 찾으면 빌드를 세운다 — 빠진 채 나가면 사용자 기기에서만 스토어가 안 열린다.
# 경로는 `VINC_OPENSSL_DIR` 로 덮을 수 있다.
# 근거: [[vinc.ops.ladybug-upgrade-review-2026-09-24]] §2.4
def _openssl3_binaries():
    import os
    if sys.platform == "win32":
        names = ("libssl-3-x64.dll", "libcrypto-3-x64.dll")
        dirs = [os.environ.get("VINC_OPENSSL_DIR", ""),
                r"C:\Program Files\OpenSSL\bin",        # 러너 이미지의 OpenSSL(ShiningLight)
                r"C:\Program Files\OpenSSL-Win64\bin",
                r"C:\Program Files\Git\mingw64\bin"]    # Git for Windows 도 같은 이름으로 싣는다
    elif sys.platform == "darwin":
        names = ("libssl.3.dylib", "libcrypto.3.dylib")
        dirs = [os.environ.get("VINC_OPENSSL_DIR", ""),
                "/opt/homebrew/opt/openssl@3/lib", "/usr/local/opt/openssl@3/lib"]
    else:
        return []
    for d in dirs:
        if d and all((pathlib.Path(d) / n).is_file() for n in names):
            return [(str(pathlib.Path(d) / n), ".") for n in names]
    raise SystemExit(
        f"OpenSSL 3 을 찾지 못했다({', '.join(names)}). ladybug 0.19.0 부터 휠이 싣지 않으므로 "
        f"사이드카가 함께 실어야 한다 — 설치하거나 VINC_OPENSSL_DIR 로 위치를 알려 주세요. "
        f"찾아본 곳: {[d for d in dirs if d]}")


BINARIES += _openssl3_binaries()

a = Analysis(
    [str(ROOT / "packaging" / "run_sidecar.py")],
    pathex=[str(ROOT)],
    datas=DATAS,        # collect_all 데이터 + 확장 오프라인 번들(lbug_extensions/)
    binaries=BINARIES,  # collect_all 이 찾은 네이티브 서브모듈(.pyd/.so/.dylib) — 정적분석 사각 보완
    # 서버가 함수 내부에서 지연 임포트하는 엔진들 — 정적 분석이 놓치므로 명시
    hiddenimports=[
        "engines.recall_ingest", *DRIVER_HIDDEN,
        # MCP 어댑터(P5) — cli 가 지연 임포트하므로 명시
        "vinc_mcp.bridge", "vinc_mcp.stdio", "vinc_mcp.http_server",
        "vinc_mcp.oauth", "vinc_mcp.install",
        # v0.1.9/v0.2.0 — 호출 시점 지연 임포트라 정적 분석이 놓칠 수 있다:
        #   remote      : api/refs.py(인용 대조)를 거쳐서만
        #   entitlement : cli 의 serve-http 분기 안에서만
        # store/edverify 는 oauth·http_server 가 최상위로 끌지만 명시해 못 박는다.
        "vinc_mcp.remote", "vinc_mcp.hubhttp", "vinc_mcp.entitlement",
        "vinc_mcp.store", "vinc_mcp.edverify",
        # W2 — 역할 렌더러 + prompts. rpc 가 최상위로 끄므로 정적 분석이 잡지만,
        # store/edverify 와 같은 이유로 명시해 못 박는다. 이 모듈이 빠지면 rpc 임포트
        # 자체가 죽어 MCP 표면이 통째로 사라진다. 렌더러가 `scripts/` 가 아니라 여기
        # 있는 이유이기도 하다 — 동결 바이너리는 `scripts/` 를 싣지 않는다.
        "vinc_mcp.roles",
        # VIN-243 — 검색 코어를 api/query.py 에서 옮겼다. api.query 가 최상위로 끌어
        # 정적 분석이 잡지만, 빠지면 검색이 통째로 죽으므로 위와 같은 이유로 못 박는다.
        "store.querybase", "vinc_rag", "vinc_rag.retrieval",
        # 저장 위치가 데이터베이스일 때만 `store.backend.open_store` 가 지연 import 한다.
        "store.location", "store.neo4j_backend", "api.cli_storage", "api.cli_selfhost",
    ],
    # cryptography/OpenSSL 은 우리 의존이 아님(argon2 도 옵션 lazy) — 시스템
    # dist-packages 의 pyo3 훅 충돌을 피하려고 명시 제외한다. ⚠ 여기 "OpenSSL" 은
    # pyOpenSSL **파이썬 패키지**다. 엔진이 링크하는 OpenSSL 3 네이티브 라이브러리는
    # 위 `_openssl3_binaries()` 가 binaries 로 싣는다 — 둘을 헷갈려 빼지 말 것.
    # torch 이하 블록: Kùzu 계보의 선택 통합(torch_geometric·pandas·polars·pyarrow·
    # networkx)이 지연 임포트로만 참조하는 스택 — 사이드카는 해당 API 를 쓰지
    # 않으므로 제외(미제외 시 CUDA 동봉으로 바이너리 2.7GB). numpy 는 안전 유지.
    excludes=[
        "tkinter", "unittest", "pydoc", "cryptography", "OpenSSL",
        "torch", "torchvision", "torchaudio", "torch_geometric", "triton",
        "nvidia", "sympy", "onnx", "transformers", "sentence_transformers",
        "tokenizers", "safetensors", "sklearn", "scipy", "pandas", "polars",
        "pyarrow", "networkx", "matplotlib", "PIL", "IPython",
        # 임베딩 워커·인코더·리랭커가 `vinc_rag/` 로 들어왔다(VIN-244). 사이드카는 `vinc_rag`
        # 를 싣지만 임베딩 스택은 싣지 않는다 — 누가 `vinc_rag.embed` 를 import 해도 빌드
        # 기계에 깔린 onnxruntime 이 따라 들어오지 않게 여기서 막는다.
        "onnxruntime", "huggingface_hub",
    ],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name="vinc",
    console=True,          # 로그 = stdout(셸이 수집)
    upx=False,             # UPX 는 오탐을 오히려 키운다 — 끈 상태 유지(§9)
    version=VERSION_FILE,  # Windows 버전 리소스(메타데이터로 오탐 완화)
)
