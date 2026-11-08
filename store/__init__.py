# -*- coding: utf-8 -*-
"""Store layer — 임베디드(LadybugDB/Kùzu) 스토어 추상화·스키마·프로비저닝.

7 엔진과 `api/query.py` 는 드라이버를 직접 열지 않고 `backend.open_store` 를 통한다
(설계 §4-1 — 단일 지점). 스키마는 선행(테이블 DDL, `embedded_schema`); `provision`
이 멱등 적용한다. 백엔드는 지금 임베디드 하나다(외부 엔진 어댑터는 `backend.GraphStore` 뒤).
"""
from __future__ import annotations

from .config import StoreConfig, load_config
from .backend import GraphStore, open_store
from .embedded_schema import render_embedded_ddl

__all__ = ["StoreConfig", "load_config", "GraphStore", "open_store", "render_embedded_ddl"]
