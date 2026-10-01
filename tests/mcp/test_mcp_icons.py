# -*- coding: utf-8 -*-
"""커넥터 아이콘 표면 — initialize serverInfo 와 /favicon.* 서빙.

⚠ 이 파일의 중심은 "아이콘이 실린다"가 아니라 **언제 안 실리는가**다.

2026-07-31 실사고: `icons`·`websiteUrl`(SEP-973, 스펙 2025-11-25)을 협상 버전과
무관하게 항상 실었더니 claude.ai 커넥터가 "계정은 인증됐지만 연결 중 오류"로 죽었다.
근거로 삼았던 "모르는 클라이언트는 추가 필드를 그냥 무시한다"는 스펙 문장에는 맞지만
구현에는 틀렸다 — 협상된 리비전의 스키마로 엄격 검증하는 클라이언트는 모르는 키에서
거부한다. **사양이 허용하는 것과 상대가 받아주는 것은 다르다.**

회귀가 조용하다는 점이 이 테스트의 존재 이유다: 아이콘은 우리가 볼 수 없는 곳
(claude.ai 커넥터 카드)에서 쓰이고, 그쪽은 지금 아이콘을 아예 안 읽는다
(claude-ai-mcp#152). 화면으로는 아무것도 확인할 수 없으므로 계약을 코드로 못 박는다.
"""
from __future__ import annotations

import io
import unittest
from pathlib import Path

from vinc_mcp import http_server, rpc

ROOT = Path(__file__).resolve().parents[2]
HUB_SVG = ROOT / "design-hub/assets/logo/vinc-ring-favicon.svg"

ICON_FIELDS = ("icons", "websiteUrl")


def _init(pv=None) -> dict:
    d = rpc.Dispatcher.__new__(rpc.Dispatcher)      # bridge 없이 _dispatch 만 본다
    return d._dispatch("initialize", {} if pv is None else {"protocolVersion": pv})


class NegotiationGateTest(unittest.TestCase):
    """가장 중요한 계약 — 구 리비전에는 새 필드를 보내지 않는다."""

    def test_지원하는_모든_버전에서_serverInfo_는_name_version_뿐이다(self):
        for pv in rpc.PROTOCOL_VERSIONS:
            with self.subTest(protocolVersion=pv):
                info = _init(pv)["serverInfo"]
                self.assertEqual(sorted(info), ["name", "version"],
                                 f"{pv} 협상인데 스펙 밖 필드가 실렸다: {sorted(info)}")

    def test_모르는_버전_요청도_구_리비전으로_떨어지므로_새_필드가_없다(self):
        """클라이언트가 2025-11-25 를 요청해도 우리는 그걸 협상하지 않는다. 요청만 보고
        필드를 실으면 '협상 결과와 다른 스키마'를 보내게 된다 — 그게 사고의 형태였다."""
        info = _init("2025-11-25")["serverInfo"]
        self.assertNotIn("2025-11-25", rpc.PROTOCOL_VERSIONS)
        for f in ICON_FIELDS:
            self.assertNotIn(f, info)

    def test_protocolVersion_누락시에도_새_필드가_없다(self):
        for f in ICON_FIELDS:
            self.assertNotIn(f, _init()["serverInfo"])

    def test_협상_버전을_그대로_되돌려준다(self):
        for pv in rpc.PROTOCOL_VERSIONS:
            self.assertEqual(_init(pv)["protocolVersion"], pv)
        self.assertEqual(_init("2099-01-01")["protocolVersion"], rpc.LATEST_PROTOCOL_VERSION)

    def test_이름과_버전은_늘_있다(self):
        info = _init(rpc.LATEST_PROTOCOL_VERSION)["serverInfo"]
        self.assertEqual(info["name"], "vinc")
        self.assertTrue(info["version"])


class IconPayloadTest(unittest.TestCase):
    """게이트가 열렸을 때(=2025-11-25 를 정식 지원할 때) 실릴 내용의 계약.

    지금은 PROTOCOL_VERSIONS 에 그 리비전이 없어 아무에게도 안 나가므로, 게이트를
    임시로 열어 페이로드만 검사한다. 목록에 추가하는 날 이 테스트가 그대로 실계약이 된다."""

    def _icons_info(self) -> dict:
        orig = rpc.PROTOCOL_VERSIONS
        rpc.PROTOCOL_VERSIONS = ("2025-11-25",) + orig
        try:
            return _init("2025-11-25")["serverInfo"]
        finally:
            rpc.PROTOCOL_VERSIONS = orig

    def test_게이트가_열리면_icons_와_websiteUrl_이_실린다(self):
        info = self._icons_info()
        self.assertEqual(info["websiteUrl"], "https://vincs.io")
        self.assertTrue(info["icons"], "icons 가 비어 있다")

    def test_PNG_가_첫_항목이다(self):
        """스펙상 클라이언트는 PNG·JPEG 만 필수 지원이고 SVG 는 선택이다. SVG 를 앞에
        두면 SVG 를 못 읽는 클라이언트가 첫 항목만 보고 포기할 수 있다."""
        icons = self._icons_info()["icons"]
        self.assertEqual(icons[0]["mimeType"], "image/png")
        self.assertTrue(icons[0]["src"].endswith(".png"))

    def test_아이콘_src_가_절대_https_다(self):
        """상대경로면 클라이언트가 무엇을 기준으로 풀지 알 수 없다."""
        for ic in self._icons_info()["icons"]:
            self.assertTrue(ic["src"].startswith("https://"), ic["src"])

    def test_sizes_가_실제_래스터와_일치한다(self):
        """어긋나면 클라이언트가 잘못 스케일한다."""
        import struct
        w, h = struct.unpack(">II", http_server._FAVICON_PNG[16:24])
        self.assertEqual(self._icons_info()["icons"][0]["sizes"], f"{w}x{h}")


