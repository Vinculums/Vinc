#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""컨테이너 안 인코드 프로브의 출력 → 요약 표(마크다운).

**왜 워크플로 안의 인라인 파이썬이 아닌가.** 같은 파서가 두 워크플로에 필요하고
(`container-latency-probe.yml` 이 e5 를, `encode-m3-probe.yml` 이 m3 를 잰다), 인라인으로
두면 두 벌이 되어 언젠가 갈린다. 그리고 인라인 파이썬은 **테스트가 안 된다** — 이 파일의
계산은 한 번밖에 못 도는 워크플로의 유일한 산출물이라, 합성 입력으로 미리 태울 수 있어야
한다.

입력은 프로브가 컨테이너 안에서 찍은 줄들이다:

    PROBE_CPU <모델명>          PROBE_NPROC <n>        PROBE_FLAGS <플래그들>
    PROBE_ENCODE <i> <코드> <초>          질의 한 건 왕복
    PROBE_BATCH  <j> <코드> <초> <건수>   패시지 배치
    PROBE_RSS_N/MAX_KB/SUM_KB <값>        (인코더 컨테이너 쪽 파일에서)

⚠ 성공(200)만 센다. 실패를 분포에 섞으면 **빨리 실패한 호출이 지연을 낮춘다** — 초록인 채로
좋아 보이는 수치가 나오는 모양이라 특히 위험하다.

사용:
    python scripts/summarize_encode_probe.py hub.out enc.out [--title "..."]
종료코드 1: 잴 것을 못 쟀다(성공한 왕복이 0건이거나 배치가 0건).
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

#: 컨테이너 선언 메모리. RSS 를 이 값과 나란히 놓는 이유는 이 숫자가 답해야 하는 질문이
#: "인코더와 리랭커가 한 컨테이너에 들어가는가" 이기 때문이다(계획 §4 의 셋째 조건).
LIMIT_KB = 2 * 1024 * 1024


def _tag(src: str, tag: str) -> str | None:
    m = re.search(rf"^PROBE_{tag} (.*)$", src, re.M)
    return m.group(1).strip() if m else None


def _num(src: str, tag: str) -> int | None:
    m = re.search(rf"^PROBE_{tag} (\d+)$", src, re.M)
    return int(m.group(1)) if m else None


def pct(xs: list[float], q: float) -> float | None:
    """가장 가까운 순위값. 표본이 열둘·넷이라 보간은 없는 정밀도를 흉내 낼 뿐이다."""
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))]


def parse(hub: str, enc: str) -> dict:
    calls = [(int(i), c, float(t)) for i, c, t in
             re.findall(r"^PROBE_ENCODE (\d+) (\d+) ([\d.]+)$", hub, re.M)]
    ok = [t for _, c, t in calls if c == "200"]
    batches = [(float(t), int(n)) for _, c, t, n in
               re.findall(r"^PROBE_BATCH (\d+) (\d+) ([\d.]+) (\d+)$", hub, re.M)
               if c == "200"]
    out: dict = {
        "cpu": _tag(hub, "CPU"), "nproc": _tag(hub, "NPROC"), "flags": _tag(hub, "FLAGS"),
        "calls": len(calls), "ok": len(ok), "batches": len(batches),
        "enc_cpu": next((l.split(":", 1)[-1].strip()
                         for l in enc.splitlines() if "model name" in l), None),
        "rss_max_kb": _num(enc, "RSS_MAX_KB"), "rss_sum_kb": _num(enc, "RSS_SUM_KB"),
        "rss_n": _num(enc, "RSS_N"),
    }
    if ok:
        # 첫 호출은 따로 둔다. 인코더가 minReplicas 0 이면 그것만 콜드 경로이고, 나머지와
        # 한 분포에 섞으면 p95 가 콜드 하나에 끌려간다.
        out["first_ms"] = ok[0] * 1000
        tail = [t * 1000 for t in ok[1:]]
        out["warm"] = {"n": len(tail), "p50": pct(tail, 0.5), "p95": pct(tail, 0.95),
                       "min": min(tail) if tail else None,
                       "max": max(tail) if tail else None}
    if batches:
        ts = [t * 1000 for t, _ in batches]
        texts = sum(n for _, n in batches)
        secs = sum(t for t, _ in batches)
        out["batch"] = {"n": len(batches), "per_batch": batches[0][1],
                        "p50": pct(ts, 0.5), "p95": pct(ts, 0.95),
                        "texts": texts, "secs": secs,
                        "per_s": texts / secs if secs else None,
                        "ms_per_text": secs / texts * 1000 if texts else None}
    return out


