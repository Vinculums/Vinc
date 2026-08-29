#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LongMemEval-S 하네스 — 표본을 재고, 전체 실행의 가격표를 뽑는다.

정본은 [[vinc.plan.0-9-0-open-decisions]] §3(판정 3: "먼저 측정한다, 발행은 나중").
이 파일은 그 §3.2 "공개된 쌍(disclosed pair)" 설계를 그대로 옮긴 것이고, 패키지 행은
[[vinc.plan.0-9-0-execution]] §3 의 W12 다.

**왜 이 도구가 있나.** 사람 결정 게이트 3은 "전체 실행에 돈을 쓸 것인가"다. 그 결정에
필요한 것은 의견이 아니라 **가격표**이고, 가격표의 미지수는 모델 품질이 아니라
**토큰 수**다. 토큰 수는 LLM 을 한 번도 부르지 않고 잴 수 있다 — 그래서 이 하네스는
LLM 없이 돌아가는 부분을 **실데이터로 전부 재고**, LLM 이 드는 부분은 재료만 두고
실행 경로를 열지 않는다(아래 표와 그 뒤 문단).

    단계                     LLM 필요?   상태
    0 표본 추출              아니오      실행
    1 적재(문서 경로)        아니오      실행 (vinc_doc_put)
    2 검색                   아니오      실행 (FTS 단독 = P1 이전의 정직한 기준선)
    1' 추출 적재             예          **미배선** — 재료만 있다(아래)
    2' 답변 생성             예          재료 있음(`llm()`), 실행 경로 미배선
    3 판정(judge)            예          재료 있음(`llm()`), 실행 경로 미배선

LLM 단계를 "구현했지만 안 돌렸다"가 아니라 **미배선**이라고 적는 이유: 키가 없는
세션에서 쓴 코드라 한 번도 실행돼 본 적이 없는데, 그 경로는 전체 실행에서 $668 을
쓴다. 실행해 본 적 없는 비싼 경로를 준비된 것처럼 내보내는 것이 이 리포가 가장 비싸게
배운 고장이라(무효 첫 평가 전례) `--arm extracted` 는 키가 있어도 **거절한다**.
프롬프트 3종은 지금도 모든 리포트에 해시로 박히므로 배선하는 사람이 이어받을 자리는 남아 있다.

§3.3 이 요구하는 기준선이 바로 2 다: "지금의 정직한 검색은 FTS 단독"이다. 임베더를
붙이지 않았으므로 `put_doc(embed=None)` 이고, 이 수치는 P1 이후 같은 명령으로 다시 재서
델타를 만드는 **before** 쪽이다.

**적재는 왜 문서 경로인가.** §3.2 는 "에피소드·기록은 vinc_ingest, 긴 재료는
vinc_doc_put" 이라고 적는다. 대화 세션 하나는 긴 재료이므로 doc_put 이 제 경로다.
세션에서 에피소드를 **추출**하는 쪽이 LLM 이 드는 부분이고, 그 자리가 `--arm extracted`
다(위 표대로 아직 거절한다). 기본이자 지금 도는 것은 `--arm docs` — 추출 없이 원문을 넣는다.

**질문마다 건초더미가 다르다.** 그래서 질문끼리 격리해야 하는데, 스토어를 500개
만드는 대신 **owner 를 질문마다 다르게** 준다(`lme:{qid}`). 이 격리는 이 리포가 이미
SEC-1 로 테스트하고 있는 것이라 새로 만들 물건이 아니다.

    # 데이터셋(265MB)은 리포 밖에 둔다. 받는 곳:
    #   https://huggingface.co/datasets/xiaowu0162/longmemeval  (파일명 longmemeval_s)
    python -X utf8 scripts/benchmark/longmemeval.py run \
        --dataset /path/longmemeval_s.json --n 50 --data /path/store --out report.json

    # 가격표만 다시 계산(이미 뽑은 report.json 에서)
    python -X utf8 scripts/benchmark/longmemeval.py price --report report.json

