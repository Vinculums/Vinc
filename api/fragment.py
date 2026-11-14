# -*- coding: utf-8 -*-
"""MCP graph fragment MERGE — `vinc_ingest` 의 서버측 계약 구현.

상류가 저작한 fragment(nodes+edges)를 **id 정확 매칭**으로만 MERGE 한다 — 유사도·의미
판단은 하지 않는다. 계약 정본은 [[vinc.canon.mcp-contract]], 이 경로가 데이터플레인에서
서는 자리는 [[vinc.spec.dataplane §3]].

- 노드 6종(record|concept|domain|bridge|episode|decision) + 자유 props{} → JSON 열.
  **의미층 = concept | decision** 이라 엣지 규칙에서 서로 바꿔 쓸 수 있다. 예외 하나:
  `relates_to` 는 개념↔개념에만 남는다([[vinc.spec.dataplane]]).
- 엣지: 의미층 4종 · connects_via(Bridge→) · about(Episode|Decision→) ·
  Record→의미층은 ATTACHES_TO{type} 로 원 타입을 보존해 수용.
- **개방층**: 6종 밖 타입은 스킵되지 않고 `Entity` 캐리어로, 양 끝이 개방 타입인 엣지는
  `TYPED{type}` 로 MERGE 된다(정보 손실 0). 어휘 밖 타입은 proposed 로 편입되고 응답
  `proposed_types` 로 재가를 유도한다. 휴면 엔진이 아니라 이 라이브 경로에 포팅한 이유는
  revstamp·SEC-3·dedupe·partial 관례를 **단일 쓰기 표면**에서 승계하기 위해서다.

안정성 계약(고쳐 놓은 것을 되돌리지 말 것):
- **partial**: warnings 가 하나라도 있으면 `partial:True` 를 얹는다 — `executed:True` 만
  보고 전량 성공으로 오인하는 것을 막는다.
- **RELATED_TO 무방향 dedupe**: 역방향이 이미 있으면 새로 만들지 않는다(양방향 재적재로
  degree 가 부풀지 않게). 그 재전송이 실어 온 근거·신뢰도는 저장된 엣지에 쓴다.
  방향성 있는 엣지에는 적용하지 않고, ATTACHES_TO 는 끝점 타입이 비대칭이라 구조적으로
  불필요하며(원 타입별로 따로 MERGE 한다), TYPED 는 사용자 저작 타입이라 방향을 추론할
  수 없어 exact 방향 dedupe 만 한다.
"""
from __future__ import annotations

import datetime
import json
import re

from engines import domain_hierarchy

from . import node_schema
from . import revstamp
from . import role_contract
from store import pkns, write_batch
from store import querybase as _qb
from store.querybase import _props_to_obj, domain_names
from store.topic_conversion_guard import guard_active
# 근거 열을 가진 rel 의 정본 — 스키마가 표를 갖고 여기서는 읽기만 한다.
from store.embedded_schema import EDGE_EVIDENCE_RELS, wiki_pairs_open

# 노드 종류·엣지 타입·필드 표의 정본은 `api/node_schema.py` 다. 아래 사설 이름은 같은
# 객체를 가리키며 이 모듈 안의 호출과 그 이름을 읽는 테스트가 쓴다. 값은 거기서 고친다.
_NODE_MAP = node_schema.NODE_MAP
_CC_EDGES = node_schema.CC_EDGES
_EC_EDGES = node_schema.EC_EDGES
_SUPERSEDES_EDGE, _SUPERSEDES_REL = node_schema.SUPERSEDES_EDGE, node_schema.SUPERSEDES_REL
_SUPERSEDES_KINDS = node_schema.SUPERSEDES_KINDS
_OWNER_SCOPED_LABELS = node_schema.OWNER_SCOPED_LABELS
_KEPT_FIELDS = node_schema.KEPT_FIELDS
_CONTRACT_FIELDS = node_schema.CONTRACT_FIELDS
_INTERVAL_FIELDS = node_schema.INTERVAL_FIELDS
# 주제 경로를 접는 두 함수의 정본도 `api/node_schema.py` 다. 읽는 쪽(`decide_read`·`diagnose`·`cli_gate`)은
# 이 모듈을 거치지 않고 거기서 바로 부른다.
_split_subject = node_schema.split_subject
_norm_subject_path = node_schema.normalize_subject_path

# 끝점 (라벨, PK) 두 쌍 — 엣지 경로가 해석한 결과를 들고 다니는 형태.
_Ends = tuple[tuple[str, str], tuple[str, str]]

# v12 — **의미층**: 지금도 참인 것(concept)과 그때 고른 것(decision). 둘은 다른
# 종류지만 그래프에서 서는 자리가 같다 — 어느 쪽도 개념 엣지의 끝점일 수 있고,
# 기록·사건·다리·청크가 가리키는 대상일 수 있다. 그래서 라우팅에서 이 집합에
# 속하는지만 보고 라벨은 _NODE_MAP 에서 꺼낸다(타입마다 분기를 늘리지 않는다).
_SEMANTIC_TYPES = ("concept", "decision")

#: v23 — 공개 위키 쪽. 커뮤니티 소유자에서는 이 네 종이 페이지 노드 넷(`_PAGE_TYPES`)
#: 어느 쌍에나 서고, ABOUT 은 사건·기록 페이지도 대상으로 받는다(`wiki_pairs_open`).
_WIKI_EDGES = ("relates_to", "prerequisite_of", "derives_from", "inherits_from")
_PAGE_TYPES = ("concept", "decision", "record", "episode")
#: 대체 관계(`_SUPERSEDES_EDGE`) — 새 것 → 옛 것. 같은 종류끼리만이고 근거가 필수다. 쓰기 경로는
#: 유효구간을 **판정하지 않는다**: 옛 것의 `valid_until` 이 비었거나 새 것의 시작과
#: 다르면 경고만 하고 어느 값도 대신 쓰지 않는다.
#: 근거: [[vinc.plan.temporal-edges-point-in-time-document-validity-2026-09-28]] §2
_EDGE_TYPES = set(_CC_EDGES) | set(_EC_EDGES) | {"connects_via", _SUPERSEDES_EDGE}

# 개방층 캐리어(F1 — D11-R2 포팅). 어휘(타입 이름)는 theme_store, 물리는 여기.
_ENTITY_LABEL, _ENTITY_ID, _TYPED_REL = "Entity", "entity_id", "TYPED"

DEFAULT_OWNER = "tenant:local"


def _is_open(ntype: str) -> bool:
    """개방층 타입 판별 — 비어있지 않고 레거시 6종 밖이면 Entity 캐리어 대상."""
    return bool(ntype) and ntype not in _NODE_MAP


def prefix_mismatch(ntype: str, nid: str) -> str:
    """id 접두사가 전용 테이블 타입을 가리키는데 타입이 다르면 그 접두사를 돌려준다.

    접두사는 삭제·끝점 해석이 테이블을 고르는 기준이다. 타입이 다르면 같은 id 의 두 번째
    행이 다른 테이블에 생기고, 묘비 키(`node_id` 하나)가 겹쳐 토픽 이동이 멈춘다."""
    prefix = nid.split(":", 1)[0] if ":" in nid else ""
    return prefix if prefix in _NODE_MAP and prefix != ntype else ""


#: 스토어 바인딩용 물리 키(PK 네임스페이싱 design A). 정의는 `store/pkns.py` 한 벌 —
#: 이 모듈만 쓰던 것을 PR-C 에서 query·sync·share·preference·docstore 가 같이 쓰게
#: 옮겼다. 여기 별칭을 남기는 이유는 호출부 표기를 짧게 두려는 것뿐이다.
_key = pkns.key


class _ThemeFold:
    """어휘 밖 타입의 주제 어휘 편입(D11-R2 §3) — theme_id 는 **lazy 확정**.

    해석 순서: fragment.theme_id(명시 — 미지 테마는 멱등 create) → 활성 테마 →
    개방 타입 최초 조우 시 "default" 테마 lazy 생성(create_theme 은 활성 테마가
    없으면 자신을 활성으로 지정). themes=None(테스트·sync/share 호출부)이면 전부
    무동작 — Entity 는 theme_id="" 로 영속되고 type 문자열은 행에 남는다(손실 0)."""

    def __init__(self, themes, fragment: dict):
        self.themes = themes
        self._explicit = (fragment.get("theme_id") or "").strip() if fragment else ""
        self.theme_id: str | None = None   # None = 미해석, "" = themes 부재
        self.proposed: list[dict] = []

    def resolve(self) -> str:
        if self.theme_id is not None:
            return self.theme_id
        if self.themes is None:
            self.theme_id = ""
            return ""
        if self._explicit:
            self.themes.create_theme(self._explicit)   # 멱등 — 명시는 존재 보장
            self.theme_id = self._explicit
        else:
            self.theme_id = self.themes.active_theme_id() or ""
            if not self.theme_id:
                self.themes.create_theme("default")     # 활성 부재 시 활성으로 지정됨
                self.theme_id = "default"
        return self.theme_id

    def fold(self, kind: str, name: str) -> None:
        if self.themes is None or not name:
            return
        tid = self.resolve()
        if not tid:
            return
        if self.themes.resolve_type(tid, kind, name) is None:
            entry = {"kind": kind, "name": name}
            if self.themes.propose_type(tid, kind, name) and entry not in self.proposed:
                self.proposed.append(entry)


#: 라벨이 열에 쓰는 필드(`_KEPT_FIELDS`)와 판정 대상 필드(`_CONTRACT_FIELDS`)는
#: `api/node_schema.py` 에 있다. 아래 둘은 그 표 밖의 필드를 받았을 때 경고 문장을 고른다.
#: 이미 자기 문장으로 경고하는 조합 — 두 번 말하지 않는다.
_ALREADY_WARNED = {("Entity", "domain"), ("Entity", "props"), ("Bridge", "domain")}
#: 열은 없지만 같은 값을 둘 자리가 따로 있는 조합 — 버렸다는 말 뒤에 그 자리를 덧붙인다.
#: 결정의 유효구간은 props 에 산다(`_decision_interval`).
_KEPT_ELSEWHERE = {("Decision", "valid_from"): "props.valid_from",
                   ("Decision", "valid_until"): "props.valid_until"}


#: 출생(`created`)으로 받는 형식: ISO 날짜 또는 날짜+시각. `known_at` 이 앞 10자를
#: 사전식으로 비교하므로 이 형식이 아니면 그 노드가 시점 읽기에서 엉뚱하게 숨는다.
_ISO_INSTANT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})?)?$")


def _iso_instant(v) -> str:
    """ISO 날짜·시각이면 그 문자열을, 아니면 빈 문자열을 돌려준다(달력상 없는 날도 거절)."""
    s = v.strip() if isinstance(v, str) else ""
    if not _ISO_INSTANT_RE.match(s):
        return ""
    try:
        datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return s


def _decision_interval(props: dict, stored_raw, nid: str, warnings: list[str]) -> dict:
    """결정의 유효구간(props.valid_from·valid_until)을 Record 열과 같은 규칙으로 검사한다.

    형식이 틀린 값은 쓰지 않고 저장된 값을 남긴다(없으면 키를 뺀다). 빈 문자열은
    명시적 지우기다. 뒤집힌 구간은 저장하되 경고한다. 넘겨받은 dict 는 고치지 않는다."""
    out, stored = props, None
    for key in ("valid_from", "valid_until"):
        raw = props.get(key)
        if raw is None:
            continue
        norm = node_schema.normalize_time(str(raw))
        if norm or not str(raw).strip():
            if norm != raw:
                out = {**out, key: norm}
            continue
        if stored is None:
            stored = _props_to_obj(stored_raw)
        out = dict(out)
        if stored.get(key) is not None:
            out[key] = stored[key]
        else:
            out.pop(key, None)
        warnings.append(
            f"props.{key} '{raw}' is not YYYY[-MM[-DD]] (id={nid}) — kept the stored "
            f"value; a malformed date is not stored as a validity interval")
    vf, vu = str(out.get("valid_from") or ""), str(out.get("valid_until") or "")
    if vf and vu and vu < vf:
        warnings.append(
            f"validity interval inverted — props.valid_until '{vu}' precedes "
            f"props.valid_from '{vf}' (id={nid}); stored as sent, but no instant "
            f"falls inside it")
    return out


def _kind_default_day(*, authored: bool, owner: str) -> str:
    """저작 경로(ingest)에서 새 결정·사건의 빈 날짜를 채울 오늘(UTC, 읽기의 오늘과 같은 시계).

    빈 문자열이면 채우지 않는다: sync 복제·팩 가져오기는 원본의 빈 값을 그대로 옮기고,
    커뮤니티 반영기는 경고 하나로도 반영을 실패로 본다(`_warn_orphan_decisions` 와 같다)."""
    if not authored or wiki_pairs_open(owner):
        return ""
    return _qb._today()


def _filled_warning(nid: str, field: str, day: str, when: str) -> str:
    """서버가 채운 날짜를 말한다. 쓰기는 거절하지 않는다."""
    return (f"{nid} has no {field}: filled with today's date {day} (UTC). "
            f"Send {field} to record {when}.")


def _warn_unkept_fields(node: dict, label: str, nid: str,
                        warnings: list[str]) -> None:
    """이 라벨이 **보관하지 않는** 계약 필드를 실어 왔으면 말한다(조용한 유실 금지).

    쓰기를 막지는 않는다. 이 경로는 ingest·sync apply·팩 가져오기가 함께 쓰므로
    거부로 만들면 필드 하나가 동기화 전체를 세운다 — 침묵을 고치는 데 필요한 것은
    거부가 아니라 **말**이다. `merged` 는 여전히 실제로 들어간 것만 센다."""
    kept = _KEPT_FIELDS.get(label)
    if kept is None:
        return
    for f in sorted(_CONTRACT_FIELDS - kept):
        if node.get(f) is None or (label, f) in _ALREADY_WARNED:
            continue
        where = _KEPT_ELSEWHERE.get((label, f))
        warnings.append(
            f"node.{f} not persisted on {label} (id={nid}) — no such column; "
            f"the value was dropped, not stored"
            + (f"; a {label} keeps it in {where}" if where else ""))


#: 되읽기 예산(직렬화 바이트). 요청 본문 상한이 응답까지 묶어 주지는 **않는다** —
#: 필드 하나만 고쳐 보낸 노드도 되읽기는 **저장된 props 전량**을 싣기 때문에 응답이
#: 요청보다 커질 수 있고, 허브는 단일 라이터 컨테이너다. 넘으면 자르되 자른 수를
#: 필드로 밝힌다(조용히 자르지 않는다 — 이 항목이 고치려는 고장이 바로 그것이다).
_READBACK_BUDGET = 256 * 1024

#: R4 — 코드에 대한 주장을 **조회 가능**하게 만드는 예약 props 셋. 산문 규약(§3:
#: "측정으로 쓰고 경로를 적는다")은 사람이 읽어야 비교되고, 이 셋은 질의가 비교한다.
#: `record` 만 싣는다 — 개념은 서술하고, 결정은 고르고, **측정하는 것은 기록**이다.
#: 근거: [[vinc.ops.graph-claim-decay]] §7.3 R4.
_CLAIM_PROPS = ("subject_path", "measured_at", "measured_ref")


