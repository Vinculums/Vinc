# -*- coding: utf-8 -*-
"""질의 임베딩 창구의 클라이언트: 사이드카가 인코딩 창구(`vinc_rag/embed/worker.py` `serve_encode`)에
묻는 자리를 한 벌로 둔다.

  - 창구 호출 `_encode_call`·`_encode_post` 와 그 런타임·빌드 대조(`_ENCODER_RUNTIME`·`_ENCODER_BUILD`)
  - 응답이 함께 나르는 표식 ContextVar 넷(런타임·인스턴스·모델·ColBERT 토큰). 읽는 쪽은 같은
    요청 스레드다(`VincApp.read_search`·`read_brief` 가 `q.search`·`q.brief` 에 넘긴다)
  - `EncoderClient`: 질의 경로의 상태. 모델 이행 여부의 30초 캐시와 콜드스타트 상한 선택

`VincApp` 이 `EncoderClient` 하나를 `self.encoder` 로 들고 `_query_embedding` 등을 그대로 넘긴다.
노드 미러 재구축(`api/server.py`)도 `_encode_call`·`_encode_post`·`_colbert` 와 런타임 상수를 쓰는데,
그쪽은 `api.server` 가 재수출한 이름으로 읽는다.
"""
from __future__ import annotations

import contextvars
import json
import os
import sys
import threading
import time
import urllib.request

from store import request_timing
from store.encoder_runtime import fingerprint as encoder_fingerprint

from . import docstore

#: 쿼리 임베딩 대기 상한(초) — **정반대 두 고장을 다른 값으로** 다룬다.
#:
#:   짧은 쪽(`_QVEC_TIMEOUT`)은 인코더가 **바쁜** 경우다. 조회 요청 안에서 도는
#:   호출이라, 워커가 배경 임베딩으로 붐비면 기다리느니 FTS 로 답하는 편이 낫다
#:   (§11 P2 게이트).
#:   긴 쪽(`_QVEC_COLD_TIMEOUT`)은 인코더가 **없는** 경우다(레플리카 0에서 깨는 중).
#:   깨는 데는 오래 걸리지만 한 번 깨면 그 뒤 질의는 싸다 — 여기서 1.5초에 포기하면
#:   벡터 팔이 영영 안 켜진다.
#:
#: 둘을 가르는 신호는 **최근 성공 시각**이다(`EncoderClient.claim_cold_retry`). 긴 값을 무는
#: 질의는 한 질의당 한 번뿐이고, 인코더가 죽어 있으면 `_QVEC_COLD_COOLDOWN` 마다 하나이며
#: 나머지는 전부 `_QVEC_TIMEOUT` 에서 끊는다.
#:
#: ⚠ **이 값들은 총 상한이 아니라 소켓 연산당 상한이다.** `urlopen(timeout=T)` 은 연결에도
#: 매 read 에도 각각 T 를 주므로, 응답을 찔끔찔끔 흘리는 인코더에는 총 지연이 T 를 얼마든지
#: 넘는다(T=1.5 에서 10.2초 걸리고도 **예외 없이 성공**한다). 그러니 "1.5+30"을 상류 예산의
#: 천장으로 잡지 마라. 총 지연을 실제로 끊는 것은 브리지 요청 상한 하나뿐이고
#: (`vinc_mcp/bridge.py` `request(timeout=60)`) 거기 걸리면 강등이 아니라 호출 자체가
#: 실패한다 — 콜드 값을 키울 때 먼저 볼 곳이 그 60초다.
#:
#: ⚠ 기다림을 늘리는 값이므로 그 기다림이 보여야 한다 — 안 보이면 "가끔 느린 검색"이
#: 새 무음 결함이 된다. 지금 남는 곳은 **사이드카 stderr 한 줄뿐**이다(`EncoderClient.query_embedding`).
#: `vector_note` 로는 못 낸다 — 그 필드는 실패/런타임 불일치 경고이지 성공 지연 로그가 아니라서
#: (`vinc_mcp/tools.py`) 성공한 콜드 재시도에 붙이면 계약이 깨진다. 화면에 내려면 계약을
#: 먼저 고칠 것.
_QVEC_TIMEOUT = float(os.environ.get("VINC_EMBED_ENCODE_TIMEOUT", "1.5"))
_QVEC_COLD_TIMEOUT = float(os.environ.get("VINC_EMBED_ENCODE_COLD_TIMEOUT", "30"))
_QVEC_WARM_WINDOW = float(os.environ.get("VINC_EMBED_ENCODE_WARM_WINDOW", "300"))
_QVEC_COLD_COOLDOWN = float(os.environ.get("VINC_EMBED_ENCODE_COLD_COOLDOWN", "120"))


