"""검색용 보정 프로필을 적용한다. 프로필이 없으면 기존 검색 결과를 보존한다."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path


def validate_profile(profile: dict) -> dict:
    """숫자만 가져오는 보정을 거절한다. 모델·런타임·평가 근거를 같이 고정한다."""
    if not isinstance(profile, dict) or type(profile.get("version")) is not int or profile["version"] != 1:
        raise ValueError("unsupported search relevance profile")
    for key in ("id", "encoder_model", "encoder_runtime", "corpus_revision"):
        if not isinstance(profile.get(key), str) or not profile[key].strip():
            raise ValueError(f"missing profile {key}")
    if not re.fullmatch(r"[0-9a-f]{64}", str(profile.get("evidence_sha256", ""))):
        raise ValueError("missing profile evidence_sha256")
    limits = profile.get("max_distance")
    if not isinstance(limits, dict) or set(limits) - {"chunk", "node"} or not limits:
        raise ValueError("profile needs separate chunk/node distance limits")
    for value in limits.values():
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise ValueError("profile distance must be numeric")
        if not math.isfinite(value) or not 0 <= value <= 2:
            raise ValueError("profile distance outside cosine range")
    if profile.get("status") not in {"experimental", "approved"}:
        raise ValueError("profile must disclose experimental/approved status")
    if 'owner_id' in profile and (not isinstance(profile['owner_id'], str) or not profile['owner_id'].strip()):
        raise ValueError('profile owner must be a nonempty string')
    return profile


def load_profile(path: str) -> dict:
    return validate_profile(json.loads(Path(path).read_text(encoding="utf-8")))


def profile_state(profile: dict, *, encoder_model: str,
                  query_runtime: str | None, corpus_revision: str | None = None,
                  runtime_warning: bool = False, owner: str | None = None) -> dict:
    """런타임이 확인되지 않은 벡터에 보정값을 적용하지 않는다."""
    profile = validate_profile(profile)
    state = "active"
    if profile["status"] != "approved":
        state = "experimental"
    elif profile.get('owner_id') and owner != profile['owner_id']:
        state = 'owner_mismatch'
    elif encoder_model != profile["encoder_model"]:
        state = "model_mismatch"
    elif not query_runtime or query_runtime != profile["encoder_runtime"] or runtime_warning:
        state = "runtime_unverified"
    elif not corpus_revision or corpus_revision != profile["corpus_revision"]:
        state = "corpus_unverified"
    return {"id": profile["id"], "state": state,
            "corpus_revision": profile["corpus_revision"],
            "evidence_sha256": profile["evidence_sha256"]}


def classify_rows(rows: list[dict], profile: dict, *, kind: str) -> list[dict]:
    """검색 순위·후보를 버리지 않고 raw distance와 팔 증거로 표식을 더한다.

    RRF 점수와 brief 격차는 확신도가 아니다. lexical/graph 결과는 유지한다.
    """
    out = []
    for source in rows:
        row = dict(source)
        arms = set(row.get("arms") or [])
        matched = row.get("matched_in")
        lexical = bool(arms & {"keyword", "title", "graph"}) or matched in {
            "title", "props", "graph"}
        evidence = {"level": "primary", "basis": "lexical_or_graph",
                    "profile_id": profile["id"]}
        if not lexical:
            limit = profile["max_distance"].get(kind)
            distance = row.get("distance")
            valid = (not isinstance(distance, bool)
                     and isinstance(distance, (float, int))
                     and math.isfinite(distance) and 0 <= distance <= 2)
            if limit is None or not valid:
                evidence.update(level="unclassified", basis="distance_unavailable")
            elif distance <= limit:
                evidence.update(basis="calibrated_vector", distance=distance)
            else:
                evidence.update(level="weak", basis="calibrated_vector", distance=distance)
        row["relevance"] = evidence
        out.append(row)
    return out
