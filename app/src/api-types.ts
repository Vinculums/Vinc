// 앱이 사이드카 · 계정과 주고받는 데이터의 형식. 실행되는 코드는 없다(형식만 둔다).
// `api.ts` 가 이것을 다시 내보내므로 부르는 쪽은 전처럼 `./api` 에서 가져간다. 호출과 전송은 `api.ts` 에 있다.
import type { StageCounts } from "./packchunks";

export type DataMode = "account" | "local";

/** 호출 하나의 스페이스를 명시적으로 못박는다. 생략하면 현재 스페이스(`cfg.team`)를
 *  따른다. `{ team: null }` 은 "현재가 팀이어도 이번 호출은 개인" 이라는 뜻이다 —
 *  스페이스를 건너다니는 공유/불러오기가 이걸 쓴다. */
export type Scope = { team: string | null };

export type SearchOptions = { mode?: "general" | "current" | "historical";
  historical?: boolean; as_of?: string; known_at?: string };

/** 그래프가 사는 곳(`store/location.py` 와 같은 합 타입). 데이터베이스 위치에는 비밀번호 칸이 없다:
 *  `secret_ref` 는 OS 키링 항목의 이름이고, 셸이 사이드카를 띄울 때 그 값을 환경으로만 넘긴다. */
export type StorageLocation =
  | { kind: "account" }
  | { kind: "folder"; path: string | null }
  | { kind: "database"; engine: "neo4j"; uri: string; database: string; user: string;
      secret_ref: string };
export type StorageConfig = {
  executed: boolean; mode: DataMode | null; mode_chosen: boolean;
  data_path: string | null; data_file: string | null;
  default_folder: string; suggested_folder: string; pinned: boolean;
  restart_needed: boolean; store_open: boolean;
  /** 고르지 않았으면 null. 설정이 틀렸으면 null 이고 `location_error` 가 사람용 문장이다. */
  location: StorageLocation | null; location_error: string | null;
  /** 지금 연 스토어의 엔진. 스토어가 없으면 null. */
  engine: string | null;
};
export type FolderCheck = {
  path: string; ok: boolean; exists: boolean; has_store: boolean; store_bytes: number;
  writable: boolean; synced: string | null; network: boolean; risks: string[];
  problem: string | null;
};
/** 데이터베이스 연결 확인(`store/neo4j_backend.py` `inspect_database`). `ok` 가 거짓이면 `problem` 이 이유다
 *  (unreachable · auth_failed · database_not_found · driver_missing · error). `risks` 중
 *  version_below_floor·read_only_user 는 막고, 나머지는 사람이 확인하면 받는다. */
export type ConnectionCheck = {
  ok: boolean; problem: string | null; detail: string | null;
  reachable: boolean; auth_ok: boolean;
  server_version: string | null; edition: string | null; version_supported: boolean | null;
  write_permission: boolean | null; latency_ms: number | null;
  vinc_schema_version: number | null; vinc_counts: { nodes: number; docs: number };
  foreign_labels: { count: number; names: string[]; nodes: number };
  writer_lease: { holder: string; active: boolean } | null;
  risks: string[];
};
export type DatabaseConnect = { uri: string; database: string; user: string; password: string };

/** 시간축(D72). `recorded` = 기록 시각, `content` = 문서 내용이 가리키는 시점. */
export type TimeBasis = "recorded" | "content";