#: 리포 이름은 **형식만** 검사한다(`repo:path`). 어느 리포를 아는지는 그래프마다 다르다 — 코드에
#: 박은 목록은 이 제품을 쓰는 다른 사람에게 늘 틀렸고 vincos 같은 새 리포에도 틀렸다. 대신 그래프에
#: 이미 있는 기록의 리포를 모아, **처음 보는 이름**이면 오타일 수 있다고 말한다(막지 않는다).
#: 근거: [[vinc.canon.graph-conventions §5.1]]
_REPO_CACHE: dict[tuple[int, str], tuple[float, set[str]]] = {}
_REPO_CACHE_TTL = 300.0


def _known_repos(store, owner: str) -> set[str]:
    """이 소유자의 Record·Decision 이 `subject_path` 에 쓴 리포 이름들. 몇 분 캐시한다(적재마다 훑지 않게)."""
    import time as _time
    key = (id(store), owner or "")
    hit = _REPO_CACHE.get(key)
    now = _time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    repos: set[str] = set()
    for label in ("Record", "Decision"):
        try:
            rows = store.run(f"MATCH (n:{label}) WHERE n.owner_id = $o AND n.props CONTAINS 'subject_path' "
                             "RETURN n.props AS p", {"o": owner}) or []
        except Exception:
            rows = []
        for r in rows:
            try:
                sp = (json.loads(r.get("p") or "{}") or {}).get("subject_path")
            except (TypeError, ValueError):
                continue
            for v in (sp if isinstance(sp, list) else [sp]):
                if isinstance(v, str):
                    repo, _path = _split_subject(_norm_subject_path(v))
                    if repo:
                        repos.add(repo)
    _REPO_CACHE[key] = (now + _REPO_CACHE_TTL, repos)
    return repos


def _subject_repo_warnings(norm: str, nid: str, warnings: list[str], known_repos=None) -> None:
    """접은 `subject_path` 가 리포를 안 담았으면, 또는 그래프가 처음 보는 리포면 경고한다(지우지 않는다).

    `known_repos` 는 그래프의 기존 리포 집합을 돌려주는 함수다. 없거나 그래프가 아직 아무 리포도
    모르면(첫 기록) 이름은 묻지 않는다."""
    repo, path = _split_subject(norm)
    if path and not repo:
        warnings.append(
            f"props.subject_path '{norm}' names no repository (id={nid}) — stored, "
            f"but a bare path cannot be compared across repositories; write it as repo:path")
    elif repo and known_repos is not None:
        known = known_repos()
        if known and repo not in known:
            warnings.append(
                f"props.subject_path repository '{repo}' is new to this graph (id={nid}) — stored; "
                f"if it is a typo it makes a namespace of its own (known: {', '.join(sorted(known)[:8])})")
            known.add(repo)          # 같은 적재 안에서 같은 이름을 거듭 말하지 않는다


def _claim_props(props, label: str, nid: str, warnings: list[str], known_repos=None):
    """예약 주장 필드를 정규화한다. props 원본은 건드리지 않는다(부분 재전송 함정).

    형식 위반은 **지우지 않고 경고한다**: props 는 자유 속성이라 서버가 값을 버리면
    그 자리가 유일한 사본이었을 때 복구가 없다. 열(`content_time`)과 반대 방향으로
    보이지만 이유는 같다 — 잃는 쪽을 고르지 않는다."""
    if not isinstance(props, dict) or not any(k in props for k in _CLAIM_PROPS):
        return props
    out = props
    if label != "Record":
        for k in _CLAIM_PROPS:
            if k in props:
                # 결정의 subject_path 는 결정 문(`decide_read.decide_paths`)이 읽는다(읽을 때 접는다,
                # 저장값은 보낸 그대로). 그래서 그 하나는 리포 경고만 받는다. 진단(claims)은
                # 여전히 기록만 본다.
                if label == "Decision" and k == "subject_path":
                    for raw in (props[k] if isinstance(props[k], list) else [props[k]]):
                        if isinstance(raw, str):
                            _subject_repo_warnings(_norm_subject_path(raw), nid, warnings, known_repos)
                    continue
                warnings.append(
                    f"props.{k} is a claim field carried by Record only (id={nid}) — "
                    f"stored on {label}, but the claim review will not read it there")
        return out
    raw = props.get("subject_path")
    if raw is not None:
        norm = _norm_subject_path(raw)
        if norm != raw:
            out = {**props, "subject_path": norm}
        _subject_repo_warnings(norm, nid, warnings, known_repos)
    at = props.get("measured_at")
    if at is not None:
        if not node_schema.normalize_time(str(at)):
            warnings.append(
                f"props.measured_at '{at}' is not YYYY[-MM[-DD]] (id={nid}) — stored "
                f"as sent, but claims cannot be ordered against it")
    return out


def _stored_nodes(store, types: dict[str, str], owner: str,
                  warnings: list[str]) -> tuple[list[dict], int]:
    """MERGE 직후의 **저장형**을 되읽는다(R3) — `(nodes, 잘린 수)`.

    왜 개수가 아니라 형태인가: `merged 14 created, 4 matched, warnings 0` 은 참이면서
    동시에 필드 하나가 사라진 것을 감췄다(2026-08-24 실측). 개수는 "행이 몇 개
    움직였나"를 말할 뿐 **무엇이 들어갔는지**는 말하지 않는다. 호출부가 같은 턴에
    보낸 것과 대조할 수 있으려면 저장형 자체가 응답에 있어야 한다.

    열 목록은 `_KEPT_FIELDS` 하나를 쓴다. 경고가 "이 라벨은 이 필드를 안 보관한다"고
    말하는 근거와 되읽기가 뽑는 열이 **같은 표**여야, 응답에서 어떤 필드가 안 보이는
    것이 곧 "그 종류엔 그 열이 없다"는 사실이 된다.

    `Domain` 은 제외한다 — 열이 `name` 하나뿐이라 저장형이 곧 id 자신이다.

    되읽기는 **증강**이다. 여기서 나는 실패가 쓰기 결과를 깨면 안 된다(쓰기는 이미
    커밋됐다). 그래서 라벨 단위로 삼키고 넘어간다.

    엣지는 아직 안 싣는다: 관계 테이블 9종에 걸쳐 열이 다르고, 노드에서와 달리
    엣지 필드가 조용히 사라진 실측이 아직 없다. 재는 것이 먼저다."""
    groups: dict[tuple[str, str], list[str]] = {}
    for nid, ntype in types.items():
        label, pk = (_ENTITY_LABEL, _ENTITY_ID) if _is_open(ntype) else _NODE_MAP[ntype]
        if not _KEPT_FIELDS.get(label):
            continue
        groups.setdefault((label, pk), []).append(nid)
    out: list[dict] = []
    dropped = 0
    budget = _READBACK_BUDGET
    for (label, pk), ids in groups.items():
        cols = sorted(_KEPT_FIELDS[label])
        scoped = label in _OWNER_SCOPED_LABELS
        # `IN` 바인딩은 **물리 키**다 — 읽기 훅은 돌려주는 값만 벗기고 Cypher 술어는
        # 물리 열을 그대로 본다(`store/embedded_backend.py` `_exec_to_dicts`).
        bind = list(pkns.keys(owner, ids)) if scoped else list(ids)
        proj = "".join(f", n.{c} AS {c}" for c in cols)
        try:
            rows = store.run(
                f"MATCH (n:{label}) WHERE n.{pk} IN $ids"
                + (" AND n.owner_id = $owner" if scoped else "")
                + f" RETURN n.{pk} AS _k{proj}",
                {"ids": bind, "owner": owner})
        except Exception as e:
            # **삼키지 않는다.** 되읽기가 증강이라 쓰기를 깨지 않는 것은 맞지만,
            # 조용히 빠지면 호출부는 "그 종류는 안 들어갔다"로 읽는다. 이 파일이
            # 고치려는 고장이 바로 그것이다.
            warnings.append(f"read-back failed for {label}: {type(e).__name__}")
            continue
        for r in rows:
            # ⚠ **되돌림표를 물리 키로 만들면 안 된다.** 훅이 이미 벗겨서 준 값을
            # 물리 키로 조회하게 되어 전부 빗나가고, `stored` 가 조용히 빈 목록이
            # 된다(플래그 켠 CI 에서만 나므로 기본 스위트는 초록으로 통과한다).
            # 바인딩한 키와 돌려받는 키가 다르다는 것이 이 층의 계약이다.
            nid = pkns.strip(r.get("_k") or "")
            if nid not in types:
                continue
            node: dict = {"id": nid, "kind": types[nid]}
            for c in cols:
                v = r.get(c)
                node[c] = (_props_to_obj(v) if c == "props"
                           else list(v) if isinstance(v, list) else v or "")
            size = len(json.dumps(node, ensure_ascii=False, default=str))
            if size > budget:
                dropped += 1
                continue
            budget -= size
            out.append(node)
    # 병합된 것보다 적게 돌아왔으면 **말한다.** 2026-08-25 에 이 자리가 조용히 빈
    # 목록을 냈다(물리 키로 되돌림표를 지어 전부 빗나갔다). 개수가 안 맞는다는 사실
    # 하나면 그때 소리가 났을 것이고, 플래그 켠 CI 까지 가지 않았다.
    expected = sum(len(v) for v in groups.values())
    if len(out) + dropped < expected:
        warnings.append(
            f"read-back returned {len(out) + dropped} of {expected} merged nodes — "
            "`stored` is incomplete, do not read it as what was written")
    return out, dropped


def _screen_node(node: dict) -> str | None:
    """노드 형태 검사. 건너뛸 이유(경고 문장)를, 통과하면 None 을 돌려준다.

    스토어를 읽지 않는다. 검사 순서가 곧 경고다: 타입·id 없음, U+001F, 접두사 불일치,
    제목 없는 Entity, 크기 상한 셋(제목·태그·props). 먼저 걸린 하나만 말한다.
    타입·id 는 `_merge_node` 와 같은 식으로 읽는다(앞뒤 공백을 벗긴 문자열)."""
    ntype = (node.get("type") or "").strip()
    nid = (node.get("id") or "").strip()
    if not ntype:
        return f"node without type — skipped ({nid or '?'})"
    if not nid:
        return f"node without id — skipped (type={ntype})"
    if pkns.has_sep(node):
        # PK 네임스페이싱(design A) 의 물리 키 판별자가 U+001F 존재 여부다
        # (strip 이 이 바이트로 owner 를 잘라낸다). 사용자 입력에 이 바이트가
        # 섞이면 나중에 물리 키로 오인되므로 지금 이 노드만 스킵한다.
        # 근거: [[vinc.plan.node-pk-namespacing]] §7
        return f"node contains U+001F (reserved for PK namespacing) — skipped (id={nid})"
    mismatch = prefix_mismatch(ntype, nid)
    if mismatch:
        return (
            f"node type {ntype!r} disagrees with its id prefix {mismatch!r}: skipped "
            f"(id={nid}). The prefix names the table, so this would store a second row "
            f"under the same id. Send type {mismatch!r} or use an id without that prefix.")
    if _is_open(ntype) and not (node.get("title") or "").strip():
        return f"entity node.title required: id={nid} (type={ntype})"
    # L1(감사 2026-08-18) — 필드 크기 상한. 본문 4MB 안에서도 단일 노드에 거대
    # 페이로드를 반복해 심어 스토어·메모리를 부풀릴 수 있다. 상한은 정상 데이터가
    # 절대 안 닿게 넉넉히 잡고, 넘으면 **자르지 않고 이 노드만 스킵**한다(props 를
    # 자르면 구조가 깨진다). 큰 원문은 props 가 아니라 문서(Chunk=SSoT)로 가야 한다.
    _t = node.get("title")
    if isinstance(_t, str) and len(_t) > 16_384:
        return f"node.title too large — skipped (id={nid}, {len(_t)} chars > 16384)"
    _tg = node.get("tags")
    if isinstance(_tg, list) and (len(_tg) > 256
            or any(isinstance(t, str) and len(t) > 2048 for t in _tg)):
        return f"node.tags too large — skipped (id={nid})"
    _pr = node.get("props")
    if _pr is not None:
        try:
            if len(json.dumps(_pr, ensure_ascii=False)) > 512 * 1024:
                return f"node.props too large — skipped (id={nid}, >512KB serialized)"
        except (TypeError, ValueError):
            # 직렬화 불가 props 는 여기서 거르지 않는다. 이 노드의 props 를 쓰는 자리의
            # 직렬화가 같은 예외를 입구 밖으로 낸다(알려진 결함, 경고가 아니다).
            pass
    return None


def _owner_conflict(current: dict, owner: str, nid: str, *, owner_scoped: bool,
                    owner_explicit: bool, matched: bool) -> str | None:
    """소유 충돌 관문. 이 노드를 쓰면 안 되면 그 경고 문장을, 아니면 None 을 돌려준다.

    명시 owner 로 이미 있는 owner-스코프 행에 쓰는데 그 행의 owner 가 호출자와 다를
    때만 걸린다. `current` 는 그 행에서 읽은 `owner_id` 를 담는다. 호출부는 SET·MERGE
    전에 부르고, 걸리면 그 id 를 `id_conflicts` 에 더한 뒤 이 노드를 건너뛴다."""
    # SEC-3: PK 충돌 소유권 탈취 차단. ID(concept:*)는 콘텐츠기반 공유 PK라, 강제 owner
    # 경로(owner_explicit=호스티드/원격/웹)에서 **남이 이미 소유한** 노드에 MERGE 하면
    # owner_id 재스탬핑(탈취) + 내용 덮어쓰기가 된다. 소유자가 다르면 노드를 건너뛴다
    # (SET/MERGE 전에). 로컬(owner_explicit=False)이나 신규(not matched)는 현행 유지.
    # 🔴 빈 owner(''·NULL) 행도 남의 것으로 본다 — 통과시키면 그 행을 호출자 owner 로
    # 다시 찍고 덮어써 조용히 가져간다(VIN-234). 기기의 레거시 빈 owner 행은 기동 때
    # `api/owner_fold.py` 가 먼저 `tenant:local` 로 접으므로 기기 편집은 막히지 않는다.
    # 근거: [[record:an-explicit-owner-write-claimed-ownerless-nodes-2026-09-30]]
    if not (owner_scoped and owner_explicit and matched):
        return None
    existing_owner = current.get("owner_id") or ""
    if existing_owner == owner:
        return None
    # ⚠ 이 스킵은 **조용한 데이터 손실**이었다(감사 2026-08-18 H1). 저작자는
    # owner 로 걸러진 검색을 먼저 돌려 "없음"을 보고 이 노드를 지었는데, 쓰기가
    # 사라지고 응답은 executed:true 로 온다. 그리고 다시 시도해도 영원히 같다
    # — 노드 PK 가 전역 네임스페이스라 그 슬러그의 물리 노드는 허브에 하나뿐이다.
    # 그래서 이유·영속성·다음 행동을 한 문장에 싣고, 호출부가 `id_conflicts` 로
    # **구조화해서** 낸다(warnings 배열은 호스트가 안 보여 주기도 한다).
    # 근본 처방(PK 를 owner 로 네임스페이스)은 [[vinc.plan.node-pk-namespacing]].
    held = "owned by another tenant" if existing_owner else "has no owner (a legacy row)"
    return (f"id {nid} {held} — skipped. Nothing was written for "
            f"this node, and every retry will be skipped the same way: node ids are "
            f"one global namespace, so this slug is not available to you. Author it "
            f"under a different id.")