def _is_timeout(exc: BaseException) -> bool:
    """시간 초과인가. urlopen 은 두 모양으로 낸다 — 읽기는 `TimeoutError`
    (=`socket.timeout`), 연결은 그것을 `reason` 에 감싼 `URLError`. 연결 거부·401 은
    여기 해당하지 않는다(빨리 오는 답이라 다시 기다릴 이유가 없다)."""
    return isinstance(exc, TimeoutError) or isinstance(
        getattr(exc, "reason", None), TimeoutError)


def _colbert_rescore_on() -> bool:
    from vinc_rag.colbert import rescore_on
    return rescore_on()


def _colbert():
    """사이드카의 투영·코드북(재채점과 토큰 채우기가 같이 쓴다). 없으면 None 이고, 둘 다 꺼진다."""
    from vinc_rag.colbert import cached
    return cached()


_ENCODER_RESPONSE_RUNTIME = contextvars.ContextVar("encoder_response_runtime", default="")
#: 마지막 질의 인코딩이 함께 받은 ColBERT 토큰(packed f32). 재채점이 꺼졌거나 못 받았으면 "".
_ENCODER_RESPONSE_COLBERT = contextvars.ContextVar("encoder_response_colbert", default="")
_ENCODER_RESPONSE_INSTANCE = contextvars.ContextVar("encoder_response_instance", default="")
#: 창구가 답한 모델(`/encode` 의 `model`). 모델을 싣지 않는 창구면 빈 값이 온다.
_ENCODER_RESPONSE_MODEL = contextvars.ContextVar("encoder_response_model", default="")


_ENCODER_RUNTIME = encoder_fingerprint()  # 이미지 안에서는 프로세스 동안 판이 바뀌지 않는다.
_ENCODER_BUILD = os.environ.get("VINC_ENCODER_BUILD", "").strip()
_ENCODER_BUILD_REQUIRED = os.environ.get("VINC_ENCODER_BUILD_REQUIRED") == "on"


def _encode_post(url: str, texts: list[str], kind: str, timeout: float) -> list:
    """`_encode_call` 의 벡터만. 질의·미러 경로가 쓰는 원래 계약이다."""
    return _encode_call(url, texts, kind, timeout).get("vectors") or []


