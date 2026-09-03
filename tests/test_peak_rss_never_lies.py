# -*- coding: utf-8 -*-
"""`peak_rss_mb()` 는 못 쟀을 때 0.0 을 답하면 안 된다.

0.0 은 수치로 읽힌다 — "이 프로세스는 메모리를 거의 안 썼다". 실제 뜻은 "못 쟀다"
였고, 그 둘은 리허설 보고서에서 구분되지 않는다. 2026-09-03 에 실제로 물렸다:
psutil 없는 환경에서 전 실행이 `peak_rss_mb: 0.0` 을 냈고, 그대로 인용했다면
"bge-m3 는 메모리를 안 쓴다"는 보고가 됐다.

원인은 `ctypes.windll.kernel32.GetCurrentProcess` 의 restype 기본값이 `c_int` 라
64비트에서 의사 핸들이 잘리고, 그 실패한 호출의 반환값을 아무도 안 본 것이다.

이 테스트는 플랫폼을 가리지 않는다: 잴 수 있으면 양수, 못 재면 **-1.0**.
0.0 은 어느 쪽도 아니다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from rehearse_rebuild import peak_rss_mb   # noqa: E402


def test_peak_rss_is_a_number_or_an_admission():
    v = peak_rss_mb()
    assert v != 0.0, "0.0 은 '못 쟀다'를 '안 썼다'로 보고하는 값이다 — -1.0 이어야 한다"
    assert v > 0.0 or v == -1.0, f"양수(측정) 또는 -1.0(못 잼) 이어야 하는데 {v}"