def _tri_state(raw, source: str, nid: str, warnings: list[str]) -> str | None:
    """시점 열(`content_time`·`valid_from`·`valid_until`·사건 `date`)에 보낸 값을 세 갈래로 가른다.

    - 없음(None): None 을 돌려준다. 쓰지 않는다(부분 재전송은 저장된 값을 보존한다).
    - 빈 값(공백뿐): "" 를 돌려준다. 명시적 지우기다.
    - 있음: 정규형 `YYYY[-MM[-DD]]` 를 돌려준다. 형식을 어기면 쓰지 않고 경고한 뒤 None 을
      돌려준다. SET 하면 오타 한 번이 저장된 시점을 지운다.

    ``source`` 는 경고 문장의 앞머리다. 클라이언트가 읽는 문장이라 글자를 바꾸지 않는다."""
    if raw is None:
        return None
    norm = node_schema.normalize_time(str(raw))
    if norm or not str(raw).strip():
        return norm
    warnings.append(f"{source} '{raw}' is not YYYY[-MM[-DD]] — kept the stored "
                    f"value (id={nid})")
    return None


def _plan_join(*parts) -> tuple[dict, list[str]]:
    """열 계획 조각들을 차례로 잇는다. 앞 조각의 열과 경고가 먼저다(SET 순서·경고 순서가 그대로 선다)."""
    sets: dict = {}
    warns: list[str] = []
    for part_sets, part_warns in parts:
        sets.update(part_sets)
        warns.extend(part_warns)
    return sets, warns


def _plan_promote(sets: dict, warns: list[str], current: dict, matched: bool, nid: str,
                  col: str, val: str, source: str) -> None:
    # props 승격은 신규이거나 열이 비었을 때만 한다. 저작된 열을 props 만 실은
    # 재전송이 덮으면 안 된다. 값이 다르면 열을 지키고 말한다.
    held = str(current.get(col) or "")
    if not matched or not held:
        sets[col] = val
    elif held != val:
        warns.append(
            f"{source} not promoted — {col} already holds a value and was kept "
            f"(id={nid}); send {col} at the top level to change it")


def _plan_entity(node: dict, ntype: str, nid: str, owner: str, *, matched: bool,
                 owner_explicit: bool, theme_id: str,
                 theme_explicit: bool) -> tuple[dict, list[str]]:
    """Entity 캐리어(F1)의 열 계획. 반환은 (열 → 값, 경고)다."""
    sets: dict = {}
    warns: list[str] = []
    # Entity 캐리어(F1) — 열은 type/title/owner_id/theme_id/tags 뿐. domain/props
    # 는 열이 없어 미보존(휴면 엔진 동형 — warnings 로 통지, 조용한 유실 금지).
    sets["type"] = ntype
    sets["title"] = str(node.get("title")).strip()
    # tags 는 다른 라벨과 같다: 실으면 쓰고 안 실으면 보존한다. 신규만 빈 목록으로 연다.
    if node.get("tags") is not None:
        sets["tags"] = [str(t) for t in (node.get("tags") or [])]
    elif not matched:
        sets["tags"] = []
    # 주제 귀속은 신규이거나 fragment 가 값을 직접 실었을 때만 쓴다. 활성 테마나
    # themes 없는 호출부("")가 기존 행의 귀속을 덮으면 안 된다.
    if not matched or (theme_explicit and theme_id):
        sets["theme_id"] = theme_id
    if node.get("domain") is not None:
        warns.append(f"node.domain not persisted on Entity (id={nid}); fold into tags/theme")
    if node.get("props"):
        warns.append(f"node.props not persisted — no schema-free column on Entity (id={nid})")
    if owner_explicit or not matched:
        sets["owner_id"] = owner
    return sets, warns


def _plan_shared(store, node: dict, label: str, ntype: str, nid: str, owner: str,
                 current: dict, *, matched: bool, default_day: str) -> tuple[dict, list[str]]:
    """저작 라벨 다섯(Concept·Decision·Episode·Record·Bridge)이 같이 갖는 열: title·domain·tags·props."""
    sets: dict = {}
    warns: list[str] = []
    # `title` 도 domain·tags·props 와 **같은 규칙**이다: 주면 쓰고, 안 주면
    # 보존한다. **신규일 때만** id 로 채운다(제목 없는 노드 금지).
    # ⚠ 이 가드를 걷어내지 말 것 — 없던 시절 스텁 재전송이 제목을 파괴했다:
    # [[record:unguarded-title-overwrote-107-nodes-with-their-ids]].
    #
    # v0.8.4 axis2 item2 — Episode·Record 는 다르다. id 를 제목 대신 쓰면
    # 빈 제목이 영영 안 보이고 PR-0 감사 전까지 아무도 못 잡는다(id 가 이미
    # 있으니 "제목 없음"이 안 드러난다). 그래서 이 두 라벨만 **빈 채로 두고
    # 경고한다** — id 는 키지 이름이 아니다. Concept·Decision·Bridge 는
    # 이 항목의 범위 밖이라 기존 대체 동작을 유지한다:
    # [[vinc.canon.mcp-contract §5.2]].
    if label in ("Episode", "Record"):
        _title = node.get("title")
        if _title is not None and str(_title).strip():
            sets["title"] = str(_title)
        elif _title is not None:
            sets["title"] = ""
            warns.append(
                f"{ntype} node.title empty — left blank, not set to the id "
                f"(id={nid})")
        elif not matched:
            warns.append(
                f"{ntype} node has no title — left blank, not set to the id "
                f"(id={nid})")
    elif node.get("title") is not None:
        sets["title"] = str(node["title"]) or nid
    elif not matched:
        sets["title"] = nid
    if node.get("domain") is not None:
        if label == "Bridge":
            # Bridge 는 domain 열이 없다(source_domain/target_domain 대신) — SET
            # 하면 Binder 예외로 죽는다(실측, 2026-07-21). 경고로 강등해 조용한
            # 유실 대신 명시 통지, 나머지 노드/엣지 처리는 계속한다.
            warns.append(
                f"node.domain not persisted on Bridge (id={nid}); "
                "Bridge has source_domain/target_domain instead, not settable via fragment ingest")
        else:
            # 정규화된 값을 **열에도** 넣는다 — 열과 Domain 노드 이름이 정확히
            # 같아야 IN_DOMAIN 이 그 열의 그래프 표현일 수 있다(engines/
            # domain_hierarchy.normalize 주석). 잘 쓴 값에는 항등이다.
            sets["domain"] = domain_hierarchy.normalize(node["domain"])
    # 열이 없는 라벨(Bridge)에 SET 하면 Binder 로 노드가 통째로 떨어진다. 경고는
    # 위 `_warn_unkept_fields` 가 이미 냈다.
    if node.get("tags") is not None and "tags" in _KEPT_FIELDS[label]:
        sets["tags"] = [str(t) for t in (node.get("tags") or [])]
    _pv = node.get("props")
    if label == "Decision" and isinstance(_pv, dict):
        # 결정의 유효구간은 props 에 산다. 형식 검사는 Record 열과 같다.
        _pv = _decision_interval(_pv, current.get("props"), nid, warns)
    if (label == "Decision" and default_day and not matched
            and (_pv is None or isinstance(_pv, dict))
            and not str((_pv or {}).get("valid_from") or "").strip()
            and not str((_pv or {}).get("valid_until") or "").strip()):
        # 새 결정만 채운다. 기존 결정은 저장된 날짜를 지키고, 보낸 값은 덮지 않는다.
        # 끝만 있는 결정은 닫힌 구간이라 시작을 오늘로 두면 뒤집힌다. 그대로 둔다.
        _pv = {**(_pv or {}), "valid_from": default_day}
        warns.append(_filled_warning(nid, "props.valid_from", default_day,
                                     "when the decision took effect"))
    if _pv is not None:
        sets["props"] = json.dumps(_claim_props(_pv, label, nid, warns,
                                                known_repos=lambda: _known_repos(store, owner)),
                                   ensure_ascii=False, sort_keys=True)
    return sets, warns


def _plan_one_liner(node: dict, nid: str, current: dict, *, matched: bool) -> tuple[dict, list[str]]:
    """Concept 의 `one_liner`."""
    sets: dict = {}
    warns: list[str] = []
    # date/summary 승격(아래 Episode)과 같은 규칙: top-level 이 오면 그것이
    # 이기고, 없으면 props.one_liner 또는 props.note 를 승격한다(키가 아예
    # 없을 때만, 그리고 열이 비었을 때만). 왜 되살렸나: [[vinc.research.no-llm-chat]] Q5.
    _props = node.get("props") if isinstance(node.get("props"), dict) else {}
    if node.get("one_liner") is not None:
        sets["one_liner"] = str(node["one_liner"])
    elif "one_liner" not in node:
        _src = "one_liner" if _props.get("one_liner") else "note"
        cand = _props.get(_src)
        if isinstance(cand, str) and cand.strip():
            _plan_promote(sets, warns, current, matched, nid, "one_liner", cand.strip(),
                          f"props.{_src}")
    return sets, warns


def _plan_content_time(node: dict, nid: str, current: dict, *,
                       matched: bool) -> tuple[dict, list[str]]:
    """Concept·Record 의 내용 시점(`content_time`)."""
    sets: dict = {}
    warns: list[str] = []
    # v7(D72) — **내용 시점**(`content_time`). 규칙은 앞의 셋과 같다:
    # top-level 이 이기고, 키가 **아예 없을 때만** props 를 승격한다(F9).
    # ⚠ 형식 위반은 **쓰지 않고 경고한다** — SET 하면 오타 한 번이 저장된
    # 시점을 지운다. 빈 문자열은 명시적 지우기로 존중한다. 문서 경로
    # (`put_doc`)도 이제 같은 규칙이다: [[record:content-time-writable-through-ingest]].
    _cp = node.get("props") if isinstance(node.get("props"), dict) else {}
    _raw = node.get("content_time")
    _promoted = False
    if _raw is None and "content_time" not in node:
        _cand = _cp.get("content_time")
        _raw = _cand if isinstance(_cand, str) else None
        _promoted = _raw is not None
    _norm = _tri_state(_raw, "content_time", nid, warns)
    if _norm is not None and not _promoted:
        sets["content_time"] = _norm
    elif _norm:      # 빈 props 값은 승격할 것이 없다. 지우기는 top-level 로만 한다
        _plan_promote(sets, warns, current, matched, nid, "content_time", _norm,
                      "props.content_time")
    return sets, warns


def _plan_validity(node: dict, nid: str, current: dict) -> tuple[dict, list[str]]:
    """Record 의 유효구간 `valid_from`·`valid_until`."""
    sets: dict = {}
    warns: list[str] = []
    # v15(W13) — **유효구간** [valid_from, valid_until). 규칙은 content_time
    # 과 같다: 키가 있을 때만 쓰고(부분 재전송은 보존), 형식 위반은 쓰지 않고
    # 경고하며, 빈 문자열은 명시적 지우기다. props 승격은 없다 — 이 두 필드는
    # 레거시 자리가 없던 신설이라 승격할 관행 자체가 없다(YAGNI).
    # 무효화("이 주장은 X 까지였다")는 valid_until 만 실은 부분 재전송으로
    # 쓴다 — 그래서 보존 가드가 이 필드의 핵심이다.
    # 근거: [[vinc.plan.0-9-0-open-decisions §2]] · 형식 계약은 content_time 동형.
    for _vcol in ("valid_from", "valid_until"):
        _vnorm = _tri_state(node.get(_vcol), _vcol, nid, warns)
        if _vnorm is not None:
            sets[_vcol] = _vnorm
    # 뒤집힌 구간은 **저장하되 말한다** — 두 값이 각자 합법이라 거부하면
    # 잃는 쪽을 고르는 일이 되고, 침묵하면 "T 에 유효한가" 질의가 전부
    # 거짓이 된다. 유효값은 방금 실은 것, 없으면 저장된 것(사전식 비교 —
    # 접두 형식이라 자릿수가 달라도 순서가 선다).
    _vf = sets.get("valid_from", current.get("valid_from") or "")
    _vu = sets.get("valid_until", current.get("valid_until") or "")
    if _vf and _vu and _vu < _vf:
        warns.append(
            f"validity interval inverted — valid_until '{_vu}' precedes "
            f"valid_from '{_vf}' (id={nid}); stored as sent, but no instant "
            f"falls inside it")
    return sets, warns


def _plan_episode(node: dict, nid: str, current: dict, *, matched: bool,
                  default_day: str) -> tuple[dict, list[str]]:
    """Episode 의 `date`·`summary`."""
    sets: dict = {}
    warns: list[str] = []
    # share pack(D19)의 레코드 노드 — 콘텐츠 필드만(선호 신호는 팩에 없음).
    #
    # F9 — **props.date/summary 를 열로 승격한다.** 계약이 두 필드를 선언하지
    # 않던 시절 상류가 실을 수 있는 자리는 props 뿐이었고, 그 열은 recall 의
    # `ORDER BY e.date DESC`·타임라인·팩/공유가 전부 읽는 자리라 비면 정렬이
    # 무의미해진다. 승격이 **쓰기 시점**인 이유가 그것이다 — 응답에서
    # coalesce 하면 그 응답 하나만 고쳐지고 정렬·타임라인·공유는 빈 채 남는다.
    # top-level 이 이기고(빈 문자열로 지우는 것 포함) props 폴백은 키가 아예
    # 없을 때만. props 원본은 지우지 않는다(부분 재전송이 원본을 깎는다).
    # 선언 누락의 계보: [[vinc.canon.mcp-contract]] §5.1.
    _props = node.get("props") if isinstance(node.get("props"), dict) else {}
    for _col in ("date", "summary"):
        _val = node.get(_col)
        _promoted = False
        if _val is None:
            # 문자열만 승격한다 — props 는 자유 형태라 dict/list 가 올 수
            # 있는데 그걸 str() 로 뭉개면 날짜가 아닌 것이 날짜 열에 앉는다.
            # 승격 못 한 값도 props 에 그대로 남으므로 유실은 아니다.
            _cand = _props.get(_col)
            if not isinstance(_cand, str) or not _cand.strip():
                continue
            _val, _promoted = _cand.strip(), True
        _val = str(_val)
        if _col == "date":
            # 날짜는 content_time 과 같은 형식 계약이다. 틀리면 쓰지 않고 저장된
            # 값을 남긴다 — 정렬·시점 읽기가 이 열을 사전식으로 비교한다.
            _dn = _tri_state(_val, "props.date" if _promoted else "date", nid, warns)
            if _dn is None:
                continue
            _val = _dn
        if _promoted:
            _plan_promote(sets, warns, current, matched, nid, _col, _val, f"props.{_col}")
        else:
            sets[_col] = _val
    if default_day and not matched and not sets.get("date"):
        # 새 사건만 채운다(결정과 같은 규칙). 빈 문자열도 새 노드에선 빈 날짜다.
        # 이미 있는 키는 자리를 지킨 채 값만 바꾼다(SET 순서가 그대로다).
        sets["date"] = default_day
        warns.append(_filled_warning(nid, "date", default_day, "when it happened"))
    return sets, warns


