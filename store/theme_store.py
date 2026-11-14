# -*- coding: utf-8 -*-
"""Theme / Type 어휘 스토어 — 주제별 사용자 저작 타입 어휘 (D11-R2 · THESIS T3·T9).

**어휘는 해석의 *틀*이지 해석의 *내용*(노드·엣지)이 아니다** — 그래서 그래프
(GraphStore→LadybugDB)가 아니라 별도 주제 메타(`<data_dir>.themes.json` — DB 파일 형제)에 둔다.
어휘 편집이 그래프 데이터를 건드리지 않게 분리하는 것이 요점(IMPL §3).

원자적 쓰기(tempfile + os.replace)는 `_save` 가 한다: 같은 디렉터리의 임시 파일에 다 쓴 뒤
os.replace 로 옮기는 방식이라 `store/atomicjson.py::write_json_0600` 과 같은 모양이다.
모든 뮤테이션은 사이드카 잡 큐(`api/jobs.py` run_sync)로
직렬화되어 호출되므로(R4) 쓰기 경합이 없다 — 리더는 파일을 직접 읽고, os.replace 가
완전한 이전/이후 스냅샷을 보장한다.

**전역 enum 은 죽지 않고 강등된다:** 구 노드/엣지 타입 이름(`LEGACY_*`)은 검증에
쓰이지 않고, 새 주제 생성 시 `provenance:system_draft, status:proposed` 초안으로만
시드된다(T9 — 백지 부담 흡수, 법이 아니라 제안).

**재가 경계(불변):** ratify/reject/rename 은 *어휘 파일만* 고친다. 이미 MERGE 된
노드·엣지의 type 문자열은 재작성하지 않는다 — 어휘 편집이 그래프를 안 건드린다는
IMPL §3 지시의 직접 구현. 결과: reject/rename 후 기존 인스턴스는 원 type 을 유지한다.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from store.querybase import now_iso

# 구 전역 enum(D11 R1) — 폐기되지 않고 **기본 초안(system_draft)**으로 지위만 강등.
# 이름만 산다: 검증 게이트로 절대 쓰이지 않고, create_theme 시 시드 소스로만 참조된다.
# (물리 라벨·id 컬럼 바인딩은 api/fragment.py 소관 — 어휘와 분리.)
LEGACY_NODE_TYPES: tuple[str, ...] = ("concept", "domain", "bridge", "episode")
LEGACY_EDGE_TYPES: tuple[str, ...] = ("relates_to", "prerequisite_of",
                                      "derives_from", "connects_via")

def owner_theme_id(owner: str) -> str:
    """이 소유자의 어휘 테마 id. 결정론이라 저장할 필요가 없다.

    테마는 파일 하나에 평평하게 들어 있고 소유 열이 없다. 공유 허브에서는 그래서
    **활성 테마 하나를 모든 테넌트가 나눠 쓴다** — 남의 theme_id 를 실어 보내면 그
    사람 어휘에 내 타입이 꽂히고(감사 M1), 실어 보내지 않아도 제안이 한 통에 고인다.

    id 를 소유자에서 파생하면 열을 늘리지 않고 그 둘이 다 닫힌다. 해시를 쓰는 것은
    소유자 문자열이 계정 식별자라 테마 이름으로 화면·파일에 눕히지 않으려는 것이다.
    ⚠ **로컬은 이 경로를 타지 않는다.** 기존 "default" 테마에 쌓인 어휘가 있고,
    id 를 바꾸면 그것이 통째로 안 보이게 된다. 강제 owner(호스티드)에서만 쓴다.
    """
    if not owner:
        return "default"
    return "owner-" + hashlib.sha256(owner.encode("utf-8")).hexdigest()[:12]


_STORE_VERSION = 1
# 어휘 메타는 **DB 파일의 형제**(`<data_dir>.themes.json`)로 둔다. `cfg.data_dir` 는
# 임베디드 DB **파일** 경로다(kuzu 0.11.3 `Database(path)` 는 디렉토리를 거부·파일 생성 —
# embedded_backend.py:143) → data_dir 안에 파일을 넣을 수 없다. 형제 파일이라 DB 와
# 충돌하지 않고 백업 시 같은 부모 디렉토리에서 발견된다.
_SUFFIX = ".themes.json"

# 상태·출처 어휘(D11-R2 §2).
STATUS_AUTHORED = "authored"
STATUS_PROPOSED = "proposed"
PROV_USER = "user"
PROV_SYSTEM_DRAFT = "system_draft"
PROV_INGEST_PROPOSED = "ingest_proposed"


def _make_type(name: str, kind: str, *, scope: str, status: str, provenance: str) -> dict:
    return {"name": name, "kind": kind, "scope": scope,
            "status": status, "provenance": provenance}


def _seed_vocabulary() -> dict:
    """구 전역 enum → system_draft/proposed 초안 어휘(T9). scope=shared —
    D11-R2 §7-1('구 enum → scope:shared, status:proposed, provenance:system_draft')."""
    node = {n: _make_type(n, "node", scope="shared", status=STATUS_PROPOSED,
                          provenance=PROV_SYSTEM_DRAFT) for n in LEGACY_NODE_TYPES}
    edge = {n: _make_type(n, "edge", scope="shared", status=STATUS_PROPOSED,
                          provenance=PROV_SYSTEM_DRAFT) for n in LEGACY_EDGE_TYPES}
    return {"node": node, "edge": edge}


def _empty_state() -> dict:
    return {"version": _STORE_VERSION, "active_theme_id": None, "themes": {}}


class ThemeStore:
    """`<data_dir>.themes.json`(DB 파일 형제) 어휘 스토어. `data_dir=None` 이면 인메모리 강등
    (무 크래시 — store=None 강등과 평행). 뮤테이션은 잡 큐로 직렬화 호출 전제."""

    def __init__(self, data_dir: str | os.PathLike | None = None):
        self._data_dir = Path(data_dir) if data_dir else None
        self._mem: dict | None = None if self._data_dir else _empty_state()

    # ── 영속 ──────────────────────────────────────────────────────────────
    @property
    def _path(self) -> Path | None:
        # DB 파일의 형제 파일(`<data_dir>.themes.json`). data_dir 안에 넣지 않는다
        # (data_dir 는 kuzu DB 파일이라 그 안에 파일 생성 불가 — 상단 주석 참조).
        return Path(str(self._data_dir) + _SUFFIX) if self._data_dir else None

    def _load(self) -> dict:
        if self._data_dir is None:
            return copy.deepcopy(self._mem)  # type: ignore[arg-type]
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
        except (FileNotFoundError, json.JSONDecodeError):
            return _empty_state()

    def _save(self, state: dict) -> None:
        if self._data_dir is None:
            self._mem = copy.deepcopy(state)
            return
        path = self._path
        path.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".themes.", suffix=".tmp")  # type: ignore[union-attr]
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise

    # ── 주제 CRUD ─────────────────────────────────────────────────────────
    def create_theme(self, theme_id: str, title: str = "",
                     guiding_questions: list[str] | None = None,
                     *, created_at: str | None = None) -> dict:
        """주제 생성 + 구 enum 초안 시드(T9). 멱등 — 이미 있으면 기존을 그대로 반환.

        guiding_questions 는 **선택**(T9 — 필수 아님, 백지 부담 회피). 초안 질문
        제안은 호스트(Claude)가 대화로 채우도록 열어둔다 — 스토어는 값을 받기만 한다.
        """
        if not theme_id or not isinstance(theme_id, str):
            raise ValueError("theme_id required (non-empty str)")
        state = self._load()
        if theme_id in state["themes"]:
            return state["themes"][theme_id]
        state["themes"][theme_id] = {
            "theme_id": theme_id,
            "title": title or theme_id,
            "guiding_questions": list(guiding_questions or []),
            "created_at": created_at or now_iso(),
            "vocabulary": _seed_vocabulary(),
        }
        if state.get("active_theme_id") is None:
            state["active_theme_id"] = theme_id
        self._save(state)
        return state["themes"][theme_id]

    def delete_theme(self, theme_id: str) -> bool:
        """주제 하나를 어휘째 지운다 — 계정 삭제 캐스케이드의 어휘 파일 몫(판정 D11).
        없으면 False 를 돌려주고 파일을 건드리지 않는다. 활성 주제였다면 남은 것 중
        하나로(없으면 None) 넘긴다."""
        if not theme_id or not isinstance(theme_id, str):
            return False
        state = self._load()
        if theme_id not in state["themes"]:
            return False
        del state["themes"][theme_id]
        if state.get("active_theme_id") == theme_id:
            state["active_theme_id"] = next(iter(state["themes"]), None)
        self._save(state)
        return True

    def list_themes(self) -> list[dict]:
        return list(self._load()["themes"].values())

    def get_theme(self, theme_id: str) -> dict | None:
        return self._load()["themes"].get(theme_id)

    def has_theme(self, theme_id: str) -> bool:
        return theme_id in self._load()["themes"]

    def activate_theme(self, theme_id: str) -> bool:
        """활성 주제 지정. 존재하지 않으면 False(무 변경)."""
        state = self._load()
        if theme_id not in state["themes"]:
            return False
        state["active_theme_id"] = theme_id
        self._save(state)
        return True

    def active_theme_id(self) -> str | None:
        return self._load().get("active_theme_id")

    # ── 타입 어휘 ──────────────────────────────────────────────────────────
    @staticmethod
    def _bucket(kind: str) -> str:
        if kind not in ("node", "edge"):
            raise ValueError(f"kind must be 'node' or 'edge', got {kind!r}")
        return kind

    def resolve_type(self, theme_id: str, kind: str, name: str) -> dict | None:
        """주제 어휘에서 (kind, name) 조회. 없으면 None(=어휘 밖)."""
        theme = self.get_theme(theme_id)
        if theme is None:
            return None
        return theme["vocabulary"][self._bucket(kind)].get(name)

    def propose_type(self, theme_id: str, kind: str, name: str) -> bool:
        """어휘 밖 타입을 status:proposed, provenance:ingest_proposed 로 편입.
        신규 편입이면 True, 이미 있으면 False(재편입 멱등 — 정보 손실 없이 살린다).
        주제가 없으면 자동 생성하지 않는다 → ingest 는 유효 theme_id 전제(§4)."""
        state = self._load()
        theme = state["themes"].get(theme_id)
        if theme is None:
            raise KeyError(f"unknown theme_id: {theme_id!r}")
        bucket = theme["vocabulary"][self._bucket(kind)]
        if name in bucket:
            return False
        bucket[name] = _make_type(name, kind, scope="theme",
                                  status=STATUS_PROPOSED, provenance=PROV_INGEST_PROPOSED)
        self._save(state)
        return True

    def list_types(self, theme_id: str, kind: str | None = None,
                   status: str | None = None) -> list[dict]:
        theme = self.get_theme(theme_id)
        if theme is None:
            return []
        buckets = (kind,) if kind else ("node", "edge")
        out: list[dict] = []
        for b in buckets:
            for t in theme["vocabulary"][b].values():
                if status is None or t["status"] == status:
                    out.append(t)
        return out

    def _find_kind(self, theme: dict, name: str, kind: str | None) -> str | None:
        """kind 미지정 시 name 이 속한 버킷을 유일하게 결정. 모호/부재면 None."""
        if kind:
            return kind if name in theme["vocabulary"][self._bucket(kind)] else None
        hits = [b for b in ("node", "edge") if name in theme["vocabulary"][b]]
        return hits[0] if len(hits) == 1 else None

    def ratify(self, theme_id: str, name: str, kind: str | None = None) -> dict:
        """proposed → authored. 사용자 재가(호스트 측 승인 전제)."""
        state = self._load()
        theme = state["themes"].get(theme_id)
        if theme is None:
            raise KeyError(f"unknown theme_id: {theme_id!r}")
        b = self._find_kind(theme, name, kind)
        if b is None:
            raise KeyError(f"type not found (or ambiguous kind): {name!r}")
        t = theme["vocabulary"][b][name]
        t["status"] = STATUS_AUTHORED
        self._save(state)
        return t

    def reject(self, theme_id: str, name: str, kind: str | None = None) -> bool:
        """어휘에서 제거. 그래프 인스턴스의 type 문자열은 건드리지 않는다(경계)."""
        state = self._load()
        theme = state["themes"].get(theme_id)
        if theme is None:
            raise KeyError(f"unknown theme_id: {theme_id!r}")
        b = self._find_kind(theme, name, kind)
        if b is None:
            return False
        del theme["vocabulary"][b][name]
        self._save(state)
        return True

    def rename(self, theme_id: str, from_name: str, to_name: str,
               kind: str | None = None) -> dict:
        """from 제거 + to 를 authored(new)로 저작. 그래프 인스턴스 미변경(경계)."""
        state = self._load()
        theme = state["themes"].get(theme_id)
        if theme is None:
            raise KeyError(f"unknown theme_id: {theme_id!r}")
        b = self._find_kind(theme, from_name, kind)
        if b is None:
            raise KeyError(f"type not found (or ambiguous kind): {from_name!r}")
        del theme["vocabulary"][b][from_name]
        renamed = _make_type(to_name, b, scope="theme",
                             status=STATUS_AUTHORED, provenance=PROV_USER)
        theme["vocabulary"][b][to_name] = renamed
        self._save(state)
        return renamed