class FaviconAssetTest(unittest.TestCase):
    """아이콘 파일 자체는 프로토콜 밖이라 게이트와 무관하게 계속 서빙한다
    (브라우저 탭·수동 참조). 사고 때도 이쪽은 손대지 않았다."""

    def test_PNG_상수가_진짜_PNG_다(self):
        self.assertEqual(http_server._FAVICON_PNG[:4], b"\x89PNG")
        self.assertGreater(len(http_server._FAVICON_PNG), 500)

    def test_PNG_가_128px_다(self):
        import struct
        w, h = struct.unpack(">II", http_server._FAVICON_PNG[16:24])
        self.assertEqual((w, h), (128, 128))

    def test_SVG_상수는_그대로_있다(self):
        self.assertIn("<svg", http_server._FAVICON_SVG)


class RequestLoggingTest(unittest.TestCase):
    """실패만 남기고 정상 트래픽은 조용히 — 침묵을 성공으로 오독하지 않기 위해.

    예전엔 log_message 가 전부 삼켰다(가비아 systemd 시절 주석). Azure 에서는 stdout 이
    곧 로그라, 삼키면 장애 때 컨테이너가 조용하다 = 단서가 없다."""

    def _capture(self, code):
        import io
        h = http_server._Handler.__new__(http_server._Handler)
        h.requestline = "POST /mcp?token=SECRET HTTP/1.1"
        h.client_address = ("203.0.113.7", 51234)
        h.address_string = lambda: "203.0.113.7"
        buf = io.StringIO()
        real = http_server.sys.stdout
        http_server.sys.stdout = buf
        try:
            h.log_request(code, "-")
        finally:
            http_server.sys.stdout = real
        return buf.getvalue()

    def test_정상_응답은_로그를_남기지_않는다(self):
        for code in (200, 201, 302, 304):
            self.assertEqual(self._capture(code), "", f"{code} 가 로그를 남겼다")

    def test_실패는_로그를_남긴다(self):
        for code in (400, 401, 500, 502):
            self.assertIn("/mcp", self._capture(code), f"{code} 가 로그에 없다")

    def test_쿼리스트링은_로그에_남지_않는다(self):
        """/authorize 파라미터 등이 컨테이너 로그에 눌러앉지 않도록 잘라낸다."""
        self.assertNotIn("SECRET", self._capture(400))


def _hub_svg() -> str:
    """정본 SVG 에서 동기화 각인 줄만 걷어낸 본문.

    각인은 `sync_out.py` 가 소비 사본에 박는 것이라 허브 원본에는 없지만, 허브와 사본
    어느 쪽을 받아도 같은 값이 나오도록 걸러 둔다."""
    raw = io.open(HUB_SVG, encoding="utf-8").read()
    return chr(10).join(ln for ln in raw.splitlines() if "sync-sha256" not in ln).strip()


class EmbeddedIconMatchesCanon(unittest.TestCase):
    """임베드된 아이콘이 정본과 같은가.

    `http_server.py` 는 이 리포에서 **로고를 인라인해도 되는 유일한 자리**다 — 사이드카는
    단일 바이너리라 외부 자산 경로가 없다. 그런데 인라인 사본이 정본에 닿지 않는 것이
    바로 로고 교체를 44곳짜리 일로 만든 원인이었다(record:a-logo-ssot-that-reaches-nobody).

    "로고가 바뀌면 여기도 갱신할 것"이라고 주석에 적어 두는 방식은 **이미 실패했다**:
    구 로고의 그 주석은 2026-08-08 에 폐기된 리포를 가리킨 채 남아 있었다. 그래서
    `sync_out.py --check` 가 파일 사본에 하는 일을, 여기서는 파이썬 문자열 안의 사본에
    대해 한다.

    깨지면 정본을 바꾸고 임베드를 안 고친 것이다. **임베드를 정본에 맞춘다**(반대가 아니다).
    """

    def test_SVG_는_허브의_파비콘_링이다(self):
        self.assertEqual(
            http_server._FAVICON_SVG, _hub_svg(),
            "커넥터 SVG 가 design-hub 의 파비콘 링과 다르다 — 정본이 바뀌었는데 "
            "_FAVICON_SVG 를 안 고쳤다.")

    # PNG 는 앱 아이콘과 바이트가 같지 않다: 앱 아이콘은 검정 판 위의 흰 링이고(2026-09-26)
    # 커넥터 PNG 는 파비콘과 같은 tight 래스터다. 정본과의 대조는 `npm run logo:check` 가 한다.

    def test_임베드된_PNG_가_실제로_PNG_다(self):
        """base64 를 잘못 잘라 붙이면 디코드는 되는데 이미지가 아닐 수 있다. 그 상태로도
        서버는 200 을 주고 아이콘만 조용히 안 뜬다."""
        self.assertEqual(http_server._FAVICON_PNG[:8], bytes([137, 80, 78, 71, 13, 10, 26, 10]))

    def test_정본이_사라지면_위_대조가_공허하게_통과하지_않는다(self):
        """파일이 없으면 빈 값끼리 비교해 통과로 보일 수 있는 구성을 막는다."""
        self.assertTrue(HUB_SVG.is_file(), f"정본이 없다: {HUB_SVG}")
        self.assertGreater(len(_hub_svg()), 200)


if __name__ == "__main__":
    unittest.main()