def _plan_birth_and_owner(store, node: dict, label: str, nid: str, owner: str, *,
                          matched: bool, owner_explicit: bool) -> tuple[dict, list[str]]:
    """출생(`created`)·소유(`owner_id`)·Record 의 `tenant_id`."""
    sets: dict = {}
    warns: list[str] = []
    # 출생(`created`) = **어느 공간에서든 처음 기록된 때**. 규칙이 둘로 갈린다 —
    #   신규 : 상류가 실은 값, 없으면 지금(HLC).
    #   기존 : **절대 안 덮는다**(팩 재적재가 남의 값으로 생일을 바꾸는 것을 막는다).
    # 동기화의 min 과 다른 이유는 신뢰다: 같은 계정의 두 기기는 한 공간이지만 팩은
    # 미신뢰 외부 입력이라 min 대신 쓰기 자체를 안 한다(동기화 쪽은
    # api/sync._reconcile_birth · [[decision:created-is-earliest-known-recording]]).
    if label in ("Concept", "Record", "Decision", "Bridge") and not matched:
        # 상류 값은 ISO 날짜·시각일 때만 받는다. 아니면 버리고 지금을 찍는다.
        _born = _iso_instant(node.get("created"))
        if node.get("created") and not _born:
            warns.append(
                f"created '{node['created']}' is not an ISO date or timestamp — "
                f"ignored, the write time was stamped instead (id={nid})")
        sets["created"] = _born or revstamp.hlc_now(store)
    if label in ("Record", "Concept", "Episode", "Bridge", "Decision") \
            and (owner_explicit or not matched):
        sets["owner_id"] = owner
    if label == "Record":
        sets["tenant_id"] = "tenant:local"
    return sets, warns


def _plan_authored(store, node: dict, label: str, ntype: str, nid: str, owner: str,
                   current: dict, *, matched: bool, owner_explicit: bool,
                   default_day: str) -> tuple[dict, list[str]]:
    """Domain·Entity 밖 다섯 라벨의 열 계획. 공통 열 뒤에 라벨 묶음별 열, 맨 끝에 출생·소유."""
    none: tuple[dict, list[str]] = ({}, [])
    return _plan_join(
        _plan_shared(store, node, label, ntype, nid, owner, current,
                     matched=matched, default_day=default_day),
        _plan_one_liner(node, nid, current, matched=matched) if label == "Concept" else none,
        _plan_content_time(node, nid, current, matched=matched)
        if label in ("Concept", "Record") else none,
        _plan_validity(node, nid, current) if label == "Record" else none,
        _plan_episode(node, nid, current, matched=matched, default_day=default_day)
        if label == "Episode" else none,
        _plan_birth_and_owner(store, node, label, nid, owner, matched=matched,
                              owner_explicit=owner_explicit))


def merge_node(store, node: dict, owner: str, warnings: list[str],
                *, owner_explicit: bool = False, theme_id: str = "",
                theme_explicit: bool = False,
                conflicts: list[str] | None = None,
                default_day: str = "") -> str | None:
    """단일 노드 MERGE. 반환 = 'created' | 'matched' | None(스킵).

    owner_id 스탬핑은 **생성 시 또는 fragment.owner 명시 시**에만 한다 — owner 없는
    재-MERGE 가 기존 노드의 소유를 기본값으로 조용히 덮어쓰면 스코프 필터(§1 불변)가
    깨진다.

    개방층(F1): 레거시 6종 밖 타입은 스킵이 아니라 Entity 캐리어로 라우팅된다 —
    스킵은 형태 오류(id 부재, Entity 는 title 부재)뿐. ``theme_id`` 는 Entity 행에
    각인되는 주제 귀속(THEME-DEEP §6) — 호출부(_ThemeFold)가 해석해 넘긴다.
    ``theme_explicit`` 은 fragment 가 theme_id 를 직접 실었는가다. 실었을 때만 기존
    Entity 의 주제를 바꾼다."""
    ntype = (node.get("type") or "").strip()
    nid = (node.get("id") or "").strip()
    if guard_active(store):
        from .topic_conversion import check_write
        check_write(store, owner, node)
    skip = _screen_node(node)
    if skip:
        warnings.append(skip)
        return None
    is_entity = _is_open(ntype)
    label, pk = (_ENTITY_LABEL, _ENTITY_ID) if is_entity else _NODE_MAP[ntype]
    _warn_unkept_fields(node, label, nid, warnings)
    # owner_id 를 가진 라벨 — Bridge(v2)·Entity(F1) 편입으로 SEC-3 가드·스탬프 규칙 승계.
    owner_scoped = label in _OWNER_SCOPED_LABELS
    # PK 네임스페이싱(design A) — 스토어 바인딩용 물리 키. **owner-스코프 라벨만**
    # 합성한다: `Domain` 은 열이 `name` 하나뿐이라 소유를 아예 담지 않고 전역 공유가
    # 성립하며(engines/domain_hierarchy 의 소유 모델), 그래서 마이그레이션 대상 라벨
    # 여섯에도 안 든다. 여기서 합성하면 남의 도메인 이름과 조용히 갈라진다.
    key = _key(owner, nid) if owner_scoped else nid

    # 존재확인 + (추적 라벨이면) 현재 추적필드값도 함께 조회 — 변경감지 스탬핑(S0).
    # SEC-3: owner-스코프 라벨이면 기존 owner_id 도 조회해 교차테넌트 충돌을 판별한다.
    tracked = revstamp.tracked_fields(label)
    if tracked:
        cols = ["n.rev AS rev"] + [f"n.{c} AS {c}" for c in tracked]
        if owner_scoped:
            cols.append("n.owner_id AS owner_id")
        cur_rows = store.run(
            f"MATCH (n:{label}) WHERE n.{pk} = $id RETURN {', '.join(cols)}", {"id": key})
        matched = bool(cur_rows)
        current = cur_rows[0] if cur_rows else {}
    else:
        exists = store.run(f"MATCH (n:{label}) WHERE n.{pk} = $id RETURN count(n) AS c",
                           {"id": key})
        matched = bool(exists and exists[0].get("c"))
        current = {}

    # 소유 충돌 관문(`_owner_conflict`). 걸린 id 는 경고와 함께 `id_conflicts` 로도 낸다.
    skip = _owner_conflict(current, owner, nid, owner_scoped=owner_scoped,
                           owner_explicit=owner_explicit, matched=matched)
    if skip:
        if conflicts is not None:
            conflicts.append(nid)
        warnings.append(skip)
        return None

    # Agent 역할이면 작성 계약과 어긋난 곳을 말한다(쓰기는 그대로 한다). 신규인지는
    # 여기서야 알므로 이 자리다 — 신규가 아니면 보내지 않은 필드는 보존되니 묻지 않는다.
    # 근거: [[vinc.canon.agent-roles §6]]
    if label == "Concept" and role_contract.is_role_id(nid):
        warnings.extend(role_contract.check(node, created=not matched,
                                            stored_domain=current.get("domain")))

    # 열 계획. 라벨 묶음마다 `_plan_*` 가 (열 → 값, 경고)를 돌려준다. 계획 순서가 곧 SET 순서이고
    # 경고 순서다(클라이언트가 경고를 순서대로 읽는다).
    if is_entity:
        plan, plan_warnings = _plan_entity(node, ntype, nid, owner, matched=matched,
                                           owner_explicit=owner_explicit, theme_id=theme_id,
                                           theme_explicit=theme_explicit)
    elif label != "Domain":
        plan, plan_warnings = _plan_authored(store, node, label, ntype, nid, owner, current,
                                             matched=matched, owner_explicit=owner_explicit,
                                             default_day=default_day)
    else:
        # Domain 의 props·title 은 `_warn_unkept_fields` 가 한 번 말한다. 두 번 말하지 않는다.
        plan, plan_warnings = {}, []
    warnings.extend(plan_warnings)
    sets = [f"n.{col} = ${col}" for col in plan]
    params = {"id": key, **plan}

    def _set(col, val):
        sets.append(f"n.{col} = ${col}")
        params[col] = val

    # 변경감지 스탬핑(S0): 신규이거나 추적필드가 실제로 달라졌을 때만 rev/updated_at/
    # sync_seq 를 찍는다 — 멱등 재적재는 무스탬프(가짜 충돌 방지). 원격에서 apply 되는
    # 경로는 스탬프를 원격값 그대로 보존하므로 이 함수를 타지 않는다(api/sync).
    if tracked:
        incoming = {c: params[c] for c in tracked if c in params}
        if not matched or revstamp.differs(current, incoming):
            _set("updated_at", revstamp.hlc_now(store))
            _set("rev", int(current.get("rev") or 0) + 1)
            _set("sync_seq", revstamp.next_seq(store, owner))

    set_clause = (" SET " + ", ".join(sets)) if sets else ""
    try:
        store.run(f"MERGE (n:{label} {{{pk}: $id}}){set_clause}", params)
    except Exception as exc:
        # 위에서 못 막은 스키마 불일치(Binder 예외 등)는 **이 노드 하나만** 스킵한다 —
        # merge_fragment 의 계약("malformed nodes ... skipped, reported in warnings
        # with partial:true")이 그렇게 약속한다. 미지 케이스용 안전망이라 지우지 말 것:
        # [[record:one-bad-node-used-to-take-the-whole-batch]].
        warnings.append(f"node write failed — skipped (id={nid}, label={label}): {exc}")
        return None

    # 도메인 계층(E-1, v11) — `domain` 열이 설정된 노드에 Domain 노드·PARENT_OF 사슬·
    # IN_DOMAIN 을 파생 유지한다. **열이 갱신될 때만** 돈다: 도메인을 안 실은 부분
    # 재전송에서 엣지를 지우면 `domain` 열은 보존되는데 그래프 표현만 사라져 둘이
    # 갈린다(title 무가드가 냈던 병과 같은 뿌리).
    #
    # 실패해도 노드 쓰기는 되돌리지 않는다 — 계층은 열에서 언제든 다시 파생되는
    # 파생물이고(백필 스크립트가 그 일을 한다), 여기서 죽으면 배치 전체가 날아간다.
    if "domain" in params and label in domain_hierarchy.DOMAIN_LABELS:
        try:
            # `link_node` 는 `$id` 를 노드 PK 로 바인딩하므로 **물리 키**를 넘긴다
            # (Domain 쪽 이름은 논리 그대로 — 전역 공유 좌표다).
            domain_hierarchy.link_node(store, label, key, params["domain"])
        except Exception as exc:
            warnings.append(f"domain hierarchy not updated (id={nid}): {exc}")

    return "matched" if matched else "created"


# 사설 별칭. 같은 함수이고, 이 이름으로 부르는 호출부·테스트가 그대로 돈다.
_merge_node = merge_node


#: v0.8.4 axis2 item3 — 쌍 자체가 안 되는 경우(양 끝 타입은 알려져 있고 선언도
#: 됐는데 그 조합이 rel 테이블 밖)의 타입 코드. warnings 는 여전히 문자열 배열이라
#: 산문 앞에 이 토큰을 붙여 grep 가능하게 만든다 — "unsupported_type"(타입 이름
#: 자체가 어휘 밖)과는 다른 사유다. 정본은 [[vinc.canon.mcp-contract §5.2]].
_ERR_DISALLOWED_PAIR = "edge_disallowed_pair"

_ALLOWED_PAIRS_HINT = (
    "allowed pairs (concept and decision are interchangeable endpoints, "
    "except relates_to which stays concept→concept): "
    "supersedes = new→old of the same kind (record→record, decision→decision, "
    "concept:doc.*→concept:doc.*), with nonempty evidence; "
    "relates_to = concept→concept; "
    "prerequisite_of|derives_from|inherits_from = concept|decision → concept|decision; "
    "returns_to = distinct role concepts, with nonempty evidence; "
    "record → concept|decision for any of those four; "
    "connects_via = bridge → concept|decision; "
    "about = episode|decision → concept|decision; "
    "any-type = open-entity→open-entity (TYPED carrier)"
)

#: 반복되는 엣지 경고의 이유별 설명. **엣지마다 한 줄씩 내면 안 된다** —
#: `rpc._result_text` 의 봉투 상한(MAX_RESULT_CHARS = 72,000자)을 넘기면 결과 전체가
#: "Result too large … Nothing was changed" 로 강등되는데, 그건 거짓말이다: MERGE 는
#: 이미 돌았고 노드는 들어가 있다. 아래 두 문구는 각각 250자·400자대라 각각 290개·
#: 180개면 상한을 넘긴다. 그래서 이유당 한 줄로 접는다.
#: 끝점 미해석은 **두 경우**이고 조치가 다르다 — 아예 안 실렸으면 실으면 되고,
#: 실었는데 스킵됐으면 그 노드를 고쳐야 한다. 한 덩이로 뭉개면 후자에서
#: "id 를 실어라"라는 이미 한 일을 다시 하라는 안내가 나간다.
_EDGE_WARN_REASONS = {
    # v0.8.4 axis2 item1 — 여기 있던 "대상을 `nodes` 에 다시 선언하라"는 안내는
    # 지웠다. 해석기가 이제 스토어를 보므로(:func:`_resolve_endpoint_type`) 이미 있는
    # 노드로 향하는 엣지는 그냥 해석된다. 그래서 이 경고가 남는 경우는 **정말로 없는
    # 것**(또는 내 것이 아닌 것)뿐이고, 그때 "다시 선언하라"는 틀린 처방이다.
    "undeclared_endpoint":
        "endpoint not found — it is not in this payload's `nodes`, and no node with "
        "that id exists in the store under your ownership. Check the id (they are "
        "exact and type-prefixed), or author the node in this same call",
    "skipped_endpoint":
        "endpoint was declared but did not merge — it was skipped earlier in this same "
        "call, so its edges went down with it. Fix the node first: the reason is in the "
        "node warnings above (missing title, id owned by another tenant, and so on)",
    "unsupported_type": f"unsupported edge type; {_ALLOWED_PAIRS_HINT}",
}

#: 접힌 경고에 실을 예시 개수. 전부 실으면 접은 의미가 없고, 하나도 안 실으면
#: 어느 엣지인지 못 찾는다.
_EDGE_WARN_SAMPLE = 8


def _warn_edge(warnings: list[str], rollup: dict[str, list[str]] | None,
               reason: str, label: str, full: str) -> None:
    """엣지 경고 하나. ``rollup`` 이 있으면 접어 두고(호출부가 flush), 없으면
    종전대로 한 줄 낸다 — `_merge_edge` 를 직접 부르는 쪽의 동작을 바꾸지 않는다."""
    if rollup is None:
        warnings.append(full)
    else:
        rollup.setdefault(reason, []).append(label)


def _flush_edge_warnings(rollup: dict[str, list[str]], warnings: list[str]) -> None:
    """이유별로 접어 둔 엣지 경고를 한 줄씩 낸다(위 표 주석 참조)."""
    for reason, labels in rollup.items():
        head = labels[:_EDGE_WARN_SAMPLE]
        rest = len(labels) - len(head)
        warnings.append(
            f"{len(labels)} edge(s) skipped — {_EDGE_WARN_REASONS[reason]}: "
            f"{', '.join(head)}" + (f" (+{rest} more)" if rest > 0 else ""))


def _edge_exists(store, rel: str, ends: _Ends, src: str, dst: str,
                 *, owner: str | None = None, osc: bool = False) -> bool:
    """이 엣지가 **이미 있었나**. 반환값은 `was` 로 쓰여 스탬프 여부를 가른다.

    SEC-3: owner 강제 경로(osc)에서 owner 를 안 걸면 **남의 엣지**를 "이미 있다"로
    읽어 스탬프를 건너뛴다. 그러면 내 엣지는 새로 생겼는데 증분 동기화에 안 실려
    다른 기기에 영영 안 넘어간다(오류도 경고도 없다). `_merge_edge` 의 MERGE 는
    이미 같은 조건으로 스코프돼 있으므로 여기만 빠져 있던 것이다."""
    (sl, sp), (dl, dp) = ends
    own = f" AND a.owner_id = $owner AND b.owner_id = $owner" if osc else ""
    r = store.run(f"MATCH (a:{sl})-[:{rel}]->(b:{dl}) "
                  f"WHERE a.{sp} = $s AND b.{dp} = $d{own} RETURN count(*) AS c",
                  {"s": src, "d": dst, **({"owner": owner} if osc else {})})
    return bool(r and r[0].get("c"))


