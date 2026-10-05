"""본문 추출기의 후보 계약. 모델 실행·네트워크·저장은 이 모듈의 책임이 아니다."""
from __future__ import annotations

import hashlib
import json
import math

MAX_CANDIDATES = 24
MAX_SPANS = 16


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prepare_candidates(text: str, doc_id: str, candidates: list[dict], *,
                       require_approval: bool = False) -> list[dict]:
    """원문으로 재검증한 후보만 반환한다. 오프셋은 문서 전체의 Unicode 반개구간이다.

    extractor는 생산자가 선언한 provenance이며 모델 품질 인증이 아니다. 후보 id는
    문서·본문 버전·스팬·생산자에 묶인다. 제목을 사람이 편집해도 원문 근거는 보존한다.
    """
    if not isinstance(candidates, list) or len(candidates) > MAX_CANDIDATES:
        raise ValueError("invalid body candidates: expected at most 24 candidates")
    digest = source_hash(text)
    out, seen = [], set()
    for item in candidates:
        if not isinstance(item, dict) or item.get("source") != "body":
            raise ValueError("invalid body candidate: source must be body")
        if require_approval and item.get("approved") is not True:
            raise ValueError("body candidate requires explicit user approval")
        if item.get("doc_id") != doc_id or item.get("source_hash") != digest:
            raise ValueError("stale body candidate: document or source hash changed")
        title = item.get("title")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("invalid body candidate title")
        producer = item.get("extractor")
        if not isinstance(producer, dict) or any(
                not isinstance(producer.get(k), str) or not producer[k].strip()
                or len(producer[k]) > 200 for k in ("name", "version", "runtime")):
            raise ValueError("body candidate requires extractor name, version and runtime")
        producer = {k: producer[k].strip() for k in ("name", "version", "runtime")}
        raw_spans = item.get("spans")
        if not isinstance(raw_spans, list) or not 1 <= len(raw_spans) <= MAX_SPANS:
            raise ValueError("body candidate requires 1 to 16 source spans")
        spans = []
        for span in raw_spans:
            if not isinstance(span, dict):
                raise ValueError("invalid body candidate span")
            start, end = span.get("char_start"), span.get("char_end")
            if (type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(text)
                    or not isinstance(span.get("text"), str)
                    or text[start:end] != span["text"]):
                raise ValueError("body candidate span does not match canonical source")
            spans.append({"char_start": start, "char_end": end, "text": text[start:end]})
        spans = sorted({(s["char_start"], s["char_end"], s["text"]) for s in spans})
        spans = [{"char_start": s, "char_end": e, "text": value} for s, e, value in spans]
        label = item.get("label", "concept")
        if not isinstance(label, str) or not label.strip() or len(label) > 100:
            raise ValueError("invalid body candidate label")
        proof = {"doc_id": doc_id, "source_hash": digest, "spans": spans,
                 "extractor": producer, "label": label}
        cid = "body:" + source_hash(json.dumps(proof, ensure_ascii=False, sort_keys=True))
        if item.get("candidate_id") not in (None, cid):
            raise ValueError("body candidate id does not match source provenance")
        if cid in seen:
            raise ValueError("duplicate body candidate")
        seen.add(cid)
        score = item.get("score")
        if score is not None and (type(score) not in (float, int)
                                  or not math.isfinite(score) or not 0 <= score <= 1):
            raise ValueError("invalid body candidate score")
        first = spans[0]
        context = text[max(0, first["char_start"] - 60):min(len(text), first["char_end"] + 60)]
        out.append({**proof, "source": "body", "candidate_id": cid,
                    "title": title.strip(), "one_liner": context[:400],
                    "evidence": context, "offset_unit": "unicode_codepoint",
                    "score": score, "provenance_verified": "source-span-only",
                    "approved": item.get("approved") is True})
    return out


def score_spans(candidates: list[dict], gold: list[dict]) -> dict:
    """동일 문서/hash의 원문 스팬 gold와 정확 일치 대조. 실제 모델 실행은 하지 않는다."""
    def keys(rows):
        return {(c["doc_id"], c["source_hash"], s["char_start"], s["char_end"])
                for c in rows for s in c["spans"]}
    predicted, expected = keys(candidates), keys(gold)
    correct = len(predicted & expected)
    return {"predicted": len(predicted), "gold": len(expected), "correct": correct,
            "precision": correct / len(predicted) if predicted else None,
            "coverage": correct / len(expected) if expected else None,
            "metric": "exact_source_span", "fresh_model_quality": "not_run"}
