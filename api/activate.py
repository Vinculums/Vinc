# -*- coding: utf-8 -*-
"""Activate — make a slice of owned memory *portable* to an external AI.

P3 of the memory IA ("내 AI로 이식"). Given recall rows (owner-scoped, optionally
preference-ranked), build a **context pack** in two portable shapes:

  - a Markdown brief (``pack.md``) a human or any LLM can read directly;
  - a structured JSON (``pack.json``) for programmatic ingestion;

plus an **MCP resource manifest** (``mcp.json``) — the connector contract an MCP
server (mounted by weg-9000-orchestration) serves so a foreign AI can pull these
episodes as ``memory://{owner}/{id}`` resources.

Builders are pure (no I/O, no wall clock — ``generated_at`` is injected) so the
pack is deterministic and testable. ``activate`` orchestrates recall → pack and
optionally writes the three files.

    python -m api.cli activate --owner user:weg1456 --rank preference --out ./pack
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import query as q

MCP_SCHEMA_VERSION = "2024-11-05"
_URI_SCHEME = "memory"


def _uri(owner: str, episode_id: str) -> str:
    return f"{_URI_SCHEME}://{owner}/{episode_id}"


@dataclass
class ContextPack:
    owner: str
    query: str
    generated_at: str | None
    episodes: list[dict]
    learner_model: list[dict] = field(default_factory=list)  # how this person learns (U-patterns)

    # ── JSON shape (programmatic ingestion) ──────────────────────────────────
    def to_json(self) -> dict:
        return {
            "kind": "weg-9000-memory/context-pack",
            "owner": self.owner,
            "query": self.query,
            "generated_at": self.generated_at,
            "episode_count": len(self.episodes),
            "episodes": self.episodes,
            "learner_model": self.learner_model,
        }

    def _learner_section(self) -> list[str]:
        if not self.learner_model:
            return []
        out = [
            "",
            f"## 학습법 — how {self.owner} learns",
            "",
            "> 이 개인의 학습 방식(인지 지문). 외부 AI는 이 방식에 맞춰 나를 도와야 한다.",
            "",
        ]
        for p in self.learner_model:
            meta = []
            if p.get("domain"):
                meta.append(f"domain: {p['domain']}")
            if p.get("observations") is not None:
                meta.append(f"n={p['observations']}")
            head = f"- **{p.get('code', '')} {p.get('name', '')}**".rstrip()
            if meta:
                head += f" ({' · '.join(meta)})"
            out.append(head)
            desc = (p.get("description") or "").strip()
            if desc:
                out.append(f"  - {desc}")
        return out

    # ── Markdown brief (human / LLM-readable) ────────────────────────────────
    def to_markdown(self) -> str:
        parts = [
            f"# Memory Context Pack — {self.owner}",
            "",
            f"> query: \"{self.query}\" · generated: {self.generated_at or 'n/a'} "
            f"· {len(self.episodes)} episode(s) · {len(self.learner_model)} learning-pattern(s)",
            "> Portable memory brief for ingestion into an external AI. "
            "Source: weg-9000-memory.",
            "",
        ]
        if not self.episodes:
            parts.append("_(no episodes in scope)_")
        for ep in self.episodes:
            pin = " 📌" if ep.get("pinned") else ""
            tags = ep.get("tags") or []
            meta = [f"date: {ep.get('date', 'n/a')}"]
            if ep.get("domain"):
                meta.append(f"domain: {ep['domain']}")
            if tags:
                meta.append("tags: " + ", ".join(tags))
            if ep.get("score") is not None:
                meta.append(f"score: {ep['score']}")
            parts.append(f"## {ep.get('title') or ep.get('id', 'untitled')}{pin}")
            parts.append("- " + " · ".join(meta))
            summary = (ep.get("summary") or "").strip()
            if summary:
                parts += ["", summary]
            parts += ["", "---", ""]
        parts += self._learner_section()
        return "\n".join(parts) + "\n"

    # ── MCP resource manifest (connector contract) ───────────────────────────
    def to_mcp_manifest(self) -> dict:
        resources = [
            {
                "uri": _uri(self.owner, ep.get("id", "")),
                "name": ep.get("title") or ep.get("id", ""),
                "mimeType": "text/markdown",
                "description": (ep.get("summary") or "")[:120],
            }
            for ep in self.episodes
        ]
        if self.learner_model:
            resources.append({
                "uri": _uri(self.owner, "_learner"),
                "name": f"learning method ({self.owner})",
                "mimeType": "text/markdown",
                "description": f"{len(self.learner_model)} learning pattern(s) — how this person learns",
            })
        return {
            "schemaVersion": MCP_SCHEMA_VERSION,
            "owner": self.owner,
            "generated_at": self.generated_at,
            "resources": resources,
            "pack": {
                "uri": _uri(self.owner, "_pack"),
                "name": f"context pack ({self.owner})",
                "mimeType": "application/json",
                "description": f"{len(self.episodes)} episode(s), query=\"{self.query}\"",
            },
        }


def build_context_pack(rows: list[dict], *, owner: str, query: str = "",
                       generated_at: str | None = None,
                       learner_model: list[dict] | None = None) -> ContextPack:
    """Assemble a ContextPack from recall rows (pure). ``learner_model`` is the
    person's LearnerPattern rows (how they learn) — carried so an export is "my
    learning *and how I learn it*"."""
    episodes = [dict(r) for r in rows]
    return ContextPack(owner=owner, query=query, generated_at=generated_at,
                       episodes=episodes, learner_model=[dict(p) for p in (learner_model or [])])