def _edge_values(store, rel: str, ends: _Ends, src: str, dst: str,
                 *, owner: str | None = None, osc: bool = False) -> dict | None:
    """저장된 엣지의 근거·신뢰도. 엣지가 없으면 None — `_edge_exists` 와 같은 스코프다.

    근거 열이 없는 rel 은 존재만 본다(빈 dict). 값 비교는 `_edge_values_change` 가 한다."""
    if rel not in EDGE_EVIDENCE_RELS:
        return {} if _edge_exists(store, rel, ends, src, dst, owner=owner, osc=osc) else None
    (sl, sp), (dl, dp) = ends
    own = f" AND a.owner_id = $owner AND b.owner_id = $owner" if osc else ""
    rows = store.run(f"MATCH (a:{sl})-[r:{rel}]->(b:{dl}) "
                     f"WHERE a.{sp} = $s AND b.{dp} = $d{own} "
                     "RETURN r.evidence AS ev, r.confidence AS conf",
                     {"s": src, "d": dst, **({"owner": owner} if osc else {})})
    if not rows:
        return None
    return {"evid": rows[0].get("ev"), "conf": rows[0].get("conf")}


def _edge_values_change(stored: dict | None, ev_p: dict) -> bool:
    """이번 쓰기가 끝점 스탬프를 받아야 하나: 새 엣지이거나 근거·신뢰도가 **실제로** 바뀔 때.

    같은 값의 재전송은 바꾸지 않는다 — 스탬프하면 재전송마다 다음 델타에 메아리가 실린다.
    ``ev_p`` 는 `_ev_set` 이 SET 할 값(`evid`·`conf`)이고, 실리지 않은 키는 바뀌지 않는다.
    근거의 빈 문자열과 없음(NULL)은 같은 값으로 본다."""
    if stored is None:
        return True
    if "evid" in ev_p and (ev_p["evid"] or "") != (stored.get("evid") or ""):
        return True
    if "conf" in ev_p:
        held = stored.get("conf")
        return held is None or float(held) != ev_p["conf"]
    return False


def stamp_edge_endpoints(store, rel: str, ends: _Ends, src: str, dst: str,
                         owner: str, *, osc: bool = False,
                         warnings: list[str] | None = None) -> None:
    """엣지 **신규 생성**(또는 근거·신뢰도 변경) 시 양 끝 노드에 revstamp — 엣지 자체는 추적열이 없어,
    끝점 스탬프가 없으면 '기존 노드 사이 신규/재생성 엣지'가 증분 동기화에 영영 안
    실린다(delta 번들의 엣지는 양끝 노드가 델타에 있을 때만 동봉). 엣지 활동 =
    끝점 활동으로 간주한다 — 부수 효과로 '편집이 삭제를 이긴다' 가드에서도 엣지
    활동이 노드 편집처럼 취급된다(의도된 의미론). 동시에 같은 (src,rel,dst) 의
    로컬 엣지 툼스톤을 상쇄한다(재생성이 낡은 삭제 표식을 이긴다).

    ``ends`` 는 호출부가 해석한 끝점 (라벨, PK) 쌍이다. v12 전에는 rel 라벨만 받아
    표에서 끌어왔는데, 그 표가 성립하려면 **rel 하나에 끝점 라벨 조합이 하나**여야
    한다. 결정 타입이 들어오면서 RELATED_TO 하나가 Concept↔Decision 네 조합을 갖게
    돼 그 전제가 깨졌다. 호출부는 이미 타입을 알고 있으므로 내려받는 편이 맞다.

    SEC-3(``osc``): owner 강제 경로에서는 **owner 를 걸어야 한다.** 안 걸면 남의
    노드에 rev/sync_seq 를 올려 그 사람의 다음 동기화에 유령 변경을 실어 보내고,
    더 나쁜 것은 아래 `_EdgeTombstone` DELETE 가 **남이 지운 엣지의 삭제 표식을
    지워** 그 엣지를 되살린다(삭제가 조용히 취소된다). 무조건 걸면 안 되는 이유는
    `_merge_edge` 와 같다 — owner 를 안 실은 로컬·서버 호출부(`api/server.py` 의
    POST 바디에 owner 가 없는 경우 등)가 자기 노드를 못 찾게 된다. 그래서 호출부의
    ``osc``(= owner_explicit and owner)를 그대로 물려받는다.

    PK 네임스페이싱(design A): ``src``/``dst`` 는 호출부가 이미 합성한 **물리 키**다
    (아래 MATCH 가 노드 PK 로 바인딩한다). 툼스톤 `ekey` 는 다르다 — `pkns.ekey` 가
    끝점을 논리로 되돌려 `src|REL|dst` 를 짓고 그 전체를 **한 번만** 합성한다."""
    own = " AND n.owner_id = $owner" if osc else ""
    op = {"owner": owner} if osc else {}
    batched = write_batch.current(store) is not None
    for (label, pk), nid in zip(ends, (src, dst)):
        if batched:
            # 쓰기 묶음 안: 읽기와 쓰기를 한 문장으로 한다. 시계·카운터는 메모리 값이므로 미리 받고,
            # 노드가 없으면(남의 노드) 받은 sync_seq 를 돌려준다. 결과는 아래 두 문장과 같다.
            seq = revstamp.next_seq(store, owner)
            rows = store.run(f"MATCH (n:{label}) WHERE n.{pk} = $id{own} "
                             "SET n.updated_at = $ua, n.rev = coalesce(n.rev, 0) + 1, "
                             "n.sync_seq = $seq RETURN n.rev AS rev",
                             {"id": nid, "ua": revstamp.hlc_now(store), "seq": seq, **op})
            if not rows:
                revstamp.give_back_seq(store, owner, seq)
        else:
            rows = store.run(f"MATCH (n:{label}) WHERE n.{pk} = $id{own} RETURN n.rev AS rev",
                             {"id": nid, **op})
        if not rows:
            if osc and warnings is not None:
                warnings.append(
                    f"edge endpoint not stamped — {nid} is not yours, so its revision "
                    "was left alone (the edge itself merged). Cross-owner edges do not "
                    "propagate: the other side never sees it")
            continue
        if batched:
            continue
        store.run(f"MATCH (n:{label}) WHERE n.{pk} = $id{own} "
                  "SET n.updated_at = $ua, n.rev = $rev, n.sync_seq = $seq",
                  {"id": nid, "ua": revstamp.hlc_now(store),
                   "rev": int(rows[0].get("rev") or 0) + 1,
                   "seq": revstamp.next_seq(store, owner), **op})
    # 루프 **밖**이다(끝점 하나가 남의 것이어도 내 툼스톤은 상쇄돼야 한다).
    # `_EdgeTombstone` 은 owner_id 열을 갖는다(store/embedded_schema.py:170-173) —
    # 예측자는 api/sync.py:140 에 이미 프로덕션으로 있다. `t.rev` 는 없다(그 열은
    # `_Tombstone` 전용).
    _drop_edge_tombstone(store, pkns.ekey(owner, src, rel, dst), osc=osc, op=op)


# 사설 별칭. 같은 함수다.
_stamp_edge_endpoints = stamp_edge_endpoints


def _edge_changed(store, rel: str, ends: _Ends, src: str, dst: str, owner: str, *, osc: bool,
                  warnings: list[str], stamp: bool, changed: list | None) -> None:
    """이 쓰기가 엣지를 새로 만들었거나 근거·신뢰도를 바꿨다. ``stamp`` 면 양 끝을 스탬프하고
    (`_stamp_edge_endpoints`), ``changed`` 가 있으면 양 끝 (라벨, PK 열, 물리 키)를 담는다.
    동기화 적용(``stamp_edges=False``)은 스탬프하지 않고 그 목록을 받아 끝점에 새 순번을 매긴다(`api.sync`)."""
    if stamp:
        _stamp_edge_endpoints(store, rel, ends, src, dst, owner, osc=osc, warnings=warnings)
    if changed is not None:
        changed.extend((label, pk, nid) for (label, pk), nid in zip(ends, (src, dst)))


def _drop_edge_tombstone(store, ekey: str, *, osc: bool, op: dict) -> None:
    """엣지가 (다시) 생겼으니 그 엣지의 삭제 표식을 지운다. 쓰기 묶음 안이면 그 owner 에게 표식이
    하나도 없을 때 지우기를 건너뛴다: 표식은 삭제 경로(`api.sync`·`api.query`)만 만들고 merge 는
    만들지 않으므로, 묶음 동안 한 번 센 값이 그대로다."""
    batch = write_batch.current(store)
    if batch is not None:
        key = ("edge_tombstones", op.get("owner") if osc else None)
        if key not in batch.memo:
            rows = store.run("MATCH (t:_EdgeTombstone)"
                             + (" WHERE t.owner_id = $owner" if osc else "")
                             + " RETURN count(t) AS c", op if osc else {})
            batch.memo[key] = bool(rows and int(rows[0].get("c") or 0))
        if not batch.memo[key]:
            return
    store.run("MATCH (t:_EdgeTombstone) WHERE t.ekey = $k"
              + (" AND t.owner_id = $owner" if osc else "") + " DELETE t",
              {"k": ekey, **op})


def _resolve_endpoint_type(store, nid: str, *, owner: str | None,
                           osc: bool) -> str:
    """엣지 끝점의 fragment 타입을 **스토어에서** 찾는다(v0.8.4 axis2 item1).

    여태 해석기는 이 호출의 `nodes` 배열 안만 봤다. 그래서 **이미 스토어에 있는**
    노드로 향하는 엣지가 스킵되고, 경고는 "대상을 다시 선언하라"고 안내했다 — 저작자가
    이미 아는 사실(그 노드는 있다)과 어긋나는 처방이었다.

    라벨은 id 접두사로 하나만 고른다(`concept:` → Concept). 접두사가 없으면 Concept
    (`api/query._seed_table` 과 같은 하위호환 규칙), 레거시 6종 밖이면 개방층 Entity 로
    보고 그 행의 `type` 열을 그대로 돌려준다. `domain:` 은 엣지 끝점이 되는 규칙이
    아예 없어 묻지 않는다.

    SEC-3(``osc``): owner 강제 경로에서는 소유로 건다. 안 걸면 남의 노드가 **존재하는지**
    알려 주고, 더 나쁜 것은 그 노드로 향하는 엣지를 해석해 준다. 프로브 실패(방언·
    미프로비저닝)는 종전 동작(미선언 경고)으로 조용히 강등한다 — 해석은 증강이다.
    근거: [[vinc.plan.node-pk-namespacing]] §7"""
    prefix = nid.split(":", 1)[0] if ":" in nid else ""
    if prefix == "domain":
        return ""
    own = " AND n.owner_id = $owner" if osc else ""
    params = {"id": _key(owner, nid), **({"owner": owner} if osc else {})}
    try:
        if not prefix or prefix in _NODE_MAP:
            ntype = prefix or "concept"
            label, pk = _NODE_MAP[ntype]
            rows = store.run(
                f"MATCH (n:{label}) WHERE n.{pk} = $id{own} RETURN count(n) AS c",
                params)
            return ntype if (rows and rows[0].get("c")) else ""
        rows = store.run(
            f"MATCH (n:{_ENTITY_LABEL}) WHERE n.{_ENTITY_ID} = $id{own} "
            "RETURN n.type AS t", params)
        return str(rows[0].get("t") or "") if rows else ""
    except Exception:
        return ""


class _EdgeCall:
    """엣지 하나를 쓰는 동안의 값: 원문 엣지, 끝점 키, owner 관문, 출생·근거 계획.

    각 단계 함수(`_merge_*_edge`)가 이것을 받아 같은 이름의 지역 변수로 풀어 쓴다."""

    def __init__(self, store, edge: dict, warnings: list[str], etype: str, src: str, dst: str, *,
                 owner: str | None, owner_explicit: bool, stamp_edges: bool,
                 changed: list | None, born):
        self.store, self.edge, self.warnings = store, edge, warnings
        self.etype, self.src, self.dst = etype, src, dst
        self.owner, self.stamp_edges, self.changed = owner, stamp_edges, changed
        self.osc = osc = bool(owner_explicit and owner)
        # PK 네임스페이싱(design A) — 스토어 바인딩용 물리 끝점 키. 논리 `src`/`dst` 는
        # 경고·`types` 조회·툼스톤 키에 그대로 남는다. 엣지 끝점이 될 수 있는 라벨은
        # 전부 owner-스코프다(Domain 타입은 어느 쌍 규칙에도 안 들어 아래 미지원으로
        # 떨어진다) — 그래서 `_merge_node` 처럼 라벨을 가릴 필요가 없다.
        #
        # owner 를 `DEFAULT_OWNER` 로 메우는 이유: 아래 `_stamp_edge_endpoints` 가 이미
        # 같은 코얼레스로 owner 를 받아 툼스톤 `ekey` 를 합성한다. 여기서 안 메우면
        # owner 없이 직접 부른 경로에서 노드 키는 논리인데 ekey 만 합성돼 삭제 표식이
        # 상쇄되지 않는다. 근거: [[vinc.plan.node-pk-namespacing]] §7
        self.eowner = eowner = owner or DEFAULT_OWNER
        self.ksrc, self.kdst = _key(eowner, src), _key(eowner, dst)
        #: 배치 하나의 엣지들은 **같은 순간에** 태어난다. 엣지마다 HLC 를 새로 뽑으면
        #: 메타 읽기·쓰기가 엣지 수만큼 늘고, 한 번의 적재가 시간축에 흩어져 보인다.
        self.born = born or (lambda: revstamp.hlc_now(store))
        self.op = {"owner": owner} if osc else {}

    def o(self, var: str) -> str:
        return f" AND {var}.owner_id = $owner" if self.osc else ""

    # v14 — **엣지 출생**. `ON CREATE SET` 이라 재-MERGE 는 안 덮는다. 상류가 실은
    # 값이 있으면 그것을, 없으면 이 배치의 시각을 쓴다. ⚠ 구 엣지는 ON CREATE 가 안
    # 뛰어 '' 로 남고 **그것이 맞다**([[record:edge-birth-v14]]).
    def birth_set(self, rel_var: str) -> tuple[str, dict]:
        return (f" ON CREATE SET {rel_var}.created = $ecreated",
                {"ecreated": str(self.edge.get("created") or "") or self.born()})

    # E-G6 — 선택 증거·신뢰도. 있으면 MERGE 뒤 SET 절로 부여. 열을 가진 rel 은
    # `EDGE_EVIDENCE_RELS`(스키마에서 파생 — CC 4종·ABOUT·v16 부터 ATTACHES_TO)이고,
    # **모든 엣지 분기가 이 함수를 부른다** — 열이 없는 rel 은 빈 절을 돌려받으므로
    # 쿼리는 그대로고, 대신 상류가 실어 보낸 값이 사라진다는 사실을 경고로 밝힌다.
    #
    # ⚠ 여기 경고가 없던 동안 CONNECTS·ATTACHES_TO·TYPED 는 근거를 **조용히** 버렸다.
    # GRAPH.md 는 record 엣지에 근거를 요구하는데 이 표면은 그것을 받지 않으므로,
    # 규율을 지킨 저작자가 아무 신호도 못 받고 문장을 잃었다. R1(미보관 필드는 버리지
    # 말고 경고)이 노드 경로에서만 닫혀 있었다:
    # [[record:attaches-to-carries-evidence-since-v16]]
    # (record 엣지는 v16 이 열을 줘서 경고 대상에서 저장 대상으로 옮겨 갔다 —
    # 남는 무열 rel 은 CONNECTS·TYPED 둘이다.)
    def ev_set(self, rel_var: str, rel: str) -> tuple[str, dict]:
        edge, warnings, src, dst = self.edge, self.warnings, self.src, self.dst
        ev, conf = edge.get("evidence"), edge.get("confidence")
        if rel not in EDGE_EVIDENCE_RELS:
            unkept = [n for n, v in (("evidence", ev), ("confidence", conf))
                      if v is not None]
            if unkept:
                warnings.append(
                    f"edge {' and '.join(unkept)} not stored on {rel} — that relation "
                    f"has no such column ({src}→{dst}); kept only on "
                    f"{', '.join(EDGE_EVIDENCE_RELS)}")
            return "", {}
        sets, p = [], {}
        if ev is not None:
            sets.append(f"{rel_var}.evidence = $evid"); p["evid"] = str(ev)
        if conf is not None:
            try:
                p["conf"] = float(conf); sets.append(f"{rel_var}.confidence = $conf")
            except (TypeError, ValueError):
                warnings.append(f"edge confidence not a number — ignored ({src}→{dst})")
        return ((" SET " + ", ".join(sets)) if sets else ""), p