export type GraphNode = {
  id: string; label: string; domain: string; degree: number;
  kind?: string;      // concept | decision | bridge | episode | record | entity (F2 — 부착 축)
  topic_description?: boolean;   // 주제 설명 문서의 미러. 화면은 노드로 그리지 않는다
  domain2?: string;   // bridge 전용 — target_domain (노드 domain = source_domain)
  ts?: string;        // D43 — created/date 우선, 없으면 updated_at. 빈 문자열 가능
  // D72 — 내용 시점(그 노드가 말하는 일이 언제 있었나). 축을 `content` 로 요청하면
  // 서버가 `ts` 를 이 값으로 갈아 끼우지만, **두 열 모두 실려 온다** — 클라이언트가
  // 재조회 없이 축을 바꿀 수 있게. 구 사이드카에는 이 키가 아예 없다(우아 강등).
  content_ts?: string;
  // D74 — 좌표는 **두 벌**이다: x/y/z 는 3D 정본, x2/y2 는 2D 정본.
  // 2D 는 3D 를 눌러 접은 것이 아니다(접으면 원뿔 배치에서 서로 다른 노드가 같은
  // 점이 된다). 같은 도메인 트리를 평면 문법으로 다시 배치한 값이라 겹침이
  // 원리적으로 생기지 않는다. 구 사이드카에는 이 키가 없다 → x/y 로 우아 강등.
  x?: number; y?: number; z?: number;
  x2?: number; y2?: number;
  // 유효구간 — 기록은 열로(`valid_until`), 결정은 속성으로(`props.valid_until`·`superseded_by`)
  // 실려 온다. 3D 몸통의 historical 상태가 읽는다(node-validity.ts).
  valid_from?: string; valid_until?: string;
  props?: Record<string, unknown> | null;
};
export type GraphEdge = {
  id?: string;        // Preserve an upstream relation identity when supplied.
  source: string; target: string; type: string; cross_domain: boolean;
  role?: string;        // CONNECTS.role / ATTACHES_TO.type 질화자 (F2)
  confidence?: number;  // D43 — 0..1. 상류 미저작이면 키 자체가 없다
  evidence?: string;    // D43 — 근거 문장. 툴팁 전용
  presentation?: boolean; // Topic membership guide, never a causal relation.
};
/** 상세 레일의 연결 요약 + 관계 목록(node-context ④). 세는 자리가 노드 자신이라
 *  어느 동선(그래프 클릭 · 드릴다운 · 기간 목록 · 검색 착지)으로 열어도 같은 값이다. */
type NodeLink = {
  id: string; title: string; domain: string; type: string;
  dir: "out" | "in" | "att";
  evidence: boolean;      // 근거 문장이 기록돼 있는가(문장 전문은 edge-context)
  confidence: number;
  // `evidence=1` 로 읽으면 저장된 문장 자체(항목 페이지가 인용한다)와 상대의 유효구간.
  evidence_text?: string;
  valid_from?: string; valid_until?: string;
  temporal?: { created_at?: string | null; valid_from?: string | null; valid_until?: string | null;
               superseded_by?: string | null } | null;
};
export type NodeLinks = {
  degree: number; out: number; in: number; attached: number;
  items: NodeLink[]; truncated: boolean; executed: boolean;
};
export type TopicDescription = {
  executed: boolean; status: "ok" | "query_failed" | "unavailable" | "invalid_request" | "conflict" | "unconfirmed";
  text?: string; content_hash?: string; exists?: boolean; doc_id?: string;
  write_may_have_applied?: boolean;
};
export type TopicDetail = {
  description?: TopicDescription;
  path: string;
  parent: { id: string; title: string; domain: string } | null;
  child_topics: number; direct_nodes: number; subtree_nodes: number; descendant_nodes: number;
  structure: { total: number; offset: number; truncated: boolean; next_offset?: number;
    items: { rel: "PARENT_OF" | "IN_DOMAIN"; id: string; title: string; domain: string }[] };
  consistency?: { status: "membership_drift"; column_direct_nodes: number };
};
export type NodeContext = {
  executed: boolean; title?: string; one_liner?: string; arm?: string; note?: string;
  error?: string; status?: "ok" | "query_failed" | "unavailable" | "unsupported_kind";
  found?: boolean; props?: Record<string, unknown>; node_kind?: string; rev?: number;
  excerpts?: { doc_id: string; doc_title: string; section: string; excerpt: string; method: string }[];
  links?: NodeLinks; topic?: TopicDetail;
  /** 시간 메타(search_temporal) — 효력 시작·끝·후속. 결정은 속성에서, 기록은 열에서 온다. */
  temporal?: { created_at?: string | null; valid_from?: string | null; valid_until?: string | null;
               superseded_by?: string | null; validity?: string } | null;
};
/** D69 좌측 문서 트리. 문서는 주제 열을 갖지 않으므로 주제도 착지 노드도 언급
 *  (MENTIONS)에서 유도된다 — `모든 문서` 목록과 같은 원천이라 두 화면이 같은 문서를
 *  다른 주제에 걸지 않는다. `depth` 는 그 주제 안 개념 계층(선행·파생 BFS)이고,
 *  `node_id` 는 클릭 시 착지할 노드다. 언급 0 문서는 `unfiled`(착지 노드 없음). */
