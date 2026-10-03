# -*- coding: utf-8 -*-
"""G0 를 돌릴 venv 를 락에서 만든다 — 엔진 판과 질의 인코더 런타임을 핀 스토어에 맞춘다.

    python -X utf8 -m rag_eval.evalkit.venv --store m3-ort130-2026-09-28
    python -X utf8 -m rag_eval.evalkit.venv --store m3-ort130-2026-09-28 --ext-dir <평평한 확장 폴더>

기본 venv(리포의 엔진 핀)로는 다른 판의 핀 스토어를 잴 수 없다. 질의 런타임이 저장 벡터의
런타임과 다르면 `run_nodes` 가 거부하고(`evalkit.runtime`), 엔진이 다르면 확장이 안 붙는다.
그래서 지금까지 손으로 만든 별도 venv(onnxruntime 1.30 · 0.20 확장)를 썼는데 그 절차가 기억에만
있었다. 이 모듈이 그 절차다: 락의 `engine`·`encoder_runtime` 을 그대로 깔고, **그 venv 로**
런타임 대조와 검색 확장 둘(vector·fts) 적재를 실제로 해 본 뒤에야 실행 명령을 낸다.

venv 는 리포 밖, 워크스페이스의 `_eval-venvs/<스토어>` 에 둔다(`_eval-stores/` 와 같은 자리).
`uv` 가 있으면 그것으로, 없으면 표준 `venv` 로 만든다. 다시 돌리면 같은 판을 다시 맞춘다.

종료코드: 0 준비됨 · 1 런타임 불일치나 확장 적재 실패(메시지가 무엇을 할지 말한다).
근거: [[vinc.manual.rag]]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

from rag_eval.evalkit import runtime

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent if ROOT.parent.name != "_wt" else ROOT.parent.parent
#: 질의 벡터를 바꾸지 않는 의존. 워커가 모델 경로를 풀 때 `huggingface_hub` 를 import 한다.
EXTRA = ("huggingface_hub",)
_OPENSSL = ("libssl-3-x64.dll", "libcrypto-3-x64.dll")

#: 새 venv 안에서 돌리는 검사. 런타임 지문과 검색 확장 둘의 적재 결과를 JSON 한 줄로 낸다.
#: algo 는 보지 않는다: 그래프 알고리즘은 2026-09-28 부터 순수 파이썬(`api/graph_algo.py`)이고,
#: 0.20.x 의 algo 확장은 상류 결함(LadybugDB/ladybug#857)으로 Windows 에서 안 붙는다.
_CHECK = r"""
import json, os, sys, tempfile
sys.path.insert(0, os.getcwd())
from rag_eval.evalkit import runtime
from store.backend import open_store
from store.config import load_config
out = {"fingerprint": runtime.fingerprint(), "ext": {}}
s = open_store(load_config({"data_dir": os.path.join(tempfile.mkdtemp(), "s")}))
for name in ("vector", "fts"):
    try:
        s._load_ext(name)
        out["ext"][name] = "ok"
    except Exception as exc:
        out["ext"][name] = f"{type(exc).__name__}: {str(exc)[:200]}"
s.close()
print("CHECK " + json.dumps(out))
"""

#: 이름으로 확장을 받아 온다(`~/.lbdb/extension/<판>` 을 채운다). 확장이 안 붙을 때 한 번만.
_INSTALL = r"""
import os, tempfile, ladybug as lb
c = lb.Connection(lb.Database(os.path.join(tempfile.mkdtemp(), "x")))
for n in ("vector", "fts"):
    c.execute(f"INSTALL {n}")