토큰 수는 키가 있으면 `/v1/messages/count_tokens` 로 **정확히** 세고, 없으면
문자수/`--chars-per-token` 로 추정한 뒤 리포트에 `exact: false` 라고 적는다. 추정을
정확한 값인 척하지 않는 것이 이 리포의 규율이다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# ── 하네스 버전 · 프롬프트 ────────────────────────────────────────────────────
#: 프롬프트가 바뀌면 수치도 바뀐다. §3.2 가 "프롬프트를 실행마다 고정해 기록하라"고
#: 요구하므로 여기 상수로 두고 해시를 리포트에 싣는다(별도 파일로 빼면 그 파일이
#: 조용히 어긋난다 — 같이 버전되는 유일한 방법이 같은 파일이다).
HARNESS_VERSION = "w12.1"

EXTRACT_PROMPT = (
    "You are the memory layer of an assistant. Read the conversation session below and "
    "write down what a user would want remembered later: durable facts, preferences, "
    "decisions, and events with their dates. Output JSON only, shaped as "
    '{"episodes":[{"title":..,"date":..,"summary":..}],'
    '"records":[{"title":..,"statement":..}]}. Omit small talk.'
)

ANSWER_PROMPT = (
    "Answer the question using ONLY the retrieved context below. If the context does not "
    "contain the answer, say exactly: I don't know. Be concise."
)

JUDGE_PROMPT = (
    "You compare a candidate answer against the reference answer for a memory benchmark. "
    "Reply with exactly one word: CORRECT or INCORRECT."
)