type NavDoc = {
  doc_id: string; title: string; updated_at: string;
  node_id: string; node_title: string; depth: number;
  /** 유효기간이 닫힌 문서만 갖는다(YYYY[-MM[-DD]]). */
  valid_until?: string;
};

/** 좌측 트리의 노드 갈래 한 줄. `concepts` 가 세는 것과 **같은 집합**이다
 *  (Concept + Decision) — 머리가 말하는 수와 줄 수가 갈리면 안 된다. */
export type NavNode = { id: string; title: string; kind: string; depth: number };
/** `nodes` 는 `navTree(owner, {nodes:true})` 로 물었을 때만 온다. 기본 응답을
 *  무겁게 하지 않으려는 것이다 — 개념 제목은 문서보다 수가 훨씬 많고, 웹모드에서는
 *  이 응답이 계정 프록시를 건넌다. */
/** `count` 는 `kind` 로 물었을 때만 온다 - 그 주제에 있는 **그 종**의 수이고,
 *  곧 그 갈래의 줄 수다. `concepts` 는 뜻이 바뀌지 않는다(늘 개념 + 결정). */
export type NavGroup = { domain: string; concepts: number; count?: number;
                         docs: NavDoc[]; nodes?: NavNode[] };
export type NavTreeRes = {
  executed: boolean; note?: string;
  domains?: NavGroup[];
  unfiled?: { doc_id: string; title: string; updated_at: string; valid_until?: string }[];
  //: `kinds` 는 종별 수 - 레일이 읽는 유일한 집계. `kind` 는 이 응답이 어느 종으로
  //: 좁혀졌는지(안 좁혔으면 없다).
  counts?: { domains: number; docs: number; unfiled: number;
             kinds?: Record<string, number>; kind?: string };
};

export type PackProgress = { total: StageCounts; done: StageCounts };

/** 되돌릴 수 없는 삭제의 확인 묶음(0.9.8). `preview_digest` 는 확인 창이 열릴 때 본
 *  상태의 지문이고, `operation_id` 는 같은 삭제를 다시 보내도 한 번만 일어나게 하는 열쇠다. */
export type DeleteGuard = { operation_id: string; preview_digest?: string };

export type ConventionsPolicy = "retain" | "move_original";
export type ConversionCheck = { ready: boolean; topic: string; fingerprint?: string;
  conventions?: { policy: ConventionsPolicy; documents: number; personal_defaults_after_move: boolean };
  retained_personal_history?: Record<string, number>;
  preserved_references?: { external_references?: number; external_attachments?: number };
  document_ownership?: Record<string, number>;
  document_missing_evidence?: Record<string, number>;
  inventory_state?: "pending" | "done" | "failed" | "stale" | "unknown" | "busy";
  inventory_ticket?: string;
  counts_known?: boolean; counts: Record<string, number> | null; documents: number | null; blockers: string[] };
export type ConversionRun = { id: string; topic: string; name: string; team: string;
  conventionsPolicy?: ConventionsPolicy;
  progress?: { status: string; phase?: string; batches?: number; durable_generation?: number;
    retained_personal_history?: Record<string, number> };
  state: "prepared" | "committing" | "graph_committed" | "active" | "cancelled" };
