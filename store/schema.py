# -*- coding: utf-8 -*-
"""Neo4j schema — constraints + indexes the 6 engines assume exist.

Each engine writes nodes/relationships; none of them creates the uniqueness
constraints or the vector/fulltext indexes that make ingestion idempotent and
search fast. This module is the single source of truth for that schema, as an
ordered list of migrations. ``{dim}`` in the vector index is substituted with
the configured embedding dimension at provision time.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    id: str
    description: str
    cypher: str


# Node-key / uniqueness constraints (idempotent MERGE targets for each engine).
_CONSTRAINTS = [
    ("c_policy_id", "Policy.id unique", "Policy", "id"),
    ("c_screen_id", "Screen.id unique", "Screen", "id"),
    ("c_reference_id", "Reference.id unique", "Reference", "id"),
    ("c_gate_id", "Gate.id unique", "Gate", "id"),
    ("c_chunk_id", "Chunk.id unique (embed_pipeline)", "Chunk", "id"),
    ("c_episode_id", "Episode.episode_id unique (recall_to_neo4j)", "Episode", "episode_id"),
    ("c_concept_id", "Concept.concept_id unique (concepts_to_neo4j)", "Concept", "concept_id"),
    ("c_bridge_id", "Bridge.bridge_id unique (bridges_to_neo4j)", "Bridge", "bridge_id"),
    ("c_skillexec_id", "SkillExecution.exec_id unique (skill_tracker)", "SkillExecution", "exec_id"),
    ("c_domain_name", "Domain.name unique (bridges_to_neo4j)", "Domain", "name"),
    ("c_learnerpattern_id", "LearnerPattern.pattern_id unique (learner_to_neo4j)", "LearnerPattern", "pattern_id"),
]


def _constraint_migrations() -> list[Migration]:
    out = []
    for mid, desc, label, prop in _CONSTRAINTS:
        cypher = (
            f"CREATE CONSTRAINT {mid} IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
        )
        out.append(Migration(id=mid, description=desc, cypher=cypher))
    return out


# Secondary (non-unique) lookup indexes. The memory these back is an individual's
# cross-domain *learning*: an Episode/Concept is owned by a person (``owner_id``)
# and belongs to a ``domain`` — a **free, user-defined** string (physics, math,
# art, economics, finance, … whatever that person chose to learn), never a fixed
# enum. ``tenant_id`` is the governance boundary. All are plain B-tree indexes —
# many nodes share an owner/domain, so these are NOT uniqueness constraints.
_INDEXES = [
    ("i_episode_owner", "index on Episode.owner_id (individual learner scope)", "Episode", "owner_id"),
    ("i_episode_domain", "index on Episode.domain (open, user-defined learning domain)", "Episode", "domain"),
    ("i_episode_tenant", "index on Episode.tenant_id (tenant governance scope)", "Episode", "tenant_id"),
    ("i_concept_owner", "index on Concept.owner_id (individual learner scope)", "Concept", "owner_id"),
    ("i_concept_domain", "index on Concept.domain (open, user-defined learning domain)", "Concept", "domain"),
    # A LearnerPattern is *how* a person learns (their U-patterns) — the most
    # personal data of all. Owned by the individual; scoped-by-owner on export.
    ("i_learnerpattern_owner", "index on LearnerPattern.owner_id (personal learning-method model)", "LearnerPattern", "owner_id"),
]


def _index_migrations() -> list[Migration]:
    out = []
    for mid, desc, label, prop in _INDEXES:
        cypher = f"CREATE INDEX {mid} IF NOT EXISTS FOR (n:{label}) ON (n.{prop})"
        out.append(Migration(id=mid, description=desc, cypher=cypher))
    return out


# Vector index over Chunk.embedding (cosine). {dim} substituted at provision.
_VECTOR_INDEX = Migration(
    id="i_chunk_embedding",
    description="vector index on Chunk.embedding (cosine, {dim} dims)",
    cypher=(
        "CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS "
        "FOR (c:Chunk) ON (c.embedding) "
        "OPTIONS {indexConfig: {"
        "`vector.dimensions`: {dim}, "
        "`vector.similarity_function`: 'cosine'}}"
    ),
)

# Fulltext (BM25) index over Chunk.text — used by hybrid_search.
_FULLTEXT_INDEX = Migration(
    id="i_chunk_text_ft",
    description="fulltext index on Chunk.text (BM25)",
    cypher="CREATE FULLTEXT INDEX chunk_text_ft IF NOT EXISTS FOR (c:Chunk) ON EACH [c.text]",
)


MIGRATIONS: list[Migration] = (
    _constraint_migrations() + _index_migrations() + [_VECTOR_INDEX, _FULLTEXT_INDEX]
)


def render(migrations: list[Migration], *, dim: int) -> list[str]:
    """Render migration Cypher with the embedding dimension substituted."""
    return [m.cypher.replace("{dim}", str(dim)) for m in migrations]
