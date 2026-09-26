# -*- coding: utf-8 -*-
"""데스크톱 계정 모드(VIN-128)의 계약.

JS 러너가 없어 소스를 텍스트로 못 박는다(`test_sync_scheduler` 와 같은 방식). 여기 있는
것들은 깨져도 화면이 **조용히** 틀린다: 키가 헤더로 새거나, 팀에 쓰려던 것이 개인으로
가거나, 권한 거절 한 번에 로그아웃되거나, 기존 로컬 작업이 빈 계정 화면 뒤로 가려진다.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_TS = (ROOT / "app/src/api.ts").read_text(encoding="utf-8")
MAIN_TS = (ROOT / "app/src/main.ts").read_text(encoding="utf-8")
FIRSTRUN_TS = (ROOT / "app/src/firstrun.ts").read_text(encoding="utf-8")
CONNECT_TS = (ROOT / "app/src/account-connect.ts").read_text(encoding="utf-8")
COPY_TS = (ROOT / "app/src/copy.ts").read_text(encoding="utf-8")


def _fn(src: str, name: str) -> str:
    """`async function name(` 또는 `function name(` 의 몸통(다음 최상위 선언 전까지)."""
    m = re.search(rf"^(?:export )?(?:async )?function {name}\(", src, re.M)
    assert m, name
    rest = src[m.start():]
    nxt = re.search(r"^(?:export )?(?:async )?function |^const |^export const ", rest[1:], re.M)
    return rest[: nxt.start() + 1] if nxt else rest


class RoutingTest(unittest.TestCase):

    def test_계정_모드의_데이터_호출은_통과로로_간다(self):
        call = _fn(API_TS, "call")
        self.assertIn("if (accountMode() && !sidecarOnly(path)) return accountCall(", call)
        self.assertIn('new URL("/space/proxy", cfg.base)', API_TS)

    def test_기기의_일은_계정_모드에서도_사이드카가_받는다(self):
        """기기 연결은 루프백 콜백이라 사이드카가 받아야 한다. 이 목록이 빠지면 계정
        모드에서 로그인 자체가 계정으로 넘어가 404 가 된다."""
        m = re.search(r"const SIDECAR_ONLY = \[([^\]]*)\]", API_TS)
        self.assertIsNotNone(m)
        items = set(re.findall(r'"([^"]+)"', m.group(1)))
        self.assertTrue({"/health", "/connect/", "/sync", "/backup", "/space/"} <= items, items)


class CredentialTest(unittest.TestCase):

    def test_멤버_키는_바디로만_간다(self):
        """사이드카의 `Authorization` 은 셸 세션 토큰 자리다. 멤버 키를 거기 실으면
        사이드카 인증이 깨지고, 로그에 키가 남는다(`api/spaceproxy.py` 키는 바디로만)."""
        via = _fn(API_TS, "viaProxy")
        self.assertIn("token: key", via)
        self.assertIn("Bearer ${cfg.token}", via)
        self.assertNotIn("Bearer ${key}", via)

    def test_개인이면_team_을_안_싣는다(self):
        via = _fn(API_TS, "viaProxy")
        self.assertIn("...(team ? { team } : {})", via)

    def test_권한_거절로_로그아웃하지_않는다(self):
        """계정은 키 거절과 역할 거절을 같은 코드로 준다. 키만 보는 읽기로 한 번 더
        물어야 팀에서 권한 없는 일을 눌렀다고 로그인 화면으로 쫓겨나지 않는다."""
        acct = _fn(API_TS, "accountCall")
        i = acct.index('code === "not_authenticated"')
        tail = acct[i:]
        self.assertIn('viaProxy(key, "GET", "/spaces"', tail)
        self.assertLess(tail.index('"/spaces"'), tail.index('"signIn"'))

    def test_로그인과_로그아웃이_캐시된_키를_버린다(self):
        self.assertIn("forgetMemberKey", CONNECT_TS)
        store = CONNECT_TS[CONNECT_TS.index("async function storeKey"):]
        self.assertIn("forgetMemberKey()", store[: store.index("dispatchEvent")])
        forget = CONNECT_TS[CONNECT_TS.index("export function forgetAccount"):]
        self.assertIn("forgetMemberKey()", forget[: forget.index("}")])


class GateTest(unittest.TestCase):

    def test_로그인이_첫_화면보다_먼저다(self):
        """사이드카 연결 → 로그인 → 모드 결정 → 첫 화면. 순서가 바뀌면 계정 모드의 첫
        조회가 키 없이 나가거나, 로그인이 사이드카 없이 루프백을 기다린다."""
        i_conn = MAIN_TS.index("await connectSidecar();")
        i_sign = MAIN_TS.index('requireSignIn("first")')
        i_mode = MAIN_TS.index("await decideDataMode()")
        i_route = MAIN_TS.index("route();", i_mode)
        self.assertLess(i_conn, i_sign)
        self.assertLess(i_sign, i_mode)
        self.assertLess(i_mode, i_route)

    def test_오프라인_버튼이_없고_닫히지_않는다(self):
        # 풀화면이라 닫기 단추도 Esc 도 없다. 닫는 것은 로그인이 끝났을 때의 close() 하나다.
        screen = _fn(FIRSTRUN_TS, "openSignInScreen")
        self.assertNotIn("keydown", screen)
        self.assertNotIn("Escape", screen)
        self.assertEqual(FIRSTRUN_TS.count("close();"), 1)
        self.assertNotIn("offlineCta", FIRSTRUN_TS)
        self.assertNotIn("offlineCta", COPY_TS)
        self.assertNotIn("offerAccount", MAIN_TS)

    def test_로그인은_모달이_아니라_창_전체다(self):
        """빈 셸 위에 뜨는 시트가 아니라 창 전체가 로그인이다(2026-09-26). 뒤의 셸은
        잠그고, 셸에서는 타이틀바(창 손잡이)를 덮지 않는다."""
        self.assertNotIn("openSheet", FIRSTRUN_TS)
        screen = _fn(FIRSTRUN_TS, "openSignInScreen")
        self.assertIn('screen.id = "signin-screen"', screen)
        self.assertIn('shell?.setAttribute("inert", "")', screen)
        self.assertIn('shell?.removeAttribute("inert")', screen)
        css = (ROOT / "app/src/app.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"#signin-screen \{[^}]*position: fixed; inset: 0;")
        self.assertIn("[data-shell] #signin-screen { top: var(--titlebar-h); }", css)

    def test_이메일이_먼저_Google_은_그다음이다(self):
        """웹 로그인과 같은 순서 · 같은 말. Google 단추는 연결 페이지에 via=google 을 실어
        로그인 안내를 건너뛴다(켜짐 여부는 그 페이지가 판단한다)."""
        screen = _fn(FIRSTRUN_TS, "openSignInScreen")
        self.assertLess(screen.index('id="fr-signin"'), screen.index('class="si-or"'))
        self.assertLess(screen.index('class="si-or"'), screen.index('id="fr-google"'))
        self.assertIn('google.addEventListener("click", () => void start("google"))', FIRSTRUN_TS)
        self.assertIn("connectAccount(undefined, { via })", FIRSTRUN_TS)
        body = _fn(CONNECT_TS, "connectAccount")
        self.assertIn('const via = opts.via ? `&via=${opts.via}` : "";', body)
        self.assertIn("${encodeURIComponent(state)}${via}", body)
        # 브랜드 네 색은 면제로 드러나 있어야 한다(gate_surface 가 이유를 보고한다).
        self.assertIn("// ds-allow: Google 로고", FIRSTRUN_TS)

    def test_로그인_화면은_특정_AI_호스트를_내세우지_않는다(self):
        """첫 화면의 이유 목록은 제품 소개다. 특정 호스트 이름(claude.ai 등)을 앞세우면
        그 호스트 홍보가 된다(2026-09-26 반려). 호스트 이름은 연결 설정의 사용법에만 쓴다."""
        blocks = re.findall(r"\n  firstRun: \{.*?\n  \},", COPY_TS, re.S)
        self.assertEqual(len(blocks), 2)  # EN · KO
        for block in blocks:
            for host in ("claude", "chatgpt", "openai", "anthropic", "gemini", "copilot"):
                self.assertNotIn(host, block.lower())

    def test_게이트는_셸에서만_선다(self):
        """웹은 세션 게이트가 따로 있고, 브라우저 dev 는 개발자의 자리다(캡처용 표식만)."""
        body = _fn(FIRSTRUN_TS, "needsSignIn")
        self.assertIn("IN_SHELL", body)
        self.assertIn("!hasAccount()", body)


class ExistingDataTest(unittest.TestCase):

    def test_로컬_데이터가_있으면_로컬로_남는다(self):
        decide = _fn(API_TS, "decideDataMode")
        self.assertIn('(await localStoreHasData()) ? "local" : "account"', decide)

    def test_모드의_정본은_사이드카_설정이다(self):
        """VIN-130: 설정 화면·사이드카·로컬 MCP 가 같은 파일을 본다. 창 저장소는 사본이다.
        설정이 먼저이고, 정한 값은 설정에 적는다(다음 기동이 창과 무관하게 같은 답을 낸다)."""
        decide = _fn(API_TS, "decideDataMode")
        self.assertLess(decide.index("storage.config()"), decide.index("dataMode.get()"))
        self.assertIn("storage.setConfig({ mode: next })", decide)

    def test_모르면_있다고_답하고_두_소유자를_본다(self):
        """모를 때 계정으로 넘기면 있던 작업이 가려진다. 로컬 MCP 는 `user:local` 로
        쓰므로(VIN-131) 그쪽도 세야 MCP 로만 쓴 사람의 작업이 안 가려진다."""
        has = _fn(API_TS, "localStoreHasData")
        self.assertIn('"tenant:local", "user:local"', has)
        self.assertRegex(has, r"catch \{\s*return true;")


class SyncTest(unittest.TestCase):

    def test_계속_도는_동기화가_없다(self):
        """VIN-130: 데이터는 모드를 바꿀 때만 옮긴다. 스케줄러가 돌아오면 로컬 모드에서
        계정에 사본이 생긴다(발주자 결정: 로컬 모드의 계정에는 사본이 없다)."""
        self.assertFalse((ROOT / "app/src/sync-scheduler.ts").exists())
        self.assertNotIn("sync-scheduler", MAIN_TS)
        self.assertNotIn("syncNow", API_TS)

    def test_스페이스는_계정에_있을_때만_의미가_있다(self):
        self.assertIn('get team(): string { return remote() ?', API_TS)


if __name__ == "__main__":
    unittest.main()
