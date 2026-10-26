"""평가 카드: 역할 하나가 한 평가 묶음에서 어떻게 나왔는지를 결과 Record 하나로 남긴다.

카드는 역할에 붙는 Record 이고 `claim_kind: result` 다. 같은 역할·같은 묶음(suite)의 새 카드는
옛 카드를 덮어쓰지 않는다: 옛 카드에 `valid_until` 을 달고 `supersedes` 엣지로 잇는다. 과제 파일과
정답은 그래프에 넣지 않는다. 카드는 묶음을 id 와 해시로만 가리킨다. 모델 비용은 카드를 만드는
사람이 낸다(Vinc 가 모델을 부르지 않는다).

⚠ 카드는 **같은 지문(저장소·하네스 판·묶음 해시)에서만** 비교한다. 지문이 다른 두 카드의 수를
나란히 놓지 않는다.
근거: [[vinc.research.agent-harness-and-eval-without-a-model-api-2026-10-25]]
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
from typing import Any, Iterable, Mapping

CARD_PREFIX = "record:eval-card-"
CARD_TAG = "eval-card"
#: 실행 기록과 같은 토픽(예약 토픽 안, 도메인은 `vinc/` 아래).
DOMAIN = "vinc/agent/harness"
ROLE_PREFIX = "concept:agent-"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TEXT_MAX = 200
_RATES = ("pass_1", "pass_k", "violation", "fabrication")


def _day(value: Any) -> str:
    if value is None:
        return _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.date().isoformat() if isinstance(value, _dt.datetime) else value.isoformat()
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ValueError("date must be YYYY-MM-DD")
    return value


def _count(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _rate(value: Any, name: str) -> dict[str, Any]:
    """`{k, n}` 을 `{k, n, rate}` 로. n 이 0 이면 rate 는 None 이다(재지 못한 것이지 0 이 아니다)."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be {{k, n}}")
    k, n = _count(value.get("k"), f"{name}.k"), _count(value.get("n"), f"{name}.n")
    if k > n:
        raise ValueError(f"{name}.k cannot exceed {name}.n")
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None}


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > _TEXT_MAX:
        raise ValueError(f"{name} must be a non-empty string of at most {_TEXT_MAX} characters")
    return value.strip()


def check_suite(suite: Mapping[str, Any]) -> dict[str, Any]:
    """`{id, hash, tasks}`. 해시는 과제 파일의 sha256 이고 파일 자체는 어디에도 싣지 않는다."""
    if not isinstance(suite, Mapping):
        raise ValueError("suite must be {id, hash, tasks}")
    digest = suite.get("hash")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ValueError("suite.hash must be the lowercase sha256 of the task file")
    return {"id": _text(suite.get("id"), "suite.id"), "hash": digest,
            "tasks": _count(suite.get("tasks"), "suite.tasks")}


def check_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """칸 하나(어댑터 × 모델 계열)의 결과. 모르는 값은 None 으로 두고 지어내지 않는다."""
    if not isinstance(result, Mapping):
        raise ValueError("each result must be an object")
    out: dict[str, Any] = {"adapter": _text(result.get("adapter"), "adapter"),
                           "model_family": _text(result.get("model_family"), "model_family"),
                           "tasks": _count(result.get("tasks"), "tasks"),
                           "runs_per_task": _count(result.get("runs_per_task"), "runs_per_task")}
    if out["runs_per_task"] < 1:
        raise ValueError("runs_per_task must be at least 1")
    for key in ("pass_1", "pass_k"):
        out[key] = _rate(result.get(key), key)
    for key in ("violation", "fabrication"):
        out[key] = None if result.get(key) is None else _rate(result[key], key)
    cost = result.get("cost_per_success_usd")
    if cost is not None and (isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0):
        raise ValueError("cost_per_success_usd must be a non-negative number or None")
    out["cost_per_success_usd"] = None if cost is None else float(cost)
    return out


def card_id(role: str, suite: Mapping[str, Any], results: Iterable[Mapping[str, Any]], *,
            store_fingerprint: str, harness_version: str, date: str) -> str:
    """`record:eval-card-<역할>-<날짜>-<8 hex>`. 같은 입력은 같은 id 라서 재시도가 한 노드로 합쳐진다."""
    cells = sorted(f"{r['adapter']}|{r['model_family']}" for r in results)
    raw = "\n".join([role, suite["id"], suite["hash"], store_fingerprint, harness_version, date, *cells])
    slug = re.sub(r"[^a-z0-9]+", "-", role[len("concept:"):].lower()).strip("-")[:40].strip("-")
    return f"{CARD_PREFIX}{slug}-{date}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:8]}"