# ── 가격표 (스킬 캐시 2026-06-24 · $/1M 토큰) ────────────────────────────────
#: 이 표는 **파생값의 재료**다. 측정값은 토큰 수이고 달러는 이 표를 곱한 결과이므로,
#: 요율이 바뀌면 리포트를 다시 계산하면 된다(`price --report`). 요율을 안 믿겠으면
#: --rate-in/--rate-out 으로 덮는다.
RATES = {
    "claude-opus-5":   (5.0, 25.0),
    "claude-fable-5":  (10.0, 50.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
RATES_AS_OF = "2026-06-24"
#: Batch API 는 같은 작업을 절반 값에 돌린다. 500문항 벤치마크는 지연에 무감각한
#: 전형적인 배치 작업이라 이 할인이 그냥 적용된다 — 가격표에 같이 싣는 이유다.
BATCH_DISCOUNT = 0.5

#: 출력 토큰은 잴 수 없다(아직 안 돌렸으니까). **예산**으로 두고 리포트에 가정이라고
#: 적는다. 입력 토큰이 비용을 지배하므로 이 값의 오차는 총액을 크게 흔들지 않는다.
DEFAULT_BUDGETS = {"extract_out": 600, "answer_out": 200, "judge_out": 10}

#: 보고 유형 5종(§3.1). 원시 question_type 을 여기로 접는다.
def bucket(qid: str, raw_type: str) -> str:
    """원시 유형 → 보고 유형. 매핑이 없으면 원시값을 그대로 돌려 **드러낸다**.

    조용히 'other' 로 삼키면 데이터셋이 바뀐 것을 아무도 모른다. §3.1 이 유형별 보고를
    요구하므로 유형이 새로 생기면 리포트에서 눈에 띄어야 한다."""
    if qid.endswith("_abs"):
        return "abstention"
    if raw_type.startswith("single-session"):
        return "single-session"
    return raw_type


REPORTED_TYPES = ["single-session", "multi-session", "temporal-reasoning",
                  "knowledge-update", "abstention"]


# ── 토큰 세기 ────────────────────────────────────────────────────────────────
class Tokens:
    """정확히 세거나(키 있음), 문자수로 추정하거나(키 없음). **어느 쪽인지 기록한다.**

    `tiktoken` 을 쓰지 않는 이유는 그것이 다른 회사 토크나이저이기 때문이다. 정확한
    값은 `/v1/messages/count_tokens` 에서만 나온다."""

    def __init__(self, model: str, chars_per_token: float, api_key: str | None,
                 base_url: str):
        self.model, self.cpt = model, chars_per_token
        self.key, self.base = api_key, base_url.rstrip("/")
        self.exact = bool(api_key)
        self.calls = 0

    def calibrate(self, samples: list[str]) -> None:
        """실제 본문 몇 개로 문자/토큰 비를 **재서** cpt 를 갈아끼운다.

        세션마다 count_tokens 를 부르면 왕복이 수천 번이라 그럴 값이 없다. 몇 개로
        비율만 재고 나머지는 문자수로 환산하는 편이 같은 정확도를 훨씬 싸게 준다.
        키가 없으면 아무것도 하지 않는다(기본값이 그대로 남고 리포트가 그렇게 적는다)."""
        if not self.exact:
            return
        chars = sum(len(s) for s in samples)
        toks = sum(self(s) for s in samples)
        if toks:
            self.cpt = round(chars / toks, 3)

    def __call__(self, text: str) -> int:
        if not self.exact:
            return int(len(text) / self.cpt)
        try:
            body = {"model": self.model,
                    "messages": [{"role": "user", "content": text}]}
            out = _post(f"{self.base}/v1/messages/count_tokens", body, self.key)
            self.calls += 1
            return int(out["input_tokens"])
        except Exception:
            # 한 번이라도 실패하면 추정으로 내려가고 그 사실을 리포트에 남긴다.
            self.exact = False
            return int(len(text) / self.cpt)


def _post(url: str, body: dict, api_key: str, timeout: float = 60.0) -> dict:
    """stdlib 로 Anthropic API 호출. 이 venv 에는 requests·httpx·anthropic 이 없고,
    그것 하나 때문에 의존성을 늘릴 값이 없다(eval_search.py 도 urllib 를 쓴다)."""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json",
                 "x-api-key": api_key,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def llm(prompt: str, user: str, *, model: str, api_key: str, base_url: str,
        max_tokens: int) -> dict:
    """한 번의 messages 호출. 답변 단계와 판정 단계가 같이 쓴다.

    반환에 usage 를 그대로 실어 보낸다 — 실제로 돌린 실행의 비용은 **추정이 아니라
    청구된 토큰**으로 적어야 하기 때문이다."""
    out = _post(f"{base_url.rstrip('/')}/v1/messages", {
        "model": model, "max_tokens": max_tokens, "system": prompt,
        "messages": [{"role": "user", "content": user}],
    }, api_key)
    text = "".join(b.get("text", "") for b in out.get("content", [])
                   if b.get("type") == "text")
    return {"text": text, "usage": out.get("usage", {})}


# ── 0. 표본 ──────────────────────────────────────────────────────────────────
def load(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit(f"기대: 질문 배열. 실제: {type(data).__name__}")
    return data


def stratify(rows: list[dict], n: int, seed: int) -> list[dict]:
    """유형 분포를 모집단에 비례시켜 n개를 뽑는다(시드 고정 = 재현 가능).

    앞에서 n개를 자르지 않는 이유: 데이터셋이 유형별로 뭉쳐 있으면 표본이 한 유형으로
    쏠린다. §3.1 이 유형별 보고를 요구하는 이상 표본에 모든 유형이 있어야 한다."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(bucket(r["question_id"], r.get("question_type", "?")),
                          []).append(r)
    rng = random.Random(seed)
    picked: list[dict] = []
    # 큰 유형부터 배분하고, 반올림 손실은 마지막에 채운다.
    order = sorted(groups, key=lambda k: -len(groups[k]))
    for k in order:
        share = max(1, round(n * len(groups[k]) / len(rows)))
        picked.extend(rng.sample(groups[k], min(share, len(groups[k]))))
    rng.shuffle(picked)
    if len(picked) > n:
        return picked[:n]
    # 반올림으로 모자라면 채운다. **id 로 비교한다** — 질문 dict 는 건초더미를 통째로
    # 물고 있어서 `r not in picked` 는 거대한 dict 비교를 500번 돌린다.
    taken = {r["question_id"] for r in picked}
    rest = [r for r in rows if r["question_id"] not in taken]
    picked.extend(rng.sample(rest, min(n - len(picked), len(rest))))
    return picked


# ── 1. 적재 ──────────────────────────────────────────────────────────────────
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def doc_id_for(qid: str, sid: str) -> str:
    return f"lme.{_SAFE.sub('-', qid)}.{_SAFE.sub('-', str(sid))}"


def render(session: list[dict]) -> str:
    """세션을 텍스트로. 역할을 붙여 두어야 검색이 화자를 구분할 수 있다."""
    out = []
    for turn in session or []:
        role = turn.get("role", "?")
        out.append(f"{role}: {turn.get('content', '')}")
    return "\n".join(out)


def ingest(store, rows: list[dict], *, tok: Tokens, verbose: bool) -> dict:
    """질문마다 owner 를 갈라 건초더미를 적재한다. 반환은 **측정치**다."""
    from api import docstore

    t0 = time.time()
    docs = chars = 0
    per_q = []
    for i, r in enumerate(rows, 1):
        qid = r["question_id"]
        owner = f"lme:{qid}"
        sessions = r.get("haystack_sessions") or []
        sids = r.get("haystack_session_ids") or list(range(len(sessions)))
        dates = r.get("haystack_dates") or []
        q_chars = 0
        for j, sess in enumerate(sessions):
            text = render(sess)
            if not text.strip():
                continue
            docstore.put_doc(
                store, owner=owner, text=text,
                doc_id=doc_id_for(qid, sids[j] if j < len(sids) else j),
                title=f"session {sids[j] if j < len(sids) else j}",
                origin=dates[j] if j < len(dates) else "",
                embed=None,          # 벡터 없음 = FTS 단독(§3.3 의 P1 이전 기준선)
            )
            docs += 1
            q_chars += len(text)
        chars += q_chars
        per_q.append({"question_id": qid, "sessions": len(sessions),
                      "chars": q_chars, "tokens": int(q_chars / tok.cpt)})
        if verbose:
            print(f"  [{i}/{len(rows)}] {qid} · 세션 {len(sessions)} · {q_chars:,}자",
                  flush=True)
    return {"docs": docs, "chars": chars, "seconds": round(time.time() - t0, 1),
            "per_question": per_q}


# ── 2. 검색 ──────────────────────────────────────────────────────────────────
def retrieve(store, rows: list[dict], *, top_k: int, verbose: bool) -> dict:
    """질문을 검색하고 정답 세션이 상위 k 에 있었는지 본다.

    이것이 §3.3 의 기준선 수치다. 답변 품질이 아니라 **재료가 손에 들어오는가**를
    재는 것이라 LLM 이 필요 없고, P1 이 움직여야 하는 바로 그 숫자다."""
    from api import query as q

    t0 = time.time()
    out = []
    ctx_chars = 0
    for i, r in enumerate(rows, 1):
        qid = r["question_id"]
        owner = f"lme:{qid}"
        gold = {doc_id_for(qid, s) for s in (r.get("answer_session_ids") or [])}
        res = q.search(r["question"], top_k=top_k,
                       filters={"owner_id": owner}, store=store)
        ranked = [row.get("doc_id") for row in (res.get("rows") or [])]
        ctx_chars += sum(len(row.get("snippet") or "") for row in (res.get("rows") or []))
        rank = next((n for n, d in enumerate(ranked, 1) if d in gold), 0)
        out.append({
            "question_id": qid,
            "type": bucket(qid, r.get("question_type", "?")),
            "gold": len(gold), "returned": len(ranked),
            "rank": rank, "error": res.get("error"),
        })
        if verbose:
            print(f"  [{i}/{len(rows)}] {qid} · rank={rank or '-'}", flush=True)
    return {"seconds": round(time.time() - t0, 1), "context_chars": ctx_chars,
            "rows": out}


def score(rows: list[dict], top_k: int) -> dict:
    """유형별 recall@1/@k · MRR.

    ⚠ **기권(abstention) 문항의 recall 은 다른 뜻이다.** 실측(2026-08-29, longmemeval_s
    500문항): 기권 문항 30건도 `answer_session_ids` 를 **전부 갖고 있다**(30/30). 즉
    "정답 세션이 없다"가 아니라, 그 세션들은 주제만 같고 답은 담지 않은 쪽을 가리킨다 —
    기권 문항의 정답은 "모른다고 말하기"이고 그건 답변 단계에서 채점된다. 그래서 여기
    숫자는 유형별로 **따로** 내고 합산 해석하지 않는다(§3.1 이 유형별 보고를 요구하는
    이유이기도 하다).

    `gold == 0` 분기는 그래도 남긴다 — 데이터셋이 바뀌면 0으로 나눠 죽는 대신 그렇다고
    적어야 하고, 그 경우를 테스트가 못박고 있다."""
    def agg(sub: list[dict]) -> dict:
        gradable = [r for r in sub if r["gold"] > 0]
        if not gradable:
            return {"n": len(sub), "gradable": 0, "note": "정답 세션 없음(답변 단계 채점)"}
        n = len(gradable)
        return {
            "n": len(sub), "gradable": n,
            "recall@1": round(sum(1 for r in gradable if r["rank"] == 1) / n, 3),
            f"recall@{top_k}": round(sum(1 for r in gradable if r["rank"]) / n, 3),
            "mrr": round(sum(1 / r["rank"] for r in gradable if r["rank"]) / n, 3),
        }
    per_type = {t: agg([r for r in rows if r["type"] == t])
                for t in REPORTED_TYPES if any(r["type"] == t for r in rows)}
    # 매핑 밖 유형이 있으면 드러낸다.
    for t in sorted({r["type"] for r in rows} - set(REPORTED_TYPES)):
        per_type[t] = agg([r for r in rows if r["type"] == t])
    return {"overall": agg(rows), "by_type": per_type}


# ── 4. 가격표 ────────────────────────────────────────────────────────────────
def price(report: dict, *, model: str, total_questions: int,
          rate_in: float | None, rate_out: float | None,
          budgets: dict | None = None) -> dict:
    """표본의 실측 토큰에서 전체 실행 비용을 외삽한다.

    **무엇이 측정이고 무엇이 가정인지 갈라 적는다.** 입력 토큰은 실측(또는 문자수
    추정), 출력 토큰은 예산, 달러는 요율표를 곱한 파생값이다.

    ⚠ 답변 단계 입력은 **과소평가**다. 검색이 `detail="summary"` 라 발췌(snippet)
    크기로 쟀는데, 진짜 호스트는 유망한 문서를 `vinc_doc_get` 으로 다시 읽는다. 그런데
    이 항은 총액에서 0.5% 수준이라(추출 적재가 지배한다) 10배로 틀려도 결정이 안 바뀐다.
    바로잡으려면 retrieve 를 `detail="full"` 로 돌리면 된다 — 순위는 안 변하고 페이로드만
    커진다."""
    b = dict(DEFAULT_BUDGETS, **(budgets or {}))
    rin, rout = RATES.get(model, (None, None))
    rin = rate_in if rate_in is not None else rin
    rout = rate_out if rate_out is not None else rout
    if rin is None:
        raise SystemExit(f"{model} 요율을 모른다 — --rate-in/--rate-out 을 줄 것")

    ing = report["ingest"]
    ret = report["retrieve"]
    n = len(ing["per_question"])
    scale = total_questions / n

    hay_tok = sum(p["tokens"] for p in ing["per_question"])
    # 추출 호출은 **내용이 있는 세션**에만 든다. `sessions` 는 빈 세션까지 세는데
    # 실측(2026-08-29, 50문항)에서 2477 중 108개가 턴 0개라 그대로 쓰면 4.4% 과대계상된다.
    sessions = ing["docs"]
    sessions_incl_empty = sum(p["sessions"] for p in ing["per_question"])
    cpt = report["config"]["chars_per_token"]
    ctx_tok = int(ret["context_chars"] / cpt)

    def usd(tin: int, tout: int) -> float:
        return round(tin / 1e6 * rin + tout / 1e6 * rout, 2)

    # 팔 1: Vinc 경로(추출 적재 → 검색 → 답변 → 판정).
    # `calls` 를 같이 세는 이유: 총액만 보면 "돈만 내면 된다"로 읽히는데, 추출은
    # 세션마다 한 번이라 전체 실행이 **API 호출 2만5천 번**이다. 그건 요율한도·병렬·
    # 재시도 설계가 필요한 운영 문제이지 결제 문제가 아니다. 결정에 같이 든다.
    extract = {"in": hay_tok, "out": sessions * b["extract_out"], "calls": sessions}
    answer = {"in": ctx_tok + n * 100, "out": n * b["answer_out"], "calls": n}
    judge = {"in": n * 200, "out": n * b["judge_out"], "calls": n}
    # 팔 2: 호스트 단독 통제군(§3.3) — 메모리가 없으니 건초더미를 통째로 먹인다.
    host_only = {"in": hay_tok, "out": n * b["answer_out"], "calls": n}

    def arm(name: str, parts: list[dict]) -> dict:
        tin = sum(p["in"] for p in parts)
        tout = sum(p["out"] for p in parts)
        calls = sum(p["calls"] for p in parts)
        s = usd(tin, tout)
        return {"sample_in_tokens": tin, "sample_out_tokens": tout,
                "sample_calls": calls, "full_calls": int(calls * scale),
                "sample_usd": s,
                "full_usd": round(s * scale, 2),
                "full_usd_batch": round(s * scale * BATCH_DISCOUNT, 2)}

    return {
        "model": model, "rates_usd_per_mtok": {"in": rin, "out": rout},
        "rates_as_of": RATES_AS_OF,
        "sample_questions": n, "full_questions": total_questions, "scale": round(scale, 2),
        "token_counts_exact": report["config"]["tokens_exact"],
        "measured": {"haystack_tokens_sample": hay_tok,
                     "sessions_sample_nonempty": sessions,
                     "sessions_sample_incl_empty": sessions_incl_empty,
                     "retrieved_context_tokens_sample": ctx_tok},
        "assumed_output_budgets": b,
        "arms": {
            "vinc_extract_ingest": arm("extract", [extract]),
            "vinc_answer_and_judge": arm("answer", [answer, judge]),
            "vinc_total": arm("vinc", [extract, answer, judge]),
            "host_only_control": arm("host", [host_only]),
            "both_arms_total": arm("both", [extract, answer, judge, host_only]),
        },
        "wall_clock": {
            "sample_ingest_seconds": ing["seconds"],
            "sample_retrieve_seconds": ret["seconds"],
            "full_ingest_hours": round(ing["seconds"] * scale / 3600, 2),
            "full_retrieve_hours": round(ret["seconds"] * scale / 3600, 2),
            "note": "LLM 단계는 미실행이라 벽시계에 없다. API 왕복이 지배한다.",
        },
    }


# ── CLI ──────────────────────────────────────────────────────────────────────
def cmd_run(a: argparse.Namespace) -> int:
    os.environ.setdefault("MEMORY_STORE_BACKEND", "embedded")
    os.environ["MEMORY_DATA_DIR"] = a.data
    from store.config import load_config
    from store.backend import open_store

    key = os.environ.get("ANTHROPIC_API_KEY")
    if a.arm == "extracted":
        # 일 다 해 놓고 마지막에 죽지 않게 **먼저** 막는다. 그리고 키가 있어도 막는다 —
        # `ingest()` 는 arm 으로 분기하지 않으므로, 여기를 통과시키면 문서 경로를 돌려
        # 놓고 결과에 "extracted" 라고 적는다. **조용히 다른 것을 재는 것**이 이 리포가
        # 가장 비싸게 배운 고장이라(무효 첫 평가 전례) 반쪽 경로를 내보내느니 거절한다.
        # 배선은 남은 일이고, 그때 쓸 재료는 이미 여기 있다(`llm()` + 아래 프롬프트 3종,
        # 해시가 모든 리포트에 박힌다).
        raise SystemExit(
            "--arm extracted 는 아직 배선되지 않았다. 추출 적재는 LLM 호출당 비용이 있고"
            " 이 하네스는 그 경로를 한 번도 실행해 본 적이 없다(키 없는 세션에서 작성).\n"
            "지금 쓸 수 있는 것은 --arm docs 이고, 그것이 §3.3 의 P1 이전 기준선이다.")

    rows_all = load(Path(a.dataset))
    rows = stratify(rows_all, a.n, a.seed)
    tok = Tokens(a.model, a.chars_per_token, key, a.base_url)
    tok.calibrate([render(s) for s in (rows[0].get("haystack_sessions") or [])[:3]])

    cfg = load_config()
    store = open_store(cfg)
    store.provision(dim=cfg.embedding_dim)
    try:
        print(f"적재 {len(rows)}문항 → {a.data}", flush=True)
        ing = ingest(store, rows, tok=tok, verbose=a.verbose)
        print(f"검색 top_k={a.top_k}", flush=True)
        ret = retrieve(store, rows, top_k=a.top_k, verbose=a.verbose)
    finally:
        store.close()

    report = {
        "harness_version": HARNESS_VERSION,
        "config": {
            "dataset": os.path.basename(a.dataset),
            "dataset_questions": len(rows_all),
            "sample": len(rows), "seed": a.seed, "top_k": a.top_k, "arm": a.arm,
            "model_for_pricing": a.model,
            "chars_per_token": tok.cpt,          # 보정됐으면 보정값이 실린다
            "chars_per_token_calibrated": tok.exact,
            "tokens_exact": tok.exact,
            "prompt_hashes": {
                k: hashlib.sha256(v.encode()).hexdigest()[:12] for k, v in
                (("extract", EXTRACT_PROMPT), ("answer", ANSWER_PROMPT),
                 ("judge", JUDGE_PROMPT))},
            "llm_stages_run": False,
            "llm_skip_reason": None if key else "ANTHROPIC_API_KEY 없음",
        },
        "ingest": ing,
        "retrieve": ret,
        "retrieval_score": score(ret["rows"], a.top_k),
    }
    report["price"] = price(report, model=a.model, total_questions=a.full,
                            rate_in=a.rate_in, rate_out=a.rate_out)
    Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    _print_summary(report)
    return 0


def cmd_price(a: argparse.Namespace) -> int:
    report = json.loads(Path(a.report).read_text(encoding="utf-8"))
    report["price"] = price(report, model=a.model, total_questions=a.full,
                            rate_in=a.rate_in, rate_out=a.rate_out)
    Path(a.report).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    _print_summary(report)
    return 0


def _print_summary(report: dict) -> None:
    s, p = report["retrieval_score"], report["price"]
    print("\n── 검색 기준선 (FTS 단독) ──")
    print(f"  전체: {s['overall']}")
    for t, v in s["by_type"].items():
        print(f"  {t:22s} {v}")
    print("\n── 전체 실행 가격표 ──")
    print(f"  모델 {p['model']} · 요율 {p['rates_usd_per_mtok']} (as of {p['rates_as_of']})")
    print(f"  토큰 정확도: {'실측' if p['token_counts_exact'] else '문자수 추정'}")
    for k, v in p["arms"].items():
        print(f"  {k:24s} 표본 ${v['sample_usd']:>8} · 전체 ${v['full_usd']:>9}"
              f" · 배치 ${v['full_usd_batch']:>9} · 호출 {v['full_calls']:>7,}")
    w = p["wall_clock"]
    print(f"  적재 {w['sample_ingest_seconds']}s/50문항 → 전체 {w['full_ingest_hours']}h"
          f" · 검색 {w['sample_retrieve_seconds']}s → {w['full_retrieve_hours']}h")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--model", default="claude-opus-5")
        p.add_argument("--full", type=int, default=500, help="전체 실행 문항 수")
        p.add_argument("--rate-in", type=float, default=None)
        p.add_argument("--rate-out", type=float, default=None)

    r = sub.add_parser("run", help="표본 → 적재 → 검색 → 가격표")
    r.add_argument("--dataset", required=True)
    r.add_argument("--data", required=True, help="스토어 디렉터리(리포 밖에 둘 것)")
    r.add_argument("--out", default="longmemeval_report.json")
    r.add_argument("--n", type=int, default=50)
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--top-k", type=int, default=10)
    r.add_argument("--arm", choices=["docs", "extracted"], default="docs")
    r.add_argument("--chars-per-token", type=float, default=4.0)
    r.add_argument("--base-url", default=os.environ.get(
        "ANTHROPIC_BASE_URL", "https://api.anthropic.com"))
    r.add_argument("--verbose", action="store_true")
    common(r)
    r.set_defaults(fn=cmd_run)

    p = sub.add_parser("price", help="리포트에서 가격표만 다시 계산")
    p.add_argument("--report", required=True)
    common(p)
    p.set_defaults(fn=cmd_price)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