def _encode_call(url: str, texts: list[str], kind: str, timeout: float, *,
                 colbert: bool = False) -> dict:
    """인코딩 창구에 한 번 묻는다(`vinc_rag/embed/worker.py` `serve_encode`).

    ⚠ **토큰을 실어야 한다**(`VINC_EMBED_ENCODE_TOKEN`). 루프백 밖의 창구(`vinc-encode`)는 인증을
    요구한다. 토큰을 빠뜨린 경로는 질의 임베딩만 전부 401 로 떨어지고 배경 적재는 멀쩡해 보인다.
    그래서 창구를 부르는 자리를 여기 한 벌로 모았다.

    실패는 **던진다** — 부르는 쪽이 시간 초과 여부를 보고 다시 갈지 정한다."""
    payload = {"texts": list(texts), "kind": kind}
    if colbert:
        payload["colbert"] = True             # ColBERT 토큰을 같은 실행에서 함께
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    tok = os.environ.get("VINC_EMBED_ENCODE_TOKEN", "").strip()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url.rstrip("/") + "/encode", data=body,
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.load(resp) or {}
    actual_runtime = out.get("encoder_runtime")
    if (not actual_runtime or
            (_ENCODER_RUNTIME and actual_runtime != _ENCODER_RUNTIME) or
            (_ENCODER_BUILD_REQUIRED and not _ENCODER_RUNTIME)):
        raise RuntimeError(
            f"remote encoder runtime differs from hub: "
            f"remote={actual_runtime or 'unknown'}; hub={_ENCODER_RUNTIME}")
    actual_build = out.get("encoder_build")
    if (_ENCODER_BUILD_REQUIRED or _ENCODER_BUILD or actual_build) and (
            not _ENCODER_BUILD or actual_build != _ENCODER_BUILD):
        raise RuntimeError(
            f"remote encoder build differs from hub: "
            f"remote={actual_build or 'unknown'}; hub={_ENCODER_BUILD or 'unknown'}")
    _ENCODER_RESPONSE_RUNTIME.set(actual_runtime)
    _ENCODER_RESPONSE_INSTANCE.set(str(out.get("encoder_instance") or ""))
    _ENCODER_RESPONSE_MODEL.set(str(out.get("model") or ""))
    return out