def build_eval_card_fragment(role: str, *, suite: Mapping[str, Any],
                             results: Iterable[Mapping[str, Any]], store_fingerprint: str,
                             harness_version: str, date: Any = None,
                             frontier: Iterable[Mapping[str, Any]] | None = None,
                             supersedes: Iterable[str] = (), domain: str | None = None,
                             title: str | None = None) -> dict[str, Any]:
    """Nodes and edges for ``POST /v1/fragments``: one eval card Record ``relates_to`` its role.

    ``supersedes`` are the ids of the role's current cards for the same suite. Each gets
    ``valid_until`` set to the new card's date (a Record column, so the rest of it is kept)
    and a ``supersedes`` edge from the new card; none is deleted. ``store_fingerprint`` and
    ``harness_version`` say what the numbers were measured on. ``frontier`` is the optional
    success-against-tokens points from harness search.
    """
    if not isinstance(role, str) or not role.startswith(ROLE_PREFIX):
        raise ValueError(f"role must be a role id starting with {ROLE_PREFIX!r}")
    suite_ref = check_suite(suite)
    cells = [check_result(r) for r in results]
    if not cells:
        raise ValueError("a card needs at least one result")
    if len({(c["adapter"], c["model_family"]) for c in cells}) != len(cells):
        raise ValueError("each adapter and model family appears once in a card")
    fingerprint = _text(store_fingerprint, "store_fingerprint")
    harness = _text(harness_version, "harness_version")
    day = _day(date)
    props: dict[str, Any] = {"claim_kind": "result", "role": role, "suite": suite_ref,
                             "date": day, "harness_version": harness,
                             "store_fingerprint": fingerprint, "results": cells}
    if frontier is not None:
        points = []
        for point in frontier:
            if not isinstance(point, Mapping):
                raise ValueError("each frontier point must be an object")
            success = point.get("success")
            tokens = point.get("tokens_in_mean")
            if (isinstance(success, bool) or not isinstance(success, (int, float))
                    or not 0 <= success <= 1):
                raise ValueError("frontier success must be a number from 0 to 1")
            if isinstance(tokens, bool) or not isinstance(tokens, (int, float)) or tokens < 0:
                raise ValueError("frontier tokens_in_mean must be a non-negative number")
            points.append({"label": _text(point.get("label"), "frontier label"),
                           "success": float(success), "tokens_in_mean": float(tokens)})
        props["frontier"] = points
    old = [str(i).strip() for i in supersedes if str(i).strip()]
    bad = [i for i in old if not i.startswith(CARD_PREFIX)]
    if bad:
        raise ValueError("supersedes must be eval card ids: " + ", ".join(bad))
    cid = card_id(role, suite_ref, cells, store_fingerprint=fingerprint, harness_version=harness,
                  date=day)
    old = [i for i in dict.fromkeys(old) if i != cid]
    names = ", ".join(sorted({c["model_family"] for c in cells}))
    node: dict[str, Any] = {
        "id": cid, "type": "record", "valid_from": day, "props": props, "tags": [CARD_TAG],
        "title": title or f"Eval card of {role[len('concept:'):]} on {suite_ref['id']} ({names}), {day}",
        "domain": domain or DOMAIN}
    nodes: list[dict[str, Any]] = [node, {"id": role, "type": "concept"}]
    edges: list[dict[str, Any]] = [{
        "from": cid, "to": role, "type": "relates_to",
        "evidence": f"Measured result of this role on suite {suite_ref['id']} "
                    f"(hash {suite_ref['hash'][:12]})."}]
    for previous in old:
        nodes.append({"id": previous, "type": "record", "valid_until": day})
        edges.append({"from": cid, "to": previous, "type": "supersedes",
                      "evidence": "A newer measurement of the same role on the same suite."})
    return {"nodes": nodes, "edges": edges}


def current_cards(client: Any, role: str, *, suite_id: str | None = None,
                  space: str | None = None) -> list[dict[str, Any]]:
    """The role's cards that nothing supersedes, newest first (``client.eval_cards``)."""
    got = client.eval_cards(role, space=space)
    cards = [c for c in got.get("cards") or [] if c.get("current")]
    if suite_id is not None:
        cards = [c for c in cards if (c.get("suite") or {}).get("id") == suite_id]
    return cards


def record_eval_card(client: Any, role: str, *, space: str | None = None, **card: Any) -> dict[str, Any]:
    """Read the role's current cards for the suite, then write the new card over them.

    ``card`` takes :func:`build_eval_card_fragment`'s keyword arguments except ``supersedes``.
    Needs a ``vinc_sk_`` key. The write goes to the key's own graph, so a rerun on another
    person's machine adds a card to that person's graph and touches nothing else.
    """
    if "supersedes" in card:
        raise TypeError("record_eval_card finds the cards to supersede itself")
    suite = check_suite(card.get("suite") or {})
    old = [c["id"] for c in current_cards(client, role, suite_id=suite["id"], space=space)]
    fragment = build_eval_card_fragment(role, supersedes=old, **card)
    return client.ingest(fragment["nodes"], fragment["edges"], space=space)
