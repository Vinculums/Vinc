"""컨테이너 앱 구조 설정의 드리프트 게이트 — 선언 없는 층에 기준선을 준다.

Terraform(infra/)은 컨테이너 앱을 의도적으로 안 든다(시크릿이 state 에 평문으로
남고, 이미지 태그는 배포마다 바뀌어 매 배포가 drift 가 된다 — infra/imports.tf).
그런데 실측된 인벤토리 표류는 전부 이 층에서 났다([[record:app-config-drift-gate-shipped-2026-09-02]]):
인벤토리에 없던 넷째 앱, "라이브 확인 불가"로 남아 있던 컨테이너 크기들, 그리고
excludedPaths 소실이 팀 OAuth 를 하루 넘게 죽인 08-30 — 경보도 배포도 그동안 초록이었다.

그래서 **값이 아니라 구조**를 잰다: env 는 이름만(값·시크릿 값 제외), 스케일·자원·
ingress·볼륨·auth(excludedPaths 포함). 이미지·리비전은 안 본다 — 배포마다 바뀌는
것이 정상인 필드가 기준선에 들어가면 게이트가 매 배포마다 운다.

기준선은 ``scripts/app-config-baseline.json`` 이고 graph-refs.json 과 같은 규율이다:
의도한 변경은 ``--write`` 로 기준선을 재생성해 **같은 커밋에** 넣는다. 대조는
hub-deploy 가 배포 직후 수행한다 — 포털 수동 변경은 다음 배포에서 걸린다. 주기
실행이 아니라 배포 시점 실행인 이유는 az 로그인이 이미 있는 자리라서다.

배포·헬스 검증은 이 앞에서 이미 통과한다. 다만 hub-deploy는 이 게이트가 실패하면
실행을 실패로 남긴다. 서비스 상태와 기준선 차이를 따로 확인하고 의도한 변경을 등록한다.

ponytail: 배포 사이의 드리프트 창은 못 본다. 주기 감시가 필요해지면 infra.yml 의
Reader 신원에 main-ref 페더레이티드 자격증명을 더해 schedule 잡으로 올릴 것.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from hub_target import target_account_app, target_hub_app
except ImportError:  # 리포 루트에서 scripts 패키지로 읽힐 때
    from scripts.hub_target import target_account_app, target_hub_app

# 허브 앱과 계정 앱은 대상 설정(.github/container-apps-target.env)을 hub-deploy 의 표 한 줄로 푼 이름이다.
# hub-deploy 의 배포 잡은 plan 이 푼 HUB_APP·ACCOUNT_APP 을 넘기므로 그 값을 먼저 쓴다(배포 커밋의 설정이
# 워크플로보다 오래될 수 있다). 기준선의 두 항목 이름도 이 이름이다. 설정을 바꾸는 변경은 기준선을 같은 커밋에 고친다.
HUB_APP = os.environ.get("HUB_APP") or target_hub_app()
ACCOUNT_APP = os.environ.get("ACCOUNT_APP") or target_account_app()
# 게이트는 여기 적힌 앱만 읽는다. 목록에 없는 앱은 라이브에 남아 있어도 대조하지 않으므로
# 앱을 내릴 때는 이 목록과 기준선에서 먼저 빼고, 라이브 삭제는 그 뒤에 해도 게이트가 울지 않는다.
APPS = (HUB_APP, ACCOUNT_APP, "vinc-layout")
RG = "vincsRG"
BASELINE = Path(__file__).resolve().parent / "app-config-baseline.json"


def _az(args: list[str]) -> dict:
    """az 호출 → stdout JSON. 확장 경고는 stderr 라 파싱을 안 더럽힌다."""
    exe = shutil.which("az")
    if not exe:
        raise RuntimeError("az CLI not found")
    r = subprocess.run([exe, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"az {' '.join(args[:3])} rc={r.returncode}: "
                           f"{(r.stderr or '').strip()[:200]}")
    return json.loads(r.stdout or "{}")


def project_app(show: dict, auth: dict) -> dict:
    """구조 투영 — 순수 함수. 값(env 값·시크릿 값)과 배포마다 바뀌는 필드
    (이미지·리비전)는 여기 없어야 한다. 그것이 이 게이트의 계약이다."""
    p = show.get("properties") or {}
    tmpl = p.get("template") or {}
    conf = p.get("configuration") or {}
    conts = tmpl.get("containers") or []
    c0 = conts[0] if conts else {}
    ing = conf.get("ingress") or {}
    gv = (auth or {}).get("globalValidation") or {}
    idp = (auth or {}).get("identityProviders") or {}
    plat = (auth or {}).get("platform") or {}
    return {
        "containers": [c.get("name") or "" for c in conts],
        "env_names": sorted(e.get("name") or "" for e in (c0.get("env") or [])),
        "resources": {k: (c0.get("resources") or {}).get(k)
                      for k in ("cpu", "memory", "ephemeralStorage")},
        # cooldownPeriod 는 배포가 아니라 운영자가 정하는 값이라 여기 든다. 아래
        # test_배포마다_바뀌는_필드는_없다 가 막는 것(이미지·리비전)과는 반대편이다 —
        # 매 배포 저절로 바뀌지 않으므로 기준선에 들어가도 게이트가 매번 울지 않는다.
        "scale": {k: (tmpl.get("scale") or {}).get(k)
                  for k in ("minReplicas", "maxReplicas", "cooldownPeriod")},
        "ingress": {
            "external": ing.get("external"),
            "targetPort": ing.get("targetPort"),
            "transport": ing.get("transport"),
            "allowInsecure": ing.get("allowInsecure"),
            "customDomains": sorted((d.get("name") or "")
                                    for d in (ing.get("customDomains") or [])),
        } if ing else None,
        "secret_names": sorted(s.get("name") or ""
                               for s in (conf.get("secrets") or [])),
        "volumes": sorted(
            [{"name": v.get("name") or "", "storageType": v.get("storageType") or "",
              "storageName": v.get("storageName") or ""}
             for v in (tmpl.get("volumes") or [])],
            key=lambda v: str(v["name"])),
        "mounts": sorted(
            [{"volumeName": m.get("volumeName") or "",
              "mountPath": m.get("mountPath") or ""}
             for m in (c0.get("volumeMounts") or [])],
            key=lambda m: str(m["mountPath"])),
        # auth show 가 {} 를 주면(플랫폼 인증 없음) None — "설정 없음"도 상태다.
        # excludedPaths 는 08-30 사고의 그 필드라 정렬해 통째로 든다.
        "auth": {
            "platformEnabled": bool(plat.get("enabled")),
            "unauthenticatedClientAction": gv.get("unauthenticatedClientAction"),
            "excludedPaths": sorted(gv.get("excludedPaths") or []),
            "providers": sorted(idp.keys()),
        } if auth else None,
    }


def measure(rg: str = RG, apps: tuple[str, ...] = APPS, *, strict: bool = False) -> dict:
    """대상 앱의 라이브 구조. 앱이 없으면 {'missing': True} — 기준선에는 이 형태가
    없으므로 대조가 시끄럽게 실패한다(넷째-앱-누락 사고의 재발 방지가 이 줄이다)."""
    out: dict = {}
    for app in apps:
        try:
            show = _az(["containerapp", "show", "-n", app, "-g", rg, "-o", "json"])
        except Exception as exc:      # noqa: BLE001 — 부재도 측정 결과다
            if strict:
                raise RuntimeError(f"{app} structure could not be read") from None
            out[app] = {"missing": True, "reason": str(exc)[:200]}
            continue
        try:
            auth = _az(["containerapp", "auth", "show", "-n", app, "-g", rg,
                        "-o", "json"])
        except Exception:
            if strict:
                raise RuntimeError(f"{app} authentication structure could not be read") from None
            auth = {}
        out[app] = project_app(show, auth)
    return out


def _dump(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true",
                    help="기준선을 라이브로 재생성한다(의도한 변경 — 같은 커밋에)")
    ap.add_argument("--baseline", default=str(BASELINE))
    ap.add_argument("--rg", default=RG)
    ap.add_argument("--apps", nargs="+", default=list(APPS),
                    help="새 앱을 기준선에 편입하기 전 라이브 구조를 함께 수집한다")
    ap.add_argument("--require-present", action="store_true",
                    help="모든 앱과 인증 구조를 읽었을 때만 기준선을 수집한다")
    a = ap.parse_args(argv)
    try:
        live = measure(a.rg, tuple(a.apps), strict=a.require_present)
    except RuntimeError as exc:
        print(f"측정 불가: {exc}", file=sys.stderr)
        return 2
    text = _dump(live)
    path = Path(a.baseline)
    if a.write:
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"기준선 갱신: {path}")
        return 0
    if not path.exists():
        print(f"기준선이 없다: {path} — 먼저 --write 로 만든다", file=sys.stderr)
        return 2
    base = path.read_text(encoding="utf-8")
    if base == text:
        print(f"드리프트 없음 — 앱 {len(live)}개가 기준선과 일치")
        return 0
    print("앱 구조 드리프트 — 기준선(-)과 라이브(+)가 다르다.", file=sys.stderr)
    print("의도한 변경이면 `python scripts/gate_app_config.py --write` 후 커밋,",
          file=sys.stderr)
    print("아니면 Azure 쪽이 움직인 것이다(08-30 excludedPaths 류).", file=sys.stderr)
    sys.stderr.writelines(difflib.unified_diff(
        base.splitlines(keepends=True), text.splitlines(keepends=True),
        fromfile="baseline", tofile="live"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