def write_pack(pack: ContextPack, out_dir: str | Path) -> dict:
    """Write pack.md / pack.json / mcp.json into ``out_dir``. Returns the paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / "pack.md"
    json_path = out / "pack.json"
    mcp_path = out / "mcp.json"
    md_path.write_text(pack.to_markdown(), encoding="utf-8")
    json_path.write_text(json.dumps(pack.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    mcp_path.write_text(json.dumps(pack.to_mcp_manifest(), ensure_ascii=False, indent=2), encoding="utf-8")
    return {"markdown": str(md_path), "json": str(json_path), "mcp": str(mcp_path)}


# ── Public entry point ───────────────────────────────────────────────────────

def activate(query: str = "", *, owner: str, tenant: str | None = None,
             domain: str | None = None, workspace: str | None = None,
             tags: list[str] | None = None, rank: str = "preference", limit: int = 20,
             include_model: bool = True,
             generated_at: str | None = None, out_dir: str | Path | None = None,
             cfg=None) -> dict:
    """Recall an individual's learning and build a portable context pack.

    ``domain`` (free, user-defined — physics/math/art/…) optionally scopes the pack
    to one learning domain; omit to export across all of the owner's domains.
    ``include_model`` (default True) also exports the person's *learning method*
    (LearnerPattern rows) so the pack is "my learning *and how I learn it*".
    Defaults to ``rank="preference"`` — an export should lead with what the learner
    actually cares about. Writes the three files when ``out_dir`` is given,
    otherwise returns the pack inline."""
    res = q.recall(query, owner=owner, tenant=tenant, domain=domain,
                   workspace=workspace, tags=tags, rank=rank, limit=limit, cfg=cfg)
    model_rows = q.learner_model(owner=owner, cfg=cfg).get("rows", []) if include_model else []
    pack = build_context_pack(res.get("rows", []), owner=owner, query=query,
                              generated_at=generated_at, learner_model=model_rows)
    out: dict = {
        "owner": owner,
        "episode_count": len(pack.episodes),
        "learner_pattern_count": len(pack.learner_model),
        "executed": res.get("executed", False),
    }
    if out_dir is not None:
        out["written"] = write_pack(pack, out_dir)
    else:
        out["pack"] = {
            "markdown": pack.to_markdown(),
            "json": pack.to_json(),
            "mcp": pack.to_mcp_manifest(),
        }
    return out