def render(d: dict, title: str) -> list[str]:
    L = [f"## {title}", "", "| 항목 | 값 |", "|---|---|"]
    L.append(f"| 호출한 컨테이너 CPU | {d['cpu'] or '읽지 못함'} |")
    L.append(f"| vCPU 수 | {d['nproc'] or '읽지 못함'} |")
    if d.get("flags"):
        L.append(f"| 플래그 | `{d['flags']}` |")
    if "first_ms" in d:
        L.append(f"| /encode 첫 호출 | **{d['first_ms']:.0f} ms** |")
    w = d.get("warm")
    if w and w["n"]:
        L.append(f"| 질의 p50 / p95 | **{w['p50']:.0f} ms** / {w['p95']:.0f} ms "
                 f"(첫 호출 제외 {w['n']}건 · 최소 {w['min']:.0f} · 최대 {w['max']:.0f}) |")
    L.append(f"| 질의 성공/전체 | {d['ok']}/{d['calls']} |")
    b = d.get("batch")
    if b:
        L.append(f"| 패시지 배치 p50 / p95 | **{b['p50']:.0f} ms** / {b['p95']:.0f} ms "
                 f"(배치 {b['n']}개 · 배치당 {b['per_batch']}건) |")
        L.append(f"| 패시지 처리량 | **{b['per_s']:.2f} 건/초** "
                 f"(텍스트당 {b['ms_per_text']:.0f} ms · {b['texts']}건 / {b['secs']:.2f}s) |")
    if d.get("enc_cpu"):
        L.append(f"| 인코더 컨테이너 CPU | {d['enc_cpu']} |")
    if d.get("rss_max_kb") is not None:
        L.append(f"| 최대 프로세스 RSS | **{d['rss_max_kb'] / 1024:.0f} MiB** |")
        L.append(f"| 전체 RSS 합 | {d['rss_sum_kb'] / 1024:.0f} MiB / 2048 MiB "
                 f"({d['rss_sum_kb'] / LIMIT_KB * 100:.0f}% · 프로세스 {d['rss_n']}개) |")
    L += ["",
          "⚠ **이 수치가 못 말하는 것**: 인코더 안의 CPU 시간만 따로 뗀 값(여기에는 컨테이너",
          "사이 왕복이 들어 있다) · 스케일다운을 부르는 유휴 시간(통제 안 함) · 동거(다른 것이",
          "같은 2GiB 에 상주할 때). Consumption 은 배포마다 하드웨어를 고정하지 않으므로 위의",
          "CPU 줄은 **이 런의 사실**이지 상수가 아니다.", ""]
    return L


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("hub", help="허브에서 찍은 출력")
    ap.add_argument("enc", nargs="?", help="인코더 컨테이너에서 찍은 출력(없어도 된다)")
    ap.add_argument("--title", default="컨테이너 안에서 잰 값")
    a = ap.parse_args(argv)

    hub = pathlib.Path(a.hub).read_text(encoding="utf-8", errors="replace")
    enc = (pathlib.Path(a.enc).read_text(encoding="utf-8", errors="replace")
           if a.enc and pathlib.Path(a.enc).exists() else "")
    d = parse(hub, enc)
    print("\n".join(render(d, a.title)))

    # 🔴 수치 없이 초록인 런은 이 리포에 실제로 있었다(`bake-probe.yml` 머리말).
    bad = []
    if not d["ok"]:
        bad.append("성공한 /encode 왕복이 0건이다 — 이 런은 지연을 재지 않았다")
    if not d["batches"]:
        bad.append("성공한 패시지 배치가 0건이다 — 처리량을 재지 않았다")
    for b in bad:
        print(f"::error::{b}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
