"""새 실험용 명시적 기권 계약. 기존 보정 파일과 보고서에는 연결하지 않는다."""
from __future__ import annotations

import math

from .pins import ASYMMETRY_ABSTAIN


def validate_policy(policy):
    if policy.get("schema") != "vinc.laya.abstention.v1":
        raise ValueError("unsupported abstention schema")
    mode = policy.get("mode")
    cut = policy.get("min_confidence")
    if mode == "abstain_all":
        if cut is not None or not policy.get("reason"):
            raise ValueError("abstain_all needs null cut and reason")
    elif mode == "threshold":
        if type(cut) not in (float, int) or not math.isfinite(cut) or not 0.5 <= cut <= 1:
            raise ValueError("finite confidence cut in [0.5, 1] required")
    else:
        raise ValueError("explicit threshold or abstain_all required")
    return policy


def empirical_policy(probabilities, labels, *, target_error=0.05, min_bucket_n=30):
    """종전 경험적 위험 계산을 유지하되 안전 문턱이 없으면 숫자로 위장하지 않는다."""
    if len(probabilities) != len(labels) or not 0 <= target_error < 1 or type(min_bucket_n) is not int or min_bucket_n < 1:
        raise ValueError("invalid fit inputs")
    if any(type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities):
        raise ValueError("invalid probability")
    if any(type(y) is not int or y not in (0, 1) for y in labels):
        raise ValueError("hard labels required")
    policy = {"schema": "vinc.laya.abstention.v1", "mode": "abstain_all", "min_confidence": None,
              "reason": "insufficient_bucket" if len(labels) < min_bucket_n else "no_safe_cut",
              "n": len(labels), "accepted": 0, "target_error": target_error, "confidence_decimals": 4,
              "formal_risk_guarantee": False}
    if len(labels) < min_bucket_n:
        return policy
    points = [(round(max(p, 1-p), 4), int((p >= 0.5) != bool(y))) for p, y in zip(probabilities, labels)]
    for cut in sorted({c for c, _ in points}):
        selected = [e for c, e in points if c >= cut]
        risk = (sum(selected) + 1) / (len(selected) + 1)
        if risk <= target_error:
            policy.update(mode="threshold", min_confidence=cut, reason="empirical_cut", accepted=len(selected), empirical_risk=risk)
            break
    return policy


def decide(probability, policy, *, symmetric=False, reverse_probability=None):
    validate_policy(policy)
    if type(probability) not in (int, float) or not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError("invalid probability")
    if policy["mode"] == "abstain_all":
        return {"accepted": False, "reason": policy["reason"]}
    if symmetric:
        if reverse_probability is None:
            return {"accepted": False, "reason": "missing_reverse"}
        if type(reverse_probability) not in (int, float) or not math.isfinite(reverse_probability) or not 0 <= reverse_probability <= 1:
            raise ValueError("invalid reverse probability")
        if abs(probability-reverse_probability) > ASYMMETRY_ABSTAIN:
            return {"accepted": False, "reason": "asymmetry"}
    accepted = round(max(probability, 1-probability), 4) >= policy["min_confidence"]
    return {"accepted": accepted, "reason": "accepted" if accepted else "below_threshold"}