# v12 — 끝점 라벨·PK 를 타입에서 꺼낸다. 아래 분기들이 라벨을 문자열로 박고
# 있었는데, 결정이 개념과 같은 자리에 서면서 조합이 늘어 그대로는 분기가
# 배로 불어난다. 자리(어느 층인가)만 보고 라벨은 표에서 꺼내면 분기는 그대로다.
def _edge_end(ntype: str) -> tuple[str, str]:
    return _NODE_MAP[ntype]


def _edge_screen(edge: dict, warnings: list[str]) -> tuple[str, str, str] | None:
    """형태 검사. 통과하면 `(타입, from, to)`, U+001F 가 섞였으면 경고하고 None."""
    etype = (edge.get("type") or "").strip()
    src, dst = (edge.get("from") or "").strip(), (edge.get("to") or "").strip()
    if pkns.has_sep(edge):
        # 노드와 같은 이유(_merge_node 주석) — U+001F 는 물리 키 판별자라 엣지
        # 끝점/타입에 섞이면 안 된다. 근거: [[vinc.plan.node-pk-namespacing]] §7
        warnings.append(
            f"edge contains U+001F (reserved for PK namespacing) — skipped ({src}→{dst})")
        return None
    return etype, src, dst


def _edge_endpoint_types(c: _EdgeCall, types: dict[str, str],
                         declared: set[str] | None) -> tuple[str, str]:
    """끝점 타입. 이 호출의 노드 표에 없으면 스토어에 묻는다(선언됐다가 건너뛴 끝점은 묻지 않는다)."""
    store, src, dst, eowner, osc = c.store, c.src, c.dst, c.eowner, c.osc
    st, dt = types.get(src, ""), types.get(dst, "")
    # axis2 item1 — 페이로드에 안 실린 끝점은 **스토어에 물어본다**. `declared` 에
    # 있는 것은 묻지 않는다: 그 노드는 방금 이 호출에서 스킵된 것이고(제목 없음·
    # 남의 id 등) 스토어의 옛 행으로 되살리면 저작자가 고쳐야 할 고장이 숨는다.
    # ``types`` 는 **건드리지 않는다** — 그 표는 "이 호출이 실제로 MERGE 한 노드"라는
    # 뜻이고 `_warn_orphan_decisions` 가 그 뜻에 기대고 있다.
    if src and not st and not (declared and src in declared):
        st = _resolve_endpoint_type(store, src, owner=eowner, osc=osc)
    if dst and not dt and not (declared and dst in declared):
        dt = _resolve_endpoint_type(store, dst, owner=eowner, osc=osc)
    return st, dt


def _merge_typed_edge(c: _EdgeCall) -> str | None:
    """개방층(Entity 끼리)의 TYPED{type} 엣지."""
    store, edge, warnings, etype, src, dst = c.store, c.edge, c.warnings, c.etype, c.src, c.dst
    owner, eowner, osc, ksrc, kdst, op = c.owner, c.eowner, c.osc, c.ksrc, c.kdst, c.op
    stamp_edges, changed = c.stamp_edges, c.changed
    _o, _birth_set, _ev_set, _end = c.o, c.birth_set, c.ev_set, _edge_end
    if not etype:
        warnings.append(f"edge without type — skipped ({src}→{dst})")
        return None
    _ev_set("r", "TYPED")          # 열 없음 — 경고만 남기고 절은 버린다
    b_set, b_p = _birth_set("r")
    store.run(
        f"MATCH (a:{_ENTITY_LABEL}), (b:{_ENTITY_LABEL}) "
        f"WHERE a.{_ENTITY_ID} = $s AND b.{_ENTITY_ID} = $d{_o('a')}{_o('b')} "
        f"MERGE (a)-[r:{_TYPED_REL} {{type: $t}}]->(b){b_set}",
        {"s": ksrc, "d": kdst, "t": etype, **op, **b_p})
    return "created"


def _edge_refused(c: _EdgeCall, st: str, dt: str, declared: set[str] | None,
                  rollup: dict[str, list[str]] | None) -> bool:
    """끝점이 안 풀렸거나, 모르는 타입이거나, 반려 경로의 조건이 안 맞으면 경고하고 True."""
    edge, warnings, etype, src, dst = c.edge, c.warnings, c.etype, c.src, c.dst
    # T4 — **대상 미선언**과 **타입 불일치**를 구분한다. 해석기는 이 호출의 `nodes`
    # 배열에서만 타입을 찾으므로(스토어 조회 없음), 이미 있는 노드로 향하는 엣지도
    # id 를 함께 싣지 않으면 타입이 '' 이 되어 쌍 검사가 전부 빗나간다. 둘을 뭉개면
    # 저작자가 고칠 방법을 모른다: [[record:endpoint-types-resolve-only-within-one-payload]].
    unresolved = [n for n, t in ((src, st), (dst, dt)) if n and not t]
    if unresolved:
        # 하나라도 '선언은 됐는데 스킵된' 것이면 그쪽을 알린다 — 그게 고칠 자리다.
        reason = ("skipped_endpoint"
                  if declared and any(n in declared for n in unresolved)
                  else "undeclared_endpoint")
        _warn_edge(warnings, rollup, reason, ", ".join(unresolved),
                   f"edge '{etype}' skipped — {', '.join(unresolved)} "
                   f"({st or '?'}→{dt or '?'}): {_EDGE_WARN_REASONS[reason]}")
        return True

    if etype not in _EDGE_TYPES:
        _warn_edge(warnings, rollup, "unsupported_type", f"{src}→{dst} ({etype})",
                   f"unsupported edge type '{etype}' — skipped ({src}→{dst}); "
                   f"{_ALLOWED_PAIRS_HINT}")
        return True

    # 반려 경로는 역할 사이에 저작하며 근거가 있어야 한다. Record/Decision
    # 경로로 접거나 일반 개념에 붙이면 검토 대상 역할이 바뀐다.
    if etype == "returns_to" and (
            st != "concept" or dt != "concept" or src == dst
            or not role_contract.is_role_id(src) or not role_contract.is_role_id(dst)
            or not isinstance(edge.get("evidence"), str) or not edge["evidence"].strip()):
        warnings.append("returns_to expects distinct role concepts and nonempty evidence; "
                        f"skipped ({src}→{dst})")
        return True
    return False


def _merge_connects_edge(c: _EdgeCall, st: str, dt: str) -> str | None:
    """다리 → 개념|결정의 CONNECTS(선택 role 이 MERGE 패턴에 든다)."""
    store, edge, warnings, etype, src, dst = c.store, c.edge, c.warnings, c.etype, c.src, c.dst
    owner, eowner, osc, ksrc, kdst, op = c.owner, c.eowner, c.osc, c.ksrc, c.kdst, c.op
    stamp_edges, changed = c.stamp_edges, c.changed
    _o, _birth_set, _ev_set, _end = c.o, c.birth_set, c.ev_set, _edge_end
    if st != "bridge" or dt not in _SEMANTIC_TYPES:
        warnings.append(
            f"{_ERR_DISALLOWED_PAIR}: connects_via expects bridge→concept|decision — "
            f"skipped ({st or '?'}→{dt or '?'}); {_ALLOWED_PAIRS_HINT}")
        return None
    (dl, dp) = _end(dt)
    # F9: 선택 role — 있으면 MERGE 패턴에 참여(bridges_ingest 의미론 동형: 같은
    # role 멱등, 다른 role 병렬 엣지). 없으면 bare MERGE(기존 role 엣지에 no-op).
    # role 기본값 조작 금지 — `{dom}_input` 관례는 bridges_ingest 전속.
    role = (edge.get("role") or "").strip()
    _ev_set("r", "CONNECTS")       # 열 없음 — 경고만
    b_set, b_p = _birth_set("r")
    if role:
        store.run(
            f"MATCH (a:Bridge), (b:{dl}) "
            f"WHERE a.bridge_id = $s AND b.{dp} = $d{_o('a')}{_o('b')} "
            f"MERGE (a)-[r:CONNECTS {{role: $role}}]->(b){b_set}",
            {"s": ksrc, "d": kdst, "role": role, **op, **b_p})
    else:
        store.run(
            f"MATCH (a:Bridge), (b:{dl}) "
            f"WHERE a.bridge_id = $s AND b.{dp} = $d{_o('a')}{_o('b')} "
            f"MERGE (a)-[r:CONNECTS]->(b){b_set}",
            {"s": ksrc, "d": kdst, **op, **b_p})
    return "created"


def _merge_supersedes_edge(c: _EdgeCall, st: str, dt: str) -> str | None:
    """대체 엣지: 같은 종류에서 새 주장이 대체한 주장을 가리킨다. 근거 필수이고 구간은 경고만 한다."""
    store, edge, warnings, etype, src, dst = c.store, c.edge, c.warnings, c.etype, c.src, c.dst
    owner, eowner, osc, ksrc, kdst, op = c.owner, c.eowner, c.osc, c.ksrc, c.kdst, c.op
    stamp_edges, changed = c.stamp_edges, c.changed
    _o, _birth_set, _ev_set, _end = c.o, c.birth_set, c.ev_set, _edge_end
    ok = (st == dt and st in _SUPERSEDES_KINDS and src != dst
          and (st != "concept" or (src.startswith("concept:doc.")
                                   and dst.startswith("concept:doc.")))
          and isinstance(edge.get("evidence"), str) and edge["evidence"].strip())
    if not ok:
        warnings.append(
            f"{_ERR_DISALLOWED_PAIR}: supersedes expects two distinct nodes of the same "
            "kind (record, decision, or concept:doc.* document mirrors) and nonempty "
            f"evidence — skipped ({st or '?'}→{dt or '?'})")
        return None
    ends = (_end(st), _end(dt))
    (sl, sp), (dl, dp) = ends
    was = _edge_values(store, _SUPERSEDES_REL, ends, ksrc, kdst, owner=owner, osc=osc)
    ev_set, ev_p = _ev_set("r", _SUPERSEDES_REL)
    b_set, b_p = _birth_set("r")
    store.run(
        f"MATCH (a:{sl}), (b:{dl}) "
        f"WHERE a.{sp} = $s AND b.{dp} = $d{_o('a')}{_o('b')} "
        f"MERGE (a)-[r:{_SUPERSEDES_REL}]->(b){b_set}{ev_set}",
        {"s": ksrc, "d": kdst, **op, **ev_p, **b_p})
    if _edge_values_change(was, ev_p):
        _edge_changed(store, _SUPERSEDES_REL, ends, ksrc, kdst, eowner, osc=osc,
                      warnings=warnings, stamp=stamp_edges, changed=changed)
    _warn_supersede_interval(store, st, ends, ksrc, kdst, src, dst, warnings)
    return "created"


def _merge_about_edge(c: _EdgeCall, st: str, dt: str) -> str | None:
    """사건·결정 → 대상의 ABOUT."""
    store, edge, warnings, etype, src, dst = c.store, c.edge, c.warnings, c.etype, c.src, c.dst
    owner, eowner, osc, ksrc, kdst, op = c.owner, c.eowner, c.osc, c.ksrc, c.kdst, c.op
    stamp_edges, changed = c.stamp_edges, c.changed
    _o, _birth_set, _ev_set, _end = c.o, c.birth_set, c.ev_set, _edge_end
    # F4 — episodic↔semantic 다리. 비대칭 쌍이라 역방향 dedupe 불요(ATTACHES_TO
    # 동형). 삭제 전파는 엣지 툼스톤(S4 확장 — graph_edit.delete_edge)이 담당한다.
    #
    # v12 — 출발점에 decision 이 붙는다. `about` 은 "X 는 Y 에 관한 것"이고,
    # 결정이 어떤 개념을 두고 내려졌다는 말이 정확히 그 형태다.
    targets = _PAGE_TYPES if wiki_pairs_open(owner) else _SEMANTIC_TYPES
    if st not in ("episode", "decision") or dt not in targets:
        warnings.append(
            f"{_ERR_DISALLOWED_PAIR}: about expects episode|decision→concept|decision — "
            f"skipped ({st or '?'}→{dt or '?'}); {_ALLOWED_PAIRS_HINT}")
        return None
    rel = _EC_EDGES[etype]
    (sl, sp), (dl, dp) = ends = (_end(st), _end(dt))
    was = _edge_values(store, rel, ends, ksrc, kdst, owner=owner, osc=osc)
    ev_set, ev_p = _ev_set("r", rel)
    b_set, b_p = _birth_set("r")
    store.run(
        f"MATCH (a:{sl}), (b:{dl}) "
        f"WHERE a.{sp} = $s AND b.{dp} = $d{_o('a')}{_o('b')} "
        f"MERGE (a)-[r:{rel}]->(b){b_set}{ev_set}",
        {"s": ksrc, "d": kdst, **op, **ev_p, **b_p})
    if _edge_values_change(was, ev_p):
        _edge_changed(store, rel, ends, ksrc, kdst, eowner, osc=osc,
                      warnings=warnings, stamp=stamp_edges, changed=changed)
    return "created"


