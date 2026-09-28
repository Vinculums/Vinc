"""Build the one fragment that records an Episode about existing nodes."""
from __future__ import annotations

import datetime as _dt
import re
import secrets
from typing import Any, Iterable

#: An episode may be ``about`` a concept or a decision; the graph refuses other pairs.
ABOUT_PREFIXES = ("concept:", "decision:")


def check_about(about: Iterable[str]) -> list[str]:
    """Return the ids, refusing any that cannot be the target of an ``about`` edge."""
    ids = [str(a).strip() for a in about if str(a).strip()]
    bad = [a for a in ids if not a.startswith(ABOUT_PREFIXES)]
    if bad:
        raise ValueError("an episode can only be about a concept or a decision id: "
                         + ", ".join(bad))
    return list(dict.fromkeys(ids))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].strip("-")


def episode_id_for(title: str, date: str) -> str:
    """``episode:<date>-<slug>-<6 hex>``. The suffix keeps two same-day episodes
    with the same title from merging into one node."""
    slug = _slug(title) or "episode"
    return f"episode:{date}-{slug}-{secrets.token_hex(3)}"


def build_episode_fragment(title: str, *, summary: str = "", about: Iterable[str] = (),
                           date: str | None = None, domain: str | None = None,
                           tags: Iterable[str] | None = None,
                           episode_id: str | None = None) -> dict[str, Any]:
    """Nodes and edges for ``POST /v1/fragments``.

    The ``about`` targets travel in the same payload as ``{id, type}`` only:
    edges resolve between nodes merged in the same call, and a node sent
    without ``title``, ``props`` or ``tags`` keeps the values it already has.
    Callers must check that the targets exist first; an unknown id would be
    created as an empty node.
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("an episode needs a title")
    targets = check_about(about)
    date = date or _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("date must be YYYY-MM-DD")
    eid = episode_id or episode_id_for(title, date)
    if not eid.startswith("episode:"):
        raise ValueError("episode_id must start with 'episode:'")
    episode: dict[str, Any] = {"id": eid, "type": "episode", "title": title, "date": date}
    if summary:
        episode["summary"] = summary
    if domain:
        episode["domain"] = domain
    if tags is not None:
        episode["tags"] = [str(t) for t in tags]
    nodes = [episode] + [{"id": t, "type": t.split(":", 1)[0]} for t in targets]
    edges = [{"from": eid, "to": t, "type": "about"} for t in targets]
    return {"nodes": nodes, "edges": edges}