"""


def pins(entry: dict) -> list[str]:
    """락 항목 → pip 요구 목록. 엔진은 `engine`("ladybug 0.20.3")에서, 없으면 리포 핀에서."""
    m = re.match(r"ladybug\s+(\S+)", entry.get("engine") or "")
    engine = m.group(1) if m else _repo_engine_pin()
    rt = entry.get("encoder_runtime") or {}
    missing = [p for p in runtime.RUNTIME_PACKAGES if not rt.get(p)]
    if missing:
        raise SystemExit(f"락 항목에 질의 런타임 판이 없다: {missing} — 모르는 판으로 잴 수 없다")
    return [f"ladybug=={engine}", *(f"{p}=={rt[p]}" for p in runtime.RUNTIME_PACKAGES), *EXTRA]


def _repo_engine_pin() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'"ladybug==([^"]+)"', text).group(1)


def _python(dest: pathlib.Path) -> pathlib.Path:
    return dest / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _env(ext_dir: str | None) -> dict:
    # 전역 PYTHONHOME/PYTHONPATH 가 시스템 파이썬을 가리키면 venv 도 깨진다(CLAUDE.md §5).
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}
    if ext_dir:
        env["VINC_LBUG_EXT_DIR"] = ext_dir
    return env


def _git_mingw_bin() -> str:
    """Git for Windows 가 OpenSSL 3 을 같은 이름으로 싣는 폴더. `which git` 은 셸마다 다른 자리를
    주므로(cmd/ · mingw64/bin/) git 이 스스로 말하는 exec-path 에서 거슬러 오른다."""
    try:
        out = subprocess.run(["git", "--exec-path"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""
    return str(pathlib.Path(out).parents[1] / "bin") if out else ""


def _openssl_dir() -> pathlib.Path | None:
    """OpenSSL 3 DLL 두 개가 **그 이름 그대로** 있는 폴더. 찾는 곳은 `packaging/sidecar.spec` 과 같다."""
    cands = [os.environ.get("VINC_OPENSSL_DIR", ""), r"C:\Program Files\OpenSSL\bin",
             r"C:\Program Files\OpenSSL-Win64\bin", _git_mingw_bin(),
             r"C:\Program Files\Git\mingw64\bin"]
    for d in cands:
        if d and all((pathlib.Path(d) / n).is_file() for n in _OPENSSL):
            return pathlib.Path(d)
    return None


def place_openssl(dest: pathlib.Path) -> str:
    """Windows 에서 OpenSSL 3 을 엔진 옆에 둔다. 0.19 부터 휠이 싣지 않아서, 없으면 네이티브
    모듈 import 가 실패하고 C API 폴백의 "Could not find lbug C API shared library" 로
    떨어진다(그 문구는 원인을 가리키지 않는다). 손으로 만든 예전 venv 가 바로 이 두 파일을
    ladybug 폴더에 복사해 두고 있었고, 그 단계는 어디에도 적혀 있지 않았다."""
    if os.name != "nt":
        return ""
    src = _openssl_dir()
    if src is None:
        raise SystemExit("OpenSSL 3(libssl-3-x64.dll · libcrypto-3-x64.dll)을 못 찾았다. "
                         "설치하거나 VINC_OPENSSL_DIR 로 그 폴더를 알려 줄 것.")
    pkg = dest / "Lib" / "site-packages" / "ladybug"
    for n in _OPENSSL:
        shutil.copy2(src / n, pkg / n)
    return str(src)


def build(dest: pathlib.Path, reqs: list[str]) -> None:
    uv = shutil.which("uv")
    if uv:
        subprocess.run([uv, "venv", "--allow-existing", "--python", sys.executable, str(dest)],
                       check=True, env=_env(None))
        subprocess.run([uv, "pip", "install", "--python", str(_python(dest)), *reqs],
                       check=True, env=_env(None))
    else:
        subprocess.run([sys.executable, "-m", "venv", str(dest)], check=True, env=_env(None))
        subprocess.run([str(_python(dest)), "-m", "pip", "install", "--quiet", *reqs],
                       check=True, env=_env(None))


def check(py: pathlib.Path, ext_dir: str | None) -> dict:
    def run() -> dict:
        cp = subprocess.run([str(py), "-X", "utf8", "-c", _CHECK], cwd=ROOT, text=True,
                            capture_output=True, env=_env(ext_dir))
        line = next((x for x in cp.stdout.splitlines() if x.startswith("CHECK ")), "")
        if not line:
            raise SystemExit(f"검사가 끝나지 못했다:\n{cp.stderr[-2000:]}")
        return json.loads(line[6:])
    got = run()
    if not ext_dir and any(v != "ok" for v in got["ext"].values()):
        subprocess.run([str(py), "-X", "utf8", "-c", _INSTALL], env=_env(None))
        got = run()
    return got


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--store", required=True, help="락의 스토어 키")
    ap.add_argument("--dest", help="venv 자리(기본: <워크스페이스>/_eval-venvs/<스토어>)")
    ap.add_argument("--ext-dir", help="확장 파일이 평평하게 든 폴더(VINC_LBUG_EXT_DIR 로 넘긴다)")
    a = ap.parse_args(argv)
    entry = runtime.lock_entry(a.store)
    dest = pathlib.Path(a.dest) if a.dest else WORKSPACE / "_eval-venvs" / a.store
    reqs = pins(entry)
    print(f"venv {dest}\n  {' '.join(reqs)}", flush=True)
    build(dest, reqs)
    ssl = place_openssl(dest)
    if ssl:
        print(f"  OpenSSL 3 ← {ssl}", flush=True)
    got = check(_python(dest), a.ext_dir)
    bad = runtime.runtime_mismatch(entry, got["fingerprint"])
    failed = {k: v for k, v in got["ext"].items() if v != "ok"}
    print(f"engine {got['fingerprint']['engine']['ladybug']} · "
          f"runtime {got['fingerprint']['encoder_runtime']} · ext {got['ext']}")
    if bad:
        print(f"런타임이 락과 다르다: {bad}", file=sys.stderr)
        return 1
    if failed:
        print("확장이 안 붙는다. 이 엔진 판의 확장 파일(libvector·libfts.lbug_extension)을 한 폴더에 "
              "두고 --ext-dir 로 다시 돌릴 것.", file=sys.stderr)
        return 1
    ext = f"VINC_LBUG_EXT_DIR={a.ext_dir} " if a.ext_dir else ""
    print("\n준비됐다. G0:\n"
          f"  cd {ROOT}\n"
          f"  {ext}PYTHONPATH=scripts {_python(dest)} -X utf8 -m rag_eval.evalkit.run_nodes "
          f"--store {a.store} --onnx <bge-m3-int8-rr.onnx> --out ../_eval-runs/<name>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