def _merge_semantic_edge(c: _EdgeCall, st: str, dt: str, wiki: bool) -> str | None:
    """의미층(개념·결정) 사이 엣지와 커뮤니티 위키 관계. relates_to 는 역방향 중복을 잇지 않는다."""
    store, edge, warnings, etype, src, dst = c.store, c.edge, c.warnings, c.etype, c.src, c.dst
    owner, eowner, osc, ksrc, kdst, op = c.owner, c.eowner, c.osc, c.ksrc, c.kdst, c.op
    stamp_edges, changed = c.stamp_edges, c.changed
    _o, _birth_set, _ev_set, _end = c.o, c.birth_set, c.ev_set, _edge_end
    if etype == "relates_to" and "decision" in (st, dt) and not wiki:
        # relates_to 만 개념↔개념에 남는다(embedded_schema._LOUVAIN_PROJECTED_REL).
        # 엔진 제약이자 의미상 옳은 자리다 — 결정은 개념에 **관한** 것이고
        # (about), 결정끼리는 **계보**(derives_from · prerequisite_of)다.
        # 조용히 다른 타입으로 바꿔 쓰지 않는다: 사용자가 적은 관계를 서버가
        # 재해석하면 그래프가 사용자가 믿는 것과 달라진다.
        warnings.append(
            f"{_ERR_DISALLOWED_PAIR}: relates_to is concept→concept only — skipped "
            f"({st}→{dt}); for a decision use `about` (decision→concept) or "
            "`derives_from`/`prerequisite_of` (decision lineage)")
        return None
    (sl, sp), (dl, dp) = ends = (_end(st), _end(dt))
    if etype == "relates_to":
        # RELATED_TO 는 의미상 무방향 — 역방향 (b)-[:RELATED_TO]->(a) 가 이미
        # 있으면 신규 생성을 skip(양방향 재적재로 degree 2배 부풀림 방지).
        rev = store.run(
            f"MATCH (b:{dl})-[:RELATED_TO]->(a:{sl}) "
            f"WHERE b.{dp} = $d AND a.{sp} = $s{_o('b')}{_o('a')} "
            "RETURN count(*) AS c",
            {"s": ksrc, "d": kdst, **op})
        if rev and rev[0].get("c"):
            # 새 엣지는 만들지 않지만 실어 온 근거·신뢰도는 저장된 방향의 엣지에
            # 쓴다. 정방향 재전송과 같은 결과여야 한다(무방향 MATCH 로 SET 하지 않는다).
            ev_set, ev_p = _ev_set("r", "RELATED_TO")
            if ev_set:
                # 저장된 방향의 끝점 순서로 읽고 스탬프한다(값이 바뀔 때만).
                rends = ((dl, dp), (sl, sp))
                held = _edge_values(store, "RELATED_TO", rends, kdst, ksrc,
                                    owner=owner, osc=osc)
                store.run(
                    f"MATCH (b:{dl})-[r:RELATED_TO]->(a:{sl}) "
                    f"WHERE b.{dp} = $d AND a.{sp} = $s{_o('b')}{_o('a')}{ev_set}",
                    {"s": ksrc, "d": kdst, **op, **ev_p})
                if _edge_values_change(held, ev_p):
                    _edge_changed(store, "RELATED_TO", rends, kdst, ksrc, eowner, osc=osc,
                                  warnings=warnings, stamp=stamp_edges, changed=changed)
            return "skipped_duplicate"
    rel = _CC_EDGES[etype]
    was = _edge_values(store, rel, ends, ksrc, kdst, owner=owner, osc=osc)
    ev_set, ev_p = _ev_set("r", rel)
    b_set, b_p = _birth_set("r")
    store.run(
        f"MATCH (a:{sl}), (b:{dl}) "
        f"WHERE a.{sp} = $s AND b.{dp} = $d{_o('a')}{_o('b')} "
        f"MERGE (a)-[r:{rel}]->(b){b_set}{ev_set}",
        {"s": ksrc, "d": kdst, **op, **ev_p, **b_p})
    if _edge_values_change(was, ev_p):
        _edge_changed(store, rel, ends, ksrc, kdst, eowner, osc=osc,
                      warnings=warnings, stamp=stamp_edges, changed=changed)
    return "created"


def _merge_record_edge(c: _EdgeCall, dt: str) -> str | None:
    """기록 → 개념|결정의 ATTACHES_TO{type}."""
    store, edge, warnings, etype, src, dst = c.store, c.edge, c.warnings, c.etype, c.src, c.dst
    owner, eowner, osc, ksrc, kdst, op = c.owner, c.eowner, c.osc, c.ksrc, c.kdst, c.op
    stamp_edges, changed = c.stamp_edges, c.changed
    _o, _birth_set, _ev_set, _end = c.o, c.birth_set, c.ev_set, _edge_end
    # Record 참여 엣지 — ATTACHES_TO{type}로 원 타입 보존(스키마 §5-2 주석).
    # (from=Record만 허용되는 비대칭 rel 테이블이라 역방향 MERGE 자체가 불가능
    # — RELATED_TO 같은 무방향 dedupe 는 이 경로에 해당 없음.)
    (dl, dp) = _end(dt)
    # v16 — 근거·신뢰도 저장(GRAPH.md 가 record 엣지에 요구하는 그 필드).
    # 원 타입은 MERGE 패턴에 넣는다(TYPED·CONNECTS 와 같다). 패턴 밖에서 SET 하면
    # 같은 쌍의 다른 타입 부착이 한 엣지로 접혀 앞의 타입·근거를 덮는다.
    ev_set, ev_p = _ev_set("r", "ATTACHES_TO")
    b_set, b_p = _birth_set("r")
    held = None
    if changed is not None:
        # 같은 타입의 부착만 본다(다른 타입은 다른 엣지다). 목록을 받을 때만 읽는다.
        rows = store.run(
            f"MATCH (a:Record)-[r:ATTACHES_TO]->(b:{dl}) "
            f"WHERE a.record_id = $s AND b.{dp} = $d AND r.type = $t{_o('a')}{_o('b')} "
            "RETURN r.evidence AS ev, r.confidence AS conf",
            {"s": ksrc, "d": kdst, "t": etype, **op})
        held = {"evid": rows[0].get("ev"), "conf": rows[0].get("conf")} if rows else None
    store.run(
        f"MATCH (a:Record), (b:{dl}) "
        f"WHERE a.record_id = $s AND b.{dp} = $d{_o('a')}{_o('b')} "
        f"MERGE (a)-[r:ATTACHES_TO {{type: $t}}]->(b){b_set}{ev_set}",
        {"s": ksrc, "d": kdst, "t": etype, **op, **ev_p, **b_p})
    if changed is not None and _edge_values_change(held, ev_p):
        _edge_changed(store, "ATTACHES_TO", (("Record", "record_id"), (dl, dp)), ksrc, kdst,
                      eowner, osc=osc, warnings=warnings, stamp=False, changed=changed)
    if stamp_edges:
        _stamp_edge_endpoints(store, "ATTACHES_TO", (("Record", "record_id"), (dl, dp)),
                              ksrc, kdst, eowner, osc=osc, warnings=warnings)
        _drop_edge_tombstone(store, pkns.ekey(eowner, ksrc, "ATTACHES_TO:" + etype, kdst),
                             osc=osc, op=op)
    return "created"


def _merge_edge(store, edge: dict, types: dict[str, str], warnings: list[str],
                *, owner: str | None = None, owner_explicit: bool = False,
                stamp_edges: bool = True,
                rollup: dict[str, list[str]] | None = None,
                declared: set[str] | None = None,
                born=None, changed: list | None = None) -> str | None:
    """단일 엣지 MERGE. 반환 = 'created' | 'skipped_duplicate' | None(스킵).

    ``changed``: 주면 이 MERGE 가 새로 만들었거나 근거·신뢰도를 바꾼 엣지의 양 끝을 담는다(`_edge_changed`).

    SEC-3: owner_explicit(강제 owner 경로)면 owner-스코프 끝점(Concept/Record/Bridge/
    Episode/Entity)을 owner_id 로 스코프한다 — 내 fragment 가 남의 노드 ID 를
    참조해도 그 노드로 향한 엣지는 MATCH 되지 않아 no-op(남의 서브그래프에 엣지
    주입 차단). Bridge 는 v2 에서 owner_id 를 얻어 스코프에 편입(구 'Concept 쪽만'
    주석 폐기). 로컬(owner_explicit=False)은 현행.

    개방층(F1): 양 끝이 모두 개방 타입(레거시 6종 밖)이면 엣지 타입 무관하게
    TYPED{type} 캐리어로 exact (from,type,to) MERGE — rel 속성이 매칭 키에 참여해
    같은 쌍에 type 이 다른 엣지가 공존한다. 미지 타입 skip 은 레거시 끝점 조합에만
    남는다(선언된 FROM/TO 쌍 밖 — 스킵이 아니라 토폴로지 한계)."""
    screened = _edge_screen(edge, warnings)
    if screened is None:
        return None
    etype, src, dst = screened
    c = _EdgeCall(store, edge, warnings, etype, src, dst, owner=owner,
                  owner_explicit=owner_explicit, stamp_edges=stamp_edges, changed=changed,
                  born=born)
    st, dt = _edge_endpoint_types(c, types, declared)
    # 개방층 우선(F1): 양 끝이 Entity 라우팅이면 알려진 타입명(relates_to 등)이어도
    # TYPED 로 간다 — Entity 테이블 간에는 레거시 rel 테이블이 존재하지 않는다.
    if _is_open(st) and _is_open(dt):
        return _merge_typed_edge(c)

    if _edge_refused(c, st, dt, declared, rollup):
        return None
    if etype == "connects_via":
        return _merge_connects_edge(c, st, dt)
    if etype == _SUPERSEDES_EDGE:
        return _merge_supersedes_edge(c, st, dt)
    if etype in _EC_EDGES:
        return _merge_about_edge(c, st, dt)
    # 커뮤니티 위키의 관계는 종류와 무관하게 이 표로 간다. 기록 출발도 ATTACHES_TO 가 아니다:
    # 반영기가 그래프를 rel 이름으로 읽어 지난 관계를 지우기 때문이다.
    wiki = (etype in _WIKI_EDGES and st in _PAGE_TYPES and dt in _PAGE_TYPES
            and wiki_pairs_open(owner))
    if wiki or (st in _SEMANTIC_TYPES and dt in _SEMANTIC_TYPES):
        return _merge_semantic_edge(c, st, dt, wiki)
    if st == "record" and dt in _SEMANTIC_TYPES:
        return _merge_record_edge(c, dt)
    # 여기 도달 = 양 끝 타입이 **선언돼 있고** 쌍이 rel 테이블 밖(진짜 토폴로지 한계).
    warnings.append(
        f"{_ERR_DISALLOWED_PAIR}: edge '{etype}' between {st}→{dt} 미지원 — skipped; "
        f"{_ALLOWED_PAIRS_HINT}")
    return None


def _interval_of(store, kind: str, label_pk: tuple[str, str], key: str) -> tuple[str, str] | None:
    """노드의 저작된 유효구간. Record 는 열, Decision 은 props(정책상 그 자리)다.
    Concept 은 구간이 없어 None — 대체 경고를 내지 않는다."""
    label, pk = label_pk
    if kind == "record":
        rows = store.run(f"MATCH (n:{label}) WHERE n.{pk} = $k "
                         "RETURN n.valid_from AS vf, n.valid_until AS vu", {"k": key})
        return (str(rows[0].get("vf") or ""), str(rows[0].get("vu") or "")) if rows else None
    if kind == "decision":
        rows = store.run(f"MATCH (n:{label}) WHERE n.{pk} = $k RETURN n.props AS p", {"k": key})
        if not rows:
            return None
        try:
            props = json.loads(rows[0].get("p") or "{}") if isinstance(rows[0].get("p"), str) \
                else (rows[0].get("p") or {})
        except ValueError:
            props = {}
        return (str(props.get("valid_from") or ""), str(props.get("valid_until") or ""))
    return None


def _warn_supersede_interval(store, kind, ends, ksrc, kdst, src, dst, warnings) -> None:
    """대체 엣지의 구간 정합 경고. **값을 쓰지 않는다** — 판정은 저작자 몫이다."""
    new, old = (_interval_of(store, kind, ends[0], ksrc),
                _interval_of(store, kind, ends[1], kdst))
    if new is None or old is None:
        return
    vf, vu = _INTERVAL_FIELDS[kind]
    if not old[1]:
        how = (" (send props_patch with expected_rev so the other props stay)"
               if kind == "decision" else "")
        warnings.append(f"supersedes {src}→{dst}: the old {kind} has no {vu}; "
                        f"set {vu} to the date the new one starts{how} so it leaves "
                        "the current read")
    elif new[0] and old[1] != new[0]:
        warnings.append(f"supersedes {src}→{dst}: old {vu} {old[1]} differs from "
                        f"new {vf} {new[0]}")


def _warn_orphan_decisions(store, types: dict[str, str], warnings: list[str],
                           *, owner: str | None = None) -> None:
    """v0.8.4 axis2 item10 — `about` 없는 결정에 경고.

    결정은 "그때 무엇을 골랐고 왜인가"이고, 그 **무엇**으로 가는 유일한 길이
    `about` 이다. 없으면 노드는 멀쩡히 앉아 있는데 `vinc_decide` 가 영영 못 꺼낸다
    — 그 도구는 주제·개념에서 출발해 걸어 나가기 때문이다. 오류도 경고도 없이
    조용히 안 보이는 형태라, 쓰기 시점에 말해 주지 않으면 아무도 안 잡는다.

    **이 호출이 실제로 MERGE 한 결정만** 본다(``types`` 는 성공한 노드로만 지어진다)
    — 스토어의 기존 고아 전부를 세면 무관한 호출마다 같은 경고가 쏟아진다.
    판정은 스토어 조회다: 이 호출의 엣지로 이었든 앞선 호출에서 이미 이어 뒀든
    ABOUT 이 하나라도 있으면 고아가 아니다. 그래서 **엣지 루프 뒤**에 돈다.

    ``owner``: 쓰기 경로에 있는 **조회**라 PK 네임스페이싱을 함께 태워야 한다 —
    안 태우면 플래그가 켜진 스토어에서 이 조회가 항상 0행이라 방금 정상으로 이은
    결정에까지 고아 경고가 붙는다([[vinc.plan.node-pk-namespacing]] §7).

    커뮤니티 위키의 결정은 페이지라 위키 관계로 닿는다. 거기서는 묻지 않는다: 반영기는
    경고 하나로도 반영을 실패로 본다."""
    if wiki_pairs_open(owner):
        return
    for nid, ntype in types.items():
        if ntype != "decision":
            continue
        r = store.run("MATCH (a:Decision)-[:ABOUT]->() WHERE a.decision_id = $id "
                      "RETURN count(*) AS c", {"id": _key(owner, nid)})
        if r and r[0].get("c"):
            continue
        warnings.append(
            f"{nid} has no 'about' edge — vinc_decide on a concept will never reach "
            f"it (only naming the decision itself does). Add "
            f'{{"from": "{nid}", "to": "concept:...", "type": "about"}}.')


def _finalize(result: dict, warnings: list[str]) -> dict:
    """warnings 를 응답에 얹고, 1개 이상이면 partial:True 를 추가한다(실사용 사고 §1 —
    executed:True 만 보고 전량 성공으로 오인하는 것을 막음). warnings 없으면 partial
    필드 자체를 생략해 기존 응답과 하위호환을 유지한다."""
    result["warnings"] = warnings
    if warnings:
        result["partial"] = True
    return result


def _domain_gate(fragment_nodes: list[dict], *, store, owner: str,
                 max_domains: int) -> dict | None:
    """주제 개수 상한. 통과면 None, 걸리면 **거부 사유 dict**.

    막는 것은 **새 주제 생성뿐**이다(AP-1 §3.2). 기존 주제에 노드를 더하는 것,
    편집, 주제 없는 노드는 언제나 통과한다 — 그래서 상한을 넘긴 채로 이미 쓰고
    있던 사용자가 자동으로 유예된다(데이터 삭제·강제 이관 없음).

    거부는 **프래그먼트 통째로** 한다. 새 주제 둘 중 하나만 받아 넣으면 사용자는
    자기가 뭘 가졌는지 모르는 상태가 되고, 재시도가 멱등하지 않게 된다.
    """
    incoming = {d for d in ((n.get("domain") or "").strip() for n in fragment_nodes) if d}
    # 예약 주제(`agent`·`vinc/agent`)는 상한 밖이다 — 들어오는 쪽과 이미 있는 쪽 **양쪽**에서
    # 뺀다. 한쪽만 빼면 기본 Agent 팩이 들어간 뒤 사용자의 남은 주제가 넷이 된다.
    incoming = {d for d in incoming if not domain_hierarchy.is_reserved(d)}
    if not incoming:
        return None                       # 주제 없는 적재는 상한과 무관
    existing = domain_names(owner=owner, store=store)
    if existing is None:
        return None                       # 스토어 미도달 = 판정 불가 → 막지 않는다
    existing = {d for d in existing if not domain_hierarchy.is_reserved(d)}
    fresh = incoming - existing
    if not fresh or len(existing) + len(fresh) <= max_domains:
        return None
    return {"error": "domain_limit_reached", "limit": max_domains,
            "current": len(existing), "attempted": sorted(fresh)}


