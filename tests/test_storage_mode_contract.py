# -*- coding: utf-8 -*-
"""저장 위치와 로컬 모드(VIN-130) 화면·셸 배선의 계약.

JS 러너가 없어 소스를 텍스트로 못 박는다(`test_account_mode_contract` 와 같은 방식).
사이드카 쪽 동작은 `test_local_mode` 가 실제 스토어로 본다. 여기는 그 동작을 부르는 줄들이
조용히 빠지거나 순서가 뒤집히지 않게 한다. 깨지면 이렇게 보인다: 계정 모드에서 옮기기의
진행을 계정에 물어 영영 안 끝나거나, 모자란 채로 모드가 바뀌어 빠진 것이 안 보이는 쪽에
남거나, 셸이 폴더를 바꿔도 사이드카가 옛 폴더를 연다.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app/src"
STORE = (SRC / "storage-mode.ts").read_text(encoding="utf-8")
API = (SRC / "api.ts").read_text(encoding="utf-8")
SETTINGS = (SRC / "surfaces/settings.ts").read_text(encoding="utf-8")
VITE = (ROOT / "app/vite.config.ts").read_text(encoding="utf-8")
MAIN_RS = (ROOT / "app/src-tauri/src/main.rs").read_text(encoding="utf-8")
CAPS = (ROOT / "app/src-tauri/capabilities/default.json").read_text(encoding="utf-8")


def _fn(src: str, name: str) -> str:
    m = re.search(rf"^(?:export )?(?:async )?function {name}\(", src, re.M)
    assert m, name
    rest = src[m.start():]
    nxt = re.search(r"^(?:export )?(?:async )?function |^const |^export const ", rest[1:], re.M)
    return rest[: nxt.start() + 1] if nxt else rest


class RoutingTest(unittest.TestCase):

    def test_저장_호출은_늘_사이드카로_간다(self):
        """계정 모드에서 부르는 것들이다. `call` 로 보내면 계정으로 가서 설정도 잡도 없다."""
        block = API[API.index("export const storage = {"):]
        block = block[: block.index("\n};") + 3]
        self.assertNotIn(" call(", block)
        for path in ('"/config"', '"/config/inspect"', '"/sync/push-all"',
                     '"/sync/pull-all"', "`/jobs/${id}`"):
            self.assertIn(path, block)
        self.assertIn('"/config"', API[API.index("const SIDECAR_ONLY"):][:200])

    def test_옮기기의_진행은_사이드카_잡에서_읽는다(self):
        self.assertEqual(STORE.count("status: storage.job"), 2)

    def test_브라우저_dev_프록시가_설정을_넘긴다(self):
        self.assertIn('"|config|shutdown"', VITE)


class NoLossTest(unittest.TestCase):

    def test_수가_안_맞으면_모드를_안_바꾼다(self):
        # 받기(켜기)는 자기 안에서 가른다.
        body = _fn(STORE, "turnOn")
        switch = body.index('storage.setConfig({ mode: "local", mode_chosen: true })')
        self.assertLess(body.index("Number(r.missing) > 0"), switch)
        self.assertLess(switch, body.index('dataMode.set("local")'))
        # 올리기는 한 벌(`moveDeviceIntoAccount`)이고, 빠진 것이 있으면 ok 가 거짓이다.
        move = _fn(STORE, "moveDeviceIntoAccount")
        self.assertRegex(move, r"if \(Number\(r\.missing\) > 0\) return \{ ok: false")
        self.assertNotIn("setConfig", move)    # 모드는 부르는 쪽이 바꾼다
        # 끄기 판의 옮기는 길은 ok 를 본 뒤에만 모드를 바꾼다.
        off = _fn(STORE, "askTurnOff")
        go = off[off.index('go.addEventListener("click"'):]
        gate = go.index("if (!moved.ok)")
        switch = go.index('storage.setConfig({ mode: "account", mode_chosen: true })')
        self.assertLess(gate, switch)
        self.assertLess(switch, go.index('dataMode.set("account")'))

    def test_로그인_뒤_알림은_옮기지_않고_로컬로_적는다(self):
        """VIN-131 (2026-09-29 결정): 옮기기는 설정의 끄기 판에만 있다. 로그인 뒤 화면이
        올리기를 다시 품으면 두 벌이 되고, 한쪽만 "빠진 것이 있으면 안 바꾼다"를 잃는다."""
        dg = (SRC / "device-graph.ts").read_text(encoding="utf-8")
        self.assertNotIn("moveDeviceIntoAccount", dg)
        self.assertNotIn("pushAll", dg)
        self.assertNotIn('mode: "account"', dg)
        # 알린 것은 설정(정본)에 먼저 적고, 못 적으면 닫지 않는다.
        click = dg[dg.index('ok.addEventListener("click"'):]
        self.assertLess(click.index('storage.setConfig({ mode: "local", mode_chosen: true })'),
                        click.index('dataMode.set("local")'))
        self.assertLess(click.index("return;"), click.index("screen.remove()"))

    def test_옮기지_않고_끄기는_설정을_먼저_쓰고_아무것도_옮기지_않는다(self):
        """VIN-179: 올리기가 실패하면 로컬 모드에서 나갈 길이 없었다. 이 길은 폴더를
        건드리지 않고 모드만 바꾼다. 정본(사이드카 설정)이 먼저다: 창의 사본만 바꾸면 다음
        기동의 `decideDataMode` 가 설정 파일의 "local" 을 도로 읽어 온다."""
        body = _fn(STORE, "askTurnOff")
        keep = body[body.index('keep.addEventListener("click"'):]
        keep = keep[: keep.index('go.addEventListener("click"')]
        self.assertLess(keep.index('storage.setConfig({ mode: "account", mode_chosen: true })'),
                        keep.index('dataMode.set("account")'))
        self.assertNotIn("pushAll", keep)
        self.assertNotIn("data_path", keep)

    def test_전량이_아닌_받기는_모드를_안_바꾼다(self):
        body = _fn(STORE, "turnOn")
        self.assertLess(body.index('r.docs_mode !== "everything"'),
                        body.index('storage.setConfig({ mode: "local", mode_chosen: true })'))

    def test_폴더를_먼저_열고_그다음에_받는다(self):
        """받기는 새 폴더의 스토어에 써야 한다. 재기동 전에 받으면 옛 스토어에 들어간다."""
        body = _fn(STORE, "turnOn")
        self.assertLess(body.index("useFolder("), body.index("storage.pullAll("))
        self.assertIn("restartSidecar()", _fn(STORE, "useFolder"))

    def test_위험한_폴더는_확인해야_받는다(self):
        self.assertIn("accept_risk: acceptRisk", _fn(STORE, "useFolder"))
        self.assertIn('r.error === "risky_folder"', _fn(STORE, "useFolder"))


class ShellTest(unittest.TestCase):

    def test_재기동_명령이_등록돼_있다(self):
        handler = MAIN_RS[MAIN_RS.index("generate_handler!["):][:400]
        self.assertIn("sidecar_restart", handler)
        self.assertIn('invoke("sidecar_restart")', API)

    def test_사이드카에_데이터_경로를_박지_않는다(self):
        """넘기면 설정 화면이 바꾼 폴더가 이 프로세스에 영영 안 먹는다(`--data` 가 이긴다)."""
        spawn = MAIN_RS[MAIN_RS.index("fn spawn_sidecar("):]
        spawn = spawn[: spawn.index("\n}\n")]
        self.assertNotIn('"--data"', spawn)

    def test_끄기_전에_부탁하고_끝난_것을_보고_띄운다(self):
        body = MAIN_RS[MAIN_RS.index("async fn sidecar_restart("):]
        body = body[: body.index("\n}\n")]
        self.assertLess(body.index("request_shutdown("), body.index("wait_exit("))
        self.assertLess(body.index("wait_exit("), body.index("spawn_sidecar("))

    def test_폴더_고르기_권한(self):
        self.assertIn('"dialog:default"', CAPS)
        self.assertIn('"plugin:dialog|open"', STORE)


class SettingsTest(unittest.TestCase):

    def test_주제_고르기와_지금_동기화가_없다(self):
        for gone in ("sync-domains", "sync-now", "setSyncDomains", "syncNow"):
            self.assertNotIn(gone, SETTINGS)
            self.assertNotIn(gone, API)

    def test_셸이면_저장_덩어리를_건다(self):
        self.assertIn('${!WEB ? `', SETTINGS)
        self.assertIn("mountStorage(stBox)", SETTINGS)


if __name__ == "__main__":
    unittest.main()
