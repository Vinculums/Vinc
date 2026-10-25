"""Shims: deterministic patches a harness adds because a model needs them today.

A shim is one Record, ``record:shim-<slug>``, attached with ``relates_to`` to the role it
patches, or to :data:`HARNESS_CONCEPT` when it applies to every role. ``valid_from`` is the
day it was installed; ``valid_until`` retires it. No node is written per firing: each run
record carries ``shims_fired {shim id: count}`` and ``model_family``, and the watcher here
turns zero firings over enough runs on every model family in use into a **draft** Decision.
A person ratifies it, only after the leave-one-out ablation row without that shim passes.
Nothing here calls a model.

이 파일은 표준 라이브러리만 쓰고 상대 import 가 없다. ``api/shim_registry.py`` 가 이
파일에서 그대로 생성되므로(``scripts/sync_shim_registry.py``) 여기에 패키지 의존을 넣지
말 것. 클라이언트는 덕 타이핑이다: ``edges``·``node``·``recall`` 메서드만 쓴다.
근거: [[decision:shims-are-records-retired-by-a-person-after-an-ablation-row-2026-10-25]]
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

log = logging.getLogger(__name__)

SHIM_PREFIX = "record:shim-"
#: 모든 역할에 걸리는 심이 붙는 개념. ``agent-`` 접두는 역할 몫이라 쓰지 않는다.
HARNESS_CONCEPT = "concept:vinc-agent-harness"
MECHANISMS = ("prompt_line", "nudge", "tool_filter", "gate")
#: ``applies_to`` 에서 이 이름들은 모델 계열이 아니라 역할의 rung 이다.
RUNGS = ("strongest", "strong", "fast", "cheapest")
RUN_TAG = "agent-run"
RUN_PREFIX = "episode:run-"
DRAFT = "draft"
#: 한 번의 엣지 읽기가 돌려주는 최대치. 잘리면 심을 놓칠 수 있으므로 멈춘다.
_EDGE_LIMIT = 5000
#: 엣지 묶음 읽기 한 번에 실을 수 있는 노드 수.
_BATCH = 100
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_DAY = re.compile(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?")


def _day(value: Any) -> str | None:
    """``YYYY[-MM[-DD]]`` (or a date) as ``YYYY-MM-DD``; a partial date is its first day."""
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    match = _DAY.match(str(value).strip())
    if not match:
        return None
    year, month, day = match.group(1), match.group(2) or "01", match.group(3) or "01"
    return f"{year}-{month}-{day}"


def _today(today: Any = None) -> str:
    if today is None:
        return _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    day = _day(today)
    if day is None:
        raise ValueError("today must be a date or YYYY-MM-DD")
    return day


def _texts(values: Iterable[str] | str | None, name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = [values]
    out = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must hold non-empty strings")
        out.append(value.strip())
    return tuple(dict.fromkeys(out))


@dataclass(frozen=True)
class Shim:
    """One shim as stored. ``attached_to`` are the roles (or the harness concept) it patches."""

    id: str
    patches: str
    mechanism: str
    applies_to: tuple[str, ...] = ()
    installed_because: str = ""
    measured_by: str | None = None
    text: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    attached_to: tuple[str, ...] = field(default_factory=tuple)
    title: str = ""
    domain: str | None = None

    @classmethod
    def from_node(cls, node: Mapping[str, Any], attached_to: Iterable[str] = ()) -> "Shim":
        """Read a stored shim Record; a record that is not a well-formed shim raises ValueError."""
        sid = str(node.get("id") or "")
        if not sid.startswith(SHIM_PREFIX):
            raise ValueError(f"{sid or 'a node'} is not a shim record")
        props = node.get("props") if isinstance(node.get("props"), Mapping) else {}
        mechanism = props.get("mechanism")
        if mechanism not in MECHANISMS:
            raise ValueError(f"{sid}: mechanism must be one of " + ", ".join(MECHANISMS))
        patches = props.get("patches")
        if not isinstance(patches, str) or not patches.strip():
            raise ValueError(f"{sid}: patches must name the behaviour it patches")
        text = props.get("text")
        return cls(id=sid, patches=patches.strip(), mechanism=mechanism,
                   applies_to=_texts(props.get("applies_to"), "applies_to"),
                   installed_because=str(props.get("installed_because") or ""),
                   measured_by=str(props["measured_by"]) if props.get("measured_by") else None,
                   text=text.strip() if isinstance(text, str) and text.strip() else None,
                   valid_from=_day(node.get("valid_from")),
                   valid_until=_day(node.get("valid_until")),
                   attached_to=tuple(sorted(set(attached_to))),
                   title=str(node.get("title") or ""),
                   domain=str(node["domain"]) if node.get("domain") else None)

    @property
    def slug(self) -> str:
        return self.id[len("record:"):]

    def is_current(self, today: Any = None) -> bool:
        """Installed by ``today`` and not retired: ``valid_from <= today < valid_until``."""
        day = _today(today)
        if self.valid_from and self.valid_from > day:
            return False
        return not self.valid_until or self.valid_until > day

    def families(self) -> tuple[str, ...]:
        """The model families ``applies_to`` names (rungs left out), lower-cased."""
        return tuple(v.casefold() for v in self.applies_to if v.casefold() not in RUNGS)

    def applies(self, model_family: str | None = None, rung: str | None = None) -> bool:
        """True when ``applies_to`` is empty, no selector is given, or it names one of them."""
        if not self.applies_to:
            return True
        wanted = {v.casefold() for v in (model_family, rung) if v}
        if not wanted:
            return True
        return any(v.casefold() in wanted for v in self.applies_to)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "patches": self.patches,
                "mechanism": self.mechanism, "applies_to": list(self.applies_to),
                "installed_because": self.installed_because, "measured_by": self.measured_by,
                "text": self.text, "valid_from": self.valid_from, "valid_until": self.valid_until,
                "attached_to": list(self.attached_to), "domain": self.domain}


def build_shim_fragment(slug: str, *, target: str, patches: str, mechanism: str,
                        applies_to: Iterable[str] = (), installed_because: str,
                        measured_by: str | None = None, text: str | None = None,
                        valid_from: str | None = None, title: str | None = None,
                        domain: str | None = None) -> dict[str, Any]:
    """Nodes and edges for ``POST /v1/fragments``: one shim Record ``relates_to`` its target.

    ``target`` is the role id it patches, or :data:`HARNESS_CONCEPT` for every role; it must
    already exist (the edge is skipped otherwise). ``installed_because`` is the incident
    record that justified it. ``text`` is the line a host adds, required for ``prompt_line``
    and ``nudge``. ``valid_from`` defaults to today (UTC), the install day.
    """
    if not isinstance(slug, str) or len(slug) > 80 or not _SLUG.fullmatch(slug):
        raise ValueError("slug must be lowercase words joined by '-', at most 80 characters")
    if not isinstance(target, str) or not target.startswith("concept:") or len(target) <= 8:
        raise ValueError("target must be a role id or the harness concept id (concept:...)")
    if mechanism not in MECHANISMS:
        raise ValueError("mechanism must be one of: " + ", ".join(MECHANISMS))
    if not isinstance(patches, str) or not patches.strip():
        raise ValueError("patches must name the behaviour the shim patches")
    if not isinstance(installed_because, str) or not installed_because.startswith("record:"):
        raise ValueError("installed_because must be the incident record id (record:...)")
    line = text.strip() if isinstance(text, str) else None
    if mechanism in ("prompt_line", "nudge") and not line:
        raise ValueError(f"a {mechanism} shim needs the text a host adds")
    if valid_from is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(valid_from)):
        raise ValueError("valid_from must be YYYY-MM-DD")
    day = _today(valid_from)
    sid = SHIM_PREFIX + slug
    props: dict[str, Any] = {"patches": patches.strip(), "mechanism": mechanism,
                             "applies_to": list(_texts(applies_to, "applies_to")),
                             "installed_because": installed_because}
    if measured_by:
        props["measured_by"] = str(measured_by)
    if line:
        props["text"] = line
    node: dict[str, Any] = {"id": sid, "type": "record", "valid_from": day, "props": props,
                            "title": title or f"Shim {slug} ({mechanism}): {patches.strip()}",
                            "tags": ["agent-shim"]}
    if domain:
        node["domain"] = domain
    edge = {"from": sid, "to": target, "type": "relates_to",
            "evidence": f"Patches {patches.strip()}; installed because of {installed_because}."}
    return {"nodes": [node], "edges": [edge]}


def attached_shims(client: Any, targets: Iterable[str], *,
                   optional: Iterable[str] = (HARNESS_CONCEPT,), today: Any = None,
                   space: str | None = None) -> list[Shim]:
    """Current shims attached to any of ``targets``, sorted by id.

    One edge read per 100 targets, then one node read per shim. A target that does not exist
    raises ``LookupError`` unless it is in ``optional`` (the harness concept may not exist in
    a graph yet); a record that is not a well-formed shim is logged and left out. Raises
    ``RuntimeError`` if an edge read was cut short.
    """
    wanted = list(dict.fromkeys(t for t in targets if t))
    day = _today(today)
    skip = set(optional)
    attached: dict[str, set[str]] = {}
    for offset in range(0, len(wanted), _BATCH):
        batch = wanted[offset:offset + _BATCH]
        result = client.edges(nodes=batch, type="ATTACHES_TO", dir="in", limit=_EDGE_LIMIT,
                              space=space)
        missing = [n for n in result.get("missing_nodes") or [] if n not in skip]
        if missing:
            raise LookupError("not found: " + ", ".join(str(n) for n in missing))
        if result.get("edges_truncated"):
            raise RuntimeError("more attached records than one edge read returns; "
                               "shims may be missing, so none are returned")
        for edge in result.get("edges") or []:
            source, target = str(edge.get("from") or ""), str(edge.get("to") or "")
            if source.startswith(SHIM_PREFIX) and target in batch:
                attached.setdefault(source, set()).add(target)
    shims = []
    for sid in sorted(attached):
        read = client.node(sid, space=space)
        if read.get("found") is False or not isinstance(read.get("node"), Mapping):
            continue
        try:
            shim = Shim.from_node(read["node"], attached[sid])
        except ValueError as exc:
            log.warning("vinc shim skipped: %s", exc)
            continue
        if shim.is_current(day):
            shims.append(shim)
    return shims


def current_shims(client: Any, role_id: str, *, model_family: str | None = None,
                  rung: str | None = None, today: Any = None, harness: str | None = HARNESS_CONCEPT,
                  space: str | None = None) -> list[Shim]:
    """The shims a run of ``role_id`` gets: attached to the role or to ``harness``, still
    valid ``today``, and whose ``applies_to`` is empty or names ``model_family`` (or ``rung``).
    ``model_family=None`` and ``rung=None`` keep every current shim."""
    if not isinstance(role_id, str) or not role_id.startswith("concept:"):
        raise ValueError("role_id must be a role id (concept:agent-...)")
    shims = attached_shims(client, [role_id, harness or ""], optional=[harness or ""],
                           today=today, space=space)
    return [s for s in shims if s.applies(model_family, rung)]


def shim_lines(shims: Iterable[Shim], *,
               mechanisms: Iterable[str] = ("prompt_line", "nudge")) -> list[str]:
    """The text of each ``prompt_line`` and ``nudge`` shim, in id order and without repeats.

    The host decides where each goes; keep them in the stable part of a prompt so two runs
    with the same shims share a cache prefix.
    """
    wanted = set(mechanisms)
    unknown = wanted - set(MECHANISMS)
    if unknown:
        raise ValueError("unknown mechanism: " + ", ".join(sorted(unknown)))
    lines: list[str] = []
    for shim in sorted(shims, key=lambda s: s.id):
        if shim.mechanism in wanted and shim.text and shim.text not in lines:
            lines.append(shim.text)
    return lines


def read_runs(client: Any, *, since: Any, tag: str = RUN_TAG, space: str | None = None,
              page_size: int = 200) -> list[dict[str, Any]]:
    """Run episodes dated ``since`` or later, newest first, each read whole (with ``props``).

    The episode timeline is newest first and returns no props, so it is paged until the dates
    fall before ``since`` and each run episode (``episode:run-...``) is then read by id.
    Only episodes tagged ``tag`` are seen; undated ones are left out.
    """
    start = _day(since)
    if start is None:
        raise ValueError("since must be a date or YYYY-MM-DD")
    if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 200:
        raise ValueError("page_size must be 1 to 200")
    runs: list[dict[str, Any]] = []
    cursor = None
    while True:
        page = client.recall(tags=[tag], limit=page_size, cursor=cursor, space=space)
        done = False
        for row in page.get("rows") or []:
            day = _day(row.get("date"))
            if day is None or day < start:
                done = True
                break
            rid = str(row.get("id") or "")
            if not rid.startswith(RUN_PREFIX):
                continue
            read = client.node(rid, space=space)
            node = read.get("node") if read.get("found") is not False else None
            if isinstance(node, Mapping) and isinstance(node.get("props"), Mapping):
                runs.append(dict(node))
        cursor = page.get("next_cursor")
        if done or not page.get("has_more") or not cursor:
            return runs


def _run_view(run: Mapping[str, Any]) -> tuple[str | None, Mapping[str, Any]]:
    props = run.get("props") if isinstance(run.get("props"), Mapping) else run
    return _day(run.get("date") or props.get("date")), props


def _fired(props: Mapping[str, Any], shim: Shim) -> int:
    counts = props.get("shims_fired")
    if not isinstance(counts, Mapping):
        return 0
    total = 0
    for key in (shim.id, shim.slug):
        value = counts.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            total += value
    return total


def retirement_drafts(shims: Iterable[Shim], runs: Iterable[Mapping[str, Any]], *,
                      window_start: Any, min_runs: int, families_in_use: Iterable[str],
                      harness: str = HARNESS_CONCEPT) -> list[dict[str, Any]]:
    """Draft Decisions proposing to retire shims that stopped firing. Pure: reads nothing.

    A shim is proposed when, on every family in ``families_in_use`` that it applies to, the
    runs that load it (its role's runs, or every run for a harness shim) dated from
    ``window_start`` (or its install day, if later) number at least ``min_runs`` and fired it
    zero times. ``runs`` are run episodes (``props`` and ``date``) or bare run props carrying
    ``date``. Each draft is a fragment: one Decision with ``props.state`` ``"draft"`` and the
    per-family counts, ``about`` what the shim is attached to. It is not a ratification.
    """
    start = _day(window_start)
    if start is None:
        raise ValueError("window_start must be a date or YYYY-MM-DD")
    if not isinstance(min_runs, int) or isinstance(min_runs, bool) or min_runs < 1:
        raise ValueError("min_runs must be a positive integer")
    families = list(dict.fromkeys(f.casefold() for f in _texts(families_in_use, "families_in_use")))
    if not families:
        raise ValueError("families_in_use must name at least one model family")
    views = [_run_view(run) for run in runs]
    drafts = []
    for shim in sorted(shims, key=lambda s: s.id):
        if not shim.attached_to:
            continue
        since = max(start, shim.valid_from) if shim.valid_from else start
        named = shim.families()
        check = [f for f in families if not named or f in named]
        if not check:
            continue
        counts = {f: {"runs": 0, "firings": 0} for f in check}
        everyone = harness in shim.attached_to
        for day, props in views:
            family = str(props.get("model_family") or "").casefold()
            if day is None or day < since or family not in counts:
                continue
            if not everyone and props.get("role") not in shim.attached_to:
                continue
            counts[family]["runs"] += 1
            counts[family]["firings"] += _fired(props, shim)
        if any(c["runs"] < min_runs or c["firings"] for c in counts.values()):
            continue
        drafts.append(_draft(shim, counts, since, min_runs))
    return drafts


def _draft(shim: Shim, counts: dict[str, dict[str, int]], since: str,
           min_runs: int) -> dict[str, Any]:
    total = sum(c["runs"] for c in counts.values())
    per = ", ".join(f"{f} {c['runs']}" for f, c in counts.items())
    did = "decision:retire-" + shim.slug
    node: dict[str, Any] = {
        "id": did, "type": "decision",
        "title": f"Draft: retire {shim.id}, which fired 0 times in {total} runs "
                 f"({per}) since {since}",
        "props": {"state": DRAFT, "proposes": "retire", "shim": shim.id,
                  "proposed_by": "shim watcher", "mechanism": shim.mechanism,
                  "patches": shim.patches, "installed_because": shim.installed_because,
                  "window_start": since, "min_runs": min_runs, "counts": counts,
                  "ratify_when": "the leave-one-out ablation row without this shim passes; "
                                 "a person ratifies, and the shim then gets valid_until"},
        "tags": ["shim-retirement"]}
    if shim.domain:
        node["domain"] = shim.domain
    evidence = (f"{shim.id} fired 0 times in {total} runs ({per}) since {since}; "
                "it stays until a person ratifies after its ablation row passes.")
    edges = [{"from": did, "to": target, "type": "about", "evidence": evidence}
             for target in shim.attached_to]
    return {"nodes": [node], "edges": edges}


__all__ = ["SHIM_PREFIX", "HARNESS_CONCEPT", "MECHANISMS", "RUN_TAG", "Shim",
           "build_shim_fragment", "attached_shims", "current_shims", "shim_lines", "read_runs",
           "retirement_drafts"]