def _mention_fields(store, owner: str, ids: list[str]) -> dict | None:
    """개념·결정의 링크를 셀 재료(제목·stem·출처). 읽지 못하면 None 이고, 호출부는 전부 다시 센다."""
    if not ids:
        return {}
    try:
        from engines.mentions_link import link_fields
        return link_fields(store, owner, ids)
    except Exception:
        return None


def _link_mentions(store, owner: str, ids: list[str], before: dict | None) -> None:
    """새로 생기거나 제목·stem·출처가 바뀐 개념·결정을 owner 의 **이미 있는** 청크에 잇는다.

    `put_doc` 은 문서 쪽에서만 링크를 세므로, 이 단계가 없으면 문서보다 늦게 들어온 개념은
    링크가 0이고 발췌가 전부 제목 전문 검색 폴백으로 간다. 재료가 그대로인 노드는 건너뛴다
    (다시 보낸 개념마다 청크를 훑지 않게). 실패는 쓰기를 무효로 만들지 않는다 — 링크는
    증강이다. 규칙과 쓰기는 링커의 것(`engines.mentions_link.link_nodes`) 하나다."""
    if not ids:
        return
    try:
        from engines.mentions_link import link_fields, link_nodes
        after = link_fields(store, owner, ids)
        changed = [i for i in ids if before is None or after.get(i) != before.get(i)]
        if changed:
            link_nodes(store, owner=owner, ids=changed)
    except Exception as exc:
        import sys
        print(f"[fragment] mentions not linked ({len(ids)} nodes): {exc}", file=sys.stderr)


#: 이보다 노드·엣지가 많은 fragment 는 트랜잭션 하나로 묶지 않고 한 문장씩 쓴다(`merge_fragment`).
BATCH_MAX_ITEMS = 20_000


def _item_count(fragment) -> int:
    if not isinstance(fragment, dict):
        return 0
    return sum(len(v) for v in (fragment.get("nodes"), fragment.get("edges")) if isinstance(v, list))


def merge_fragment(fragment: dict, *, store, themes=None,
                   stamp_edges: bool = True,
                   max_domains: int | None = None,
                   read_back: bool = False,
                   authored: bool = False,
                   edge_ends: list | None = None) -> dict:
    """fragment 전체 MERGE 를 트랜잭션 하나로 쓴다(`store.write_batch`). 계약은 :func:`_merge_fragment_once`.

    커밋이 한 번이라 문장마다 WAL 을 디스크에 내리지 않고, 도중에 예외가 나면 아무것도 남지 않는다.

    ⚠ 이 함수는 몇몇 문장의 실패를 **삼키고 이어 간다**(노드 하나만 건너뛰기·도메인 고리·링크).
    트랜잭션 안에서는 실패한 문장이 트랜잭션 전체를 되돌리므로, 그런 실패가 하나라도 있었으면
    묶음을 버리고 한 문장씩 다시 쓴다. 결과는 묶기 전과 같다. `VINC_FRAGMENT_BATCH=off` 면 늘
    한 문장씩 쓴다.

    노드·엣지가 :data:`BATCH_MAX_ITEMS` 를 넘는 fragment 는 묶지 않는다: 커밋 전의 변경은 엔진이
    메모리에 들고 있으므로, 로컬 본문 상한(256MB)까지 오는 fragment 하나가 메모리를 다 쓸 수 있다.
    근거: [[vinc.manual.core]] §3"""
    kwargs = dict(store=store, themes=themes, stamp_edges=stamp_edges, max_domains=max_domains,
                  read_back=read_back, authored=authored, edge_ends=edge_ends)
    if _item_count(fragment) > BATCH_MAX_ITEMS:
        return _merge_fragment_once(fragment, **kwargs)
    result, first = write_batch.run_batched(
        store, lambda: _merge_fragment_once(fragment, **kwargs), what="fragment")
    # 어휘 제안은 첫 시도에서만 "새로" 보인다(다시 돌리면 이미 제안돼 있다). 사라지지 않게 옮긴다.
    if first and first.get("proposed_types") and not result.get("proposed_types"):
        result["proposed_types"] = first["proposed_types"]
    return result


def _merge_fragment_once(fragment: dict, *, store, themes=None,
                         stamp_edges: bool = True,
                         max_domains: int | None = None,
                         read_back: bool = False,
                         authored: bool = False,
                         edge_ends: list | None = None) -> dict:
    """fragment 전체 MERGE. 반환 = D11 §4 계약(+ dedupe/partial/개방층 확장):
    ``{merged:{nodes:{created,matched}, edges:{created,[skipped_duplicate]}},
    warnings:[…], executed:…, [partial:True], [proposed_types:[…]],
    [id_conflicts:[…]], [stored:[…]], [stored_truncated:N]}``. ``id_conflicts`` 는 **남이 이미 가진 id** 라 영영 쓸 수
    없는 것들이다 — 재시도가 고치지 못하는 유일한 스킵이라 따로 낸다.

    ``themes``(ThemeStore, 선택): 개방층 타입의 어휘 편입(D11-R2 §3)에 쓴다 —
    서버 잡 큐 경로만 전달(server.write_ingest). sync/share 호출부는 미전달 유지
    (복제에서 어휘 fold-in 은 부적절 — 원 저작 시점에 이미 편입됨).

    ``stamp_edges``: 신규 생성된 동기화 대상 엣지(CC 4종·ABOUT)의 양 끝 노드에
    revstamp(엣지 추가·재생성의 증분 동기화 전파 — _stamp_edge_endpoints 주석).
    **sync apply 경로만 False** — 원격발 엣지를 다시 스탬프하면 에코가 된다.

    ``edge_ends``(선택): 목록을 주면 이 호출이 새로 만들었거나 근거·신뢰도를 바꾼 엣지의 양 끝을
    (라벨, PK 열, 물리 키)로 채운다. 묶음을 버리고 다시 쓰면 다시 채우므로 시도마다 비운다. 동기화
    적용(`api.sync.apply_delta`)이 ``stamp_edges=False`` 와 함께 주고, 그 끝점에 새 순번을 매겨 다음
    델타에 싣는다. 응답에는 싣지 않는다(물리 키가 응답에 나가지 않게).

    ``max_domains``(선택): 무료 등급 주제 개수 상한(AP-1 §3.3). **기본 None = 무제한**
    이라는 점이 설계의 핵심이다 — sync 복제·로컬 경로가 아무 코드 변경 없이 자동
    면제되고, 위험이 "면제를 잊음"(상한 초과 사용자의 복제 파손)에서 "적용을
    잊음"(상한이 안 걸림)으로 뒤집힌다. 뒤쪽이 훨씬 싼 실패다.

    ``read_back``(선택): 응답에 ``stored`` 를 싣는다 — MERGE 직후 되읽은 **저장형**
    노드 목록(`_stored_nodes`). **ingest 라우트만 켠다**(`themes` 와 같은 이유로
    호출부별 분기다): sync 복제·팩 가져오기는 수백 노드를 한 번에 밀어넣는데, 거기서
    저장형 전량을 되돌리면 응답이 요청보다 커지고 아무도 그것을 읽지 않는다. 되읽기가
    필요한 곳은 사람이 손으로 페이로드를 짜는 자리다."""
    warnings: list[str] = []
    if edge_ends is not None:
        edge_ends.clear()
    if store is None:
        return _finalize({"merged": None, "executed": False}, warnings + ["no reachable store"])
    created = matched = e_created = e_skipped_dup = 0
    nodes = list(fragment.get("nodes") or [])
    edges = list(fragment.get("edges") or [])
    review_claims = fragment.get("review_claims", False) if authored else False
    if type(review_claims) is not bool:
        return _finalize({"merged": None, "executed": False, "error": "review_claims must be a boolean"},
                         ["review_claims must be a boolean"])
    owner_explicit = bool((fragment.get("owner") or "").strip())
    owner = (fragment.get("owner") or "").strip() or DEFAULT_OWNER
    from .mutation_contract import prepare_fragment, MutationConflict
    try:
        fragment = prepare_fragment(store, fragment, owner)
        nodes = list(fragment.get("nodes") or [])
        edges = list(fragment.get("edges") or [])
    except MutationConflict as exc:
        return _finalize({"merged": None, **exc.result}, [exc.result["error"]])
    if guard_active(store):
        from .topic_conversion import check_write
        check_write(store, owner, fragment)
    beliefs = []
    if authored:
        # 저작 시점의 근거를 검사한다. sync/팩 복원은 근거보다 패키지가 먼저 올 수 있다.
        # 근거: [[vinc.canon.mcp-contract]] Record props와 공개 저작 경로
        from .package_contract import validate_nodes
        denial = validate_nodes(nodes, store=store, owner=owner)
        if denial:
            return _finalize({"merged": None, "executed": False,
                              "error": "invalid_package", "note": denial},
                             ["invalid_package: " + denial])
        from .spec_contract import validate_nodes as validate_spec_nodes
        denial = validate_spec_nodes(nodes, store=store, owner=owner)
        if denial:
            return _finalize({"merged": None, "executed": False,
                              "error": "invalid_spec_record", "note": denial},
                             ["invalid_spec_record: " + denial])
        from . import belief_contract
        try:
            nodes, edges, beliefs = belief_contract.prepare(nodes, edges, store=store, owner=owner)
        except belief_contract.InvalidBelief as exc:
            return _finalize({"merged": None, "executed": False,
                              "error": "invalid_belief", "note": str(exc)},
                             ["invalid_belief: " + str(exc)])
    if max_domains is not None and owner_explicit:
        denial = _domain_gate(nodes, store=store, owner=owner, max_domains=max_domains)
        if denial:
            return _finalize({"merged": None, "executed": False, **denial}, warnings + [
                f"topic limit reached ({denial['current']}/{max_domains}) — "
                f"new topics rejected: {', '.join(denial['attempted'])}. "
                "Existing topics keep working."])
    fold = _ThemeFold(themes, fragment)
    # 링크(MENTIONS)를 다시 셀 노드를 고르려고 쓰기 **전** 재료를 읽는다(`_link_mentions`).
    mention_ids = sorted({(n.get("id") or "").strip() for n in nodes
                          if (n.get("type") or "").strip() in _SEMANTIC_TYPES} - {""})
    mention_before = _mention_fields(store, owner, mention_ids)
    # 끝점 해석표는 **실제로 MERGE 된 노드**로만 짓는다 — 스킵된 노드가 끝점으로
    # 등록되면 그리로 향하는 엣지가 0행을 만나고도 created 로 세어진다.
    # ⚠ 판정은 **`r is not None`** 이다. "경고가 없었나"로 재면 안 된다 — 경고를
    # 내면서도 정상 MERGE 되는 자리가 실재한다(Bridge 에 domain):
    # [[record:endpoint-table-must-be-built-from-merged-nodes]].
    types: dict[str, str] = {}
    declared = {(n.get("id") or "").strip() for n in nodes}
    id_conflicts: list[str] = []
    default_day = _kind_default_day(authored=authored, owner=owner)
    for n in nodes:
        ntype = (n.get("type") or "").strip()
        if _is_open(ntype):
            fold.fold("node", ntype)
        r = _merge_node(store, n, owner, warnings, owner_explicit=owner_explicit,
                        theme_id=fold.theme_id or "", theme_explicit=bool(fold._explicit),
                        conflicts=id_conflicts, default_day=default_day)
        created += (r == "created")
        matched += (r == "matched")
        if r is not None:
            types[(n.get("id") or "").strip()] = ntype
    edge_rollup: dict[str, list[str]] = {}
    # 이 배치의 엣지 출생 시각(v14) — **한 번만** 뽑아 전부가 나눠 쓴다. 엣지마다
    # 새로 뽑으면 메타 왕복이 엣지 수만큼 늘고, 한 번의 적재가 시간축에 흩어져 보인다.
    # 엣지가 없으면 아예 안 뽑는다(lambda 가 지연).
    _born_cache: list[str] = []

    def _born() -> str:
        if not _born_cache:
            _born_cache.append(revstamp.hlc_now(store))
        return _born_cache[0]

    for e in edges:
        st, dt = types.get((e.get("from") or "").strip(), ""), types.get((e.get("to") or "").strip(), "")
        etype = (e.get("type") or "").strip()
        if etype and _is_open(st) and _is_open(dt):
            fold.fold("edge", etype)   # TYPED 캐리어행 엣지 타입만 어휘 편입
        r = _merge_edge(store, e, types, warnings, owner=owner,
                        owner_explicit=owner_explicit, stamp_edges=stamp_edges,
                        rollup=edge_rollup, declared=declared, born=_born, changed=edge_ends)
        e_created += (r == "created")
        e_skipped_dup += (r == "skipped_duplicate")
    _flush_edge_warnings(edge_rollup, warnings)
    if authored and edges:
        from . import belief_write
        belief_write.apply(beliefs, edges, store=store, owner=owner, warnings=warnings)
    _warn_orphan_decisions(store, types, warnings, owner=owner)
    _link_mentions(store, owner, [i for i in mention_ids if i in types], mention_before)
    edges_out = {"created": e_created}
    if e_skipped_dup:
        edges_out["skipped_duplicate"] = e_skipped_dup
    # **어느 소유자로 썼는지 밝힌다.** 값의 출처는 반드시 **쓴 쪽**이다 — 클라이언트
    # 인자를 되울리면 호스티드에서 거짓말이 된다(거기선 키가 owner 를 고정한다).
    # 왜 whoami 로는 안 되는지: [[decision:identity-rides-on-the-first-door-not-a-whoami]].
    result = {"merged": {"nodes": {"created": created, "matched": matched}, "edges": edges_out},
              "executed": True, "owner": owner}
    if fold.proposed:
        result["proposed_types"] = fold.proposed
    if read_back and types:
        # 쓰기 응답이 "몇 개 움직였나"만 말하던 자리(R3). 되읽기는 쓰기가 끝난 뒤에
        # 하므로 여기서 실패해도 위의 커밋은 유효하다.
        stored, truncated = _stored_nodes(store, types, owner, warnings)
        result["stored"] = stored
        if truncated:
            result["stored_truncated"] = truncated
    if id_conflicts:
        # warnings 안의 산문이 아니라 **필드**로 낸다. 호스트가 warnings 를 접거나
        # 안 보여 주면 저작자는 자기 노드가 사라진 줄도 모른다(감사 H1 의 조용한 절반).
        result["id_conflicts"] = id_conflicts
    if authored and read_back and types:
        from . import authoring
        result["retrieval"] = authoring.node_retrieval()
        try:
            review = authoring.review(store, result.get("stored") or [], owner=owner,
                                      compare_claims=review_claims)
            if review:
                result.update(review)
        except Exception:
            result["advisory_review"] = {"state": "unavailable"}
        if result.get("stored_truncated") and result.get("advisory_review", {}).get("state") != "unavailable":
            result.setdefault("advisory_review", {})["state"] = "limited"
        if any(w.startswith("read-back") for w in warnings):
            result.setdefault("advisory_review", {})["state"] = "unavailable"
    return _finalize(result, warnings)
