# -*- coding: utf-8 -*-
"""Query API — read-only entry points over the memory store.

Three entry points sit on top of the ingestion engines:
    recall  : episodic memory retrieval (Episode nodes, time-ordered)
    search  : hybrid vector + BM25 retrieval over Chunk nodes
    decide  : decision support — graph traversal over policy/concept relations

Each entry point builds a deterministic *query plan* (testable without a
database) and, when the store is reachable, executes it.
"""
from __future__ import annotations

from .query import QueryPlan, decide, recall, search

__all__ = ["QueryPlan", "recall", "search", "decide"]