class EncoderClient:
    """사이드카 하나의 질의 임베딩 상태: 모델 이행 여부의 30초 캐시와 콜드스타트 상한 선택.

    ``reader`` 는 스토어 리더 뷰를 여는 함수다(`VincApp._reader` 와 같은 약속: 못 열면 None).
    연 뷰는 이 클래스가 닫는다."""

    def __init__(self, reader):
        self._reader = reader
        self._mig_note = ""      # 임베딩 모델 이행 중 사유(빈 문자열 = 정상)
        self._mig_at = 0.0       # 그 계산 시각 — 30초 캐시(`embed_migrating`)
        self._mig_pending = 0    # 이행 중 남은 문서 수(GM-13 배지 진행도, note 와 동시 갱신)
        self._mig_stored = ""    # 저장 벡터의 모델(같은 캐시로 읽는다, 빈 값 = 모른다)
        # 쿼리 임베딩 창구 상태 — 콜드스타트와 '바쁨'을 가르는 재료(`claim_cold_retry`).
        # ⚠ 아래 셋은 전부 `time.monotonic()` 눈금이다(벽시계 아님). NTP 역방향 스텝이나
        # VM 재개가 시계를 밀면 '웜'·쿨다운 가름이 오류도 로그도 없이 뒤집힌다.
        self._qvec_ok = float("-inf")  # 마지막 성공 시각(-inf = 아직 한 번도 성공 못 했다)
        self._qvec_cold_until = 0.0  # 이 시각까지는 긴 상한을 쓰지 않는다(죽은 인코더 가드)
        self._qvec_cold_busy = False # 지금 긴 상한으로 기다리는 스레드가 있다
        self._qvec_lock = threading.Lock()

    @property
    def migration_pending(self) -> int:
        """이행 중 남은 문서 수. `embed_migrating` 이 사유와 함께 게시한 값이다."""
        return self._mig_pending

    def embed_migrating(self) -> str:
        """이행 중이면 사유 문구, 아니면 빈 문자열. **30초 캐시**한다.

        검색마다 스토어를 한 번 더 두드리면 그 자체가 조회 비용이다. 이행은 시간
        단위로 도는 일이라 30초 낡은 답이어도 판단이 바뀌지 않는다.

        ⚠ 지역 변수로 다 계산한 뒤 게시하고 `_mig_at` 을 **맨 끝에** 쓴다. 시각을 먼저
        쓰면 계산하는 사이 들어온 검색이 빈 사유(이행 아님)와 빈 저장 모델을 읽어, 이행
        중에도 벡터 팔을 쓰고 모델 대조도 건너뛴다. 그 창 안의 동시 호출은 대신 각자
        스토어를 읽는다(틀린 답보다 중복 읽기가 싸다).
        근거: [[vinc.audit.rag-code-review-2026-09-29]] 33번"""
        now = time.time()
        if now - self._mig_at < 30.0:
            return self._mig_note
        note, pending, stored = "", 0, ""
        try:
            r = self._reader()
            try:
                st = docstore.embed_model_state(r)
            finally:
                if r is not None:
                    r.close()
            stored = str(st.get("stored") or "")
            if st.get("migrating"):
                # 화면(GM-13 배지)은 이 영문 문장을 그대로 쓰지 않는다(em 대시 포함,
                # D38) — 구조화 진행도는 _mig_pending 이 따로 나른다.
                note = (f"embedding migration in progress "
                        f"({st['pending']} docs pending) — FTS only")
                pending = int(st.get("pending") or 0)
        except Exception:
            pass                      # 판단이 안 서면 막지 않는다(기본은 통과)
        self._mig_note, self._mig_pending, self._mig_stored = note, pending, stored
        self._mig_at = now            # 이 줄이 캐시를 연다. 위 줄보다 앞으로 옮기지 말 것
        return note

    @request_timing.timed("embed")
    def query_embedding(self, text: str):
        """쿼리 임베딩을 얻는다 — 없으면 `(None, 사유)` 로 **정직하게** 강등한다.

        임베딩 모델을 들고 있는 프로세스는 임베딩 워커뿐이다(사이드카엔 torch 가 없다).
        그래서 워커가 여는 창구(`vinc_rag/embed/worker.py` `serve_encode`)에 물어본다.
        `VINC_EMBED_ENCODE` 가 없으면 **시도조차 하지 않는다** — 데스크탑에는 워커가
        없고, 매 검색마다 연결 실패를 기다리는 건 그 자체로 성능 사고다.

        상한이 둘인 이유: 이 호출은 **조회 요청 안**에 있으므로 인코더가 바쁠 때는
        빨리 포기하는 게 맞지만(`_QVEC_TIMEOUT`), 인코더가 잠들어 있을 때는 한 번
        기다려 깨우는 게 맞다(`_QVEC_COLD_TIMEOUT`) — 깨고 나면 그 뒤가 전부 싸다.
        어느 쪽인지는 `claim_cold_retry` 가 최근 성공 시각으로 가른다. 강등은 응답
        메타(`vector_note`)에 남으므로 조용하지 않다."""
        _ENCODER_RESPONSE_COLBERT.set("")   # 앞 요청의 토큰이 이 스레드에 남지 않게
        _ENCODER_RESPONSE_RUNTIME.set("")
        _ENCODER_RESPONSE_MODEL.set("")
        url = os.environ.get("VINC_EMBED_ENCODE", "").strip()
        if not url or not text:
            return None, ""
        # 모델 이행 중에는 **벡터 팔을 아예 쓰지 않는다** — 반쪽 커버리지가 순위에
        # 끼면 "이미 처리된 문서" 쪽으로 결과가 기운다. FTS 로만 답하는 편이 정직하고,
        # 이행이 끝나면 저절로 풀린다([[vinc.plan.embedding-pipeline]]).
        mig = self.embed_migrating()
        if mig:
            return None, mig
        try:
            return self.encode_query(url, text, _QVEC_TIMEOUT)
        except Exception as exc:
            if not (_is_timeout(exc) and self.claim_cold_retry()):
                return None, f"{type(exc).__name__}: {str(exc)[:120]}"
        # 콜드 자격을 얻었다 — **한 번만** 길게 기다린다. 여기서도 실패하면 인코더가 죽은
        # 것으로 보고 쿨다운을 걸어, 뒤따르는 질의들이 같은 값을 물지 않게 한다.
        t0 = time.monotonic()
        try:
            out = self.encode_query(url, text, _QVEC_COLD_TIMEOUT)
            # 성공한 콜드 재시도는 어디에도 안 남으면 "가끔 30초 걸리는 검색"이 된다.
            # `vector_note` 는 못 쓴다 — 그 필드는 실패/런타임 불일치 경고이고
            # 여기서는 성공했다(`vinc_mcp/tools.py`). 화면에 보이게 하는 것은
            # 별건이고, 최소한 로그에는 남긴다.
            print(f"vinc: 질의 임베딩 콜드 재시도 성공 — "
                  f"{time.monotonic() - t0:.1f}s 기다렸다", file=sys.stderr, flush=True)
            return out
        except Exception as exc:
            with self._qvec_lock:
                self._qvec_cold_until = time.monotonic() + _QVEC_COLD_COOLDOWN
            return None, f"{type(exc).__name__}: {str(exc)[:120]}"
        finally:
            with self._qvec_lock:
                self._qvec_cold_busy = False

    def encode_query(self, url: str, text: str, timeout: float):
        """창구에 한 번 물어본다. 성공하면 `(벡터|None, 사유)`, 실패하면 **던진다** —
        바깥이 시간 초과 여부를 보고 긴 상한으로 한 번 더 갈지 정한다.

        ⚠ 창구는 `_encode_call`·`_encode_post` 로만 부른다. 토큰을 싣는 자리가 거기 한 벌이라,
        직접 부르면 질의 임베딩만 401 로 떨어진다(사유는 `vector_note` 에 남는다)."""
        _ENCODER_RESPONSE_COLBERT.set("")
        cb = _colbert() if _colbert_rescore_on() else None
        if cb is not None:
            # 재채점이 켜졌으면 같은 실행에서 토큰도 받는다(인코딩 한 번). 코드북이 다른 창구의
            # 토큰은 쓰지 않는다 — 그 질의는 재채점 없이 간다.
            out = _encode_call(url, [text], "query", timeout, colbert=True)
            vecs = out.get("vectors") or []
            toks = out.get("colbert") or []
            if out.get("colbert_books") == cb.id and len(toks) == 1:
                _ENCODER_RESPONSE_COLBERT.set(toks[0])
        else:
            vecs = _encode_post(url, [text], "query", timeout)
        self._qvec_ok = time.monotonic()  # 살아 있다 — 다음 초과는 '바쁨'으로 읽는다
        # 창구의 모델이 저장 벡터의 모델과 다르면 벡터 팔을 뺀다. 폭이 같으면 엔진은
        # 아무 오류 없이 다른 공간의 이웃을 낸다. 한쪽이라도 모르면(모델을 안 싣는 창구·기록 없음)
        # 막지 않는다. 저장 모델은 `embed_migrating` 의 30초 캐시가 읽어 둔 값이다.
        got, stored = _ENCODER_RESPONSE_MODEL.get(), self._mig_stored
        if got and stored and got != stored:
            return None, (f"query encoder model {got} differs from the stored vectors' "
                          f"model {stored}: full-text results only")
        return (list(vecs[0]), "") if vecs else (None, "encode returned no vector")

    def claim_cold_retry(self) -> bool:
        """짧은 상한이 시간 초과일 때 **긴 상한으로 한 번 더** 갈 자격을 준다.

        주는 조건 셋. (1) 웜 창(`_QVEC_WARM_WINDOW`) 안에 성공한 적이 없다 — 있으면
        인코더는 살아 있고 지금 초과는 '바쁨'이라 더 기다려도 손해다. (2) 직전 긴
        시도가 실패해 건 쿨다운이 지났다 — 인코더가 죽어 있는데 **모든** 질의가 긴
        상한을 물면 안 된다. (3) 긴 상한으로 기다리는 스레드가 지금 없다 — 동시 질의가
        다같이 기다리면 콜드스타트 한 번이 요청 전부를 세운다."""
        now = time.monotonic()
        with self._qvec_lock:
            if self._qvec_cold_busy or now < self._qvec_cold_until:
                return False
            if now - self._qvec_ok < _QVEC_WARM_WINDOW:
                return False
            self._qvec_cold_busy = True
            return True
