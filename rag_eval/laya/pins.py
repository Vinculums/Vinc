# -*- coding: utf-8 -*-
"""핀과 질문 문구. 바꾸면 측정을 다시 한다. 근거: [[vinc.plan.laya-test-method-2026-10-07 §1 §2]]"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import time

PACKAGE = "laya==0.3.28"
REPO = "convaiinnovations/laya-multilingual"
REVISION = "e4e9ddf21a7b1903b7acffd8814ad4307bf63a67"
SHA256 = {"model.safetensors": "9d628fd971b700382ac6f65920a86f149777b2e748e0c955fb3b19695aa8f204"}
BACKEND = "eager"
MAX_LEN = 1024
SEED = 20261007
TARGET_ERROR = 0.05
ASYMMETRY_ABSTAIN = 0.2
PREDICATES = ("supports", "contradicts", "same_as")
SYMMETRIC = ("contradicts", "same_as")

# 얼린 문구. 영어만(오너 확정 10-07). criteria 는 라벨 추종을 줄이는 뜻풀이다.
QUESTIONS = {
    "supports": {"type": "noul",
                 "instructions": "Does statement A give evidence or a reason for statement B?",
                 "criteria": {"true": "A provides a measurement, result or argument that makes B more credible.",
                              "false": "A is unrelated to B, merely restates B, or argues against B."}},
    "contradicts": {"type": "noul",
                    "instructions": "Do statements A and B make claims that cannot both be true?",
                    "criteria": {"true": "Accepting A requires rejecting B, including when the opposition is in meaning rather than in wording.",
                                 "false": "A and B can both hold, or they are about different things."}},
    "same_as": {"type": "noul",
                "instructions": "Do statements A and B make the same claim about the same thing?",
                "criteria": {"true": "A and B assert the same fact or decision, possibly in different words or languages.",
                             "false": "A and B differ in subject, scope, time or verdict, or one adds a claim the other lacks."}},
}

_HANGUL = re.compile(r"[가-힣]")


def render_state(a_text: str, b_text: str, context: str | None = None) -> str:
    s = f"A: {a_text.strip()}\nB: {b_text.strip()}"
    if context:
        s += f"\nContext: {context.strip()[:400]}"
    return s


def _lang_one(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    ratio = sum(1 for c in letters if _HANGUL.match(c)) / len(letters)
    return "ko" if ratio > 0.6 else "en" if ratio < 0.1 else "mixed"


def detect_lang(*texts: str) -> str:
    """쌍의 언어. 양쪽이 같은 언어면 그 언어, 한쪽이 한국어이고 다른 쪽이 영어면 mixed."""
    langs = {_lang_one(t) for t in texts if t and t.strip()}
    if not langs:
        return "en"
    return langs.pop() if len(langs) == 1 else "mixed"


def questions_hash() -> str:
    return hashlib.sha256(json.dumps(QUESTIONS, sort_keys=True).encode()).hexdigest()[:12]


def model_dir_sha256(model_dir: str) -> str | None:
    """로컬 체크포인트의 model.safetensors sha256. 미세조정 체크포인트(§7.1)를 핀 대신 적을 때 쓴다."""
    import os
    path = os.path.join(model_dir, "model.safetensors")
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def pin_header(model_dir: str | None = None) -> dict:
    """출력 파일 머리에 싣는 핀. torch/transformers 는 설치된 venv 에서만 읽힌다.

    model_dir 이 있으면 핀 체크포인트 대신 그 로컬 체크포인트(미세조정, §7.1)를 돌린 것이고,
    머리에 model_dir 와 그 safetensors sha256 을 함께 적어 어느 가중치의 수치인지 남긴다."""
    out = {"package": PACKAGE, "repo": REPO, "revision": REVISION, "sha256": SHA256,
           "backend": BACKEND, "max_len": MAX_LEN, "seed": SEED, "questions_hash": questions_hash(),
           "python": platform.python_version(), "machine": platform.platform(),
           "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if model_dir:
        out.update(model_dir=model_dir, model_dir_sha256=model_dir_sha256(model_dir),
                   note="model_dir overrides the pinned checkpoint; revision/sha256 above are the base it was tuned from")
    try:
        import laya  # noqa: F401
        import torch  # noqa: F401
        import transformers  # noqa: F401
        out.update(laya=laya.__version__, torch=torch.__version__, transformers=transformers.__version__,
                   cuda=bool(torch.cuda.is_available()))
    except Exception as exc:  # noqa: BLE001
        out["runtime"] = f"unavailable: {type(exc).__name__}"
    return out


def load_agent(model_dir: str | None = None):
    """핀대로 잠근 에이전트. revision 과 sha256 이 다르면 laya 가 거절한다.

    model_dir 을 주면 그 로컬 체크포인트(미세조정 결과)를 대신 올린다. 핀 검증은 없으므로
    출력 머리의 model_dir_sha256 이 그 자리를 대신한다."""
    import laya
    if model_dir:
        agent = laya.load(model_dir)
    else:
        agent = laya.load(REPO, revision=REVISION, expected_sha256=SHA256)
    try:
        agent.set_backend(BACKEND, strict=True)
    except TypeError:
        agent.set_backend(BACKEND)
    return agent
