# -*- coding: utf-8 -*-
"""골드 후보 쌍 생성기 (M1-1 준비, VIN-404). 허브 REST 를 읽기 전용 키로 읽어 §3 형식에서 labels 만 비운 JSONL 을 쓴다.

    VINC_RO=... python -m rag_eval.laya.candidates --out out/candidates/candidates.jsonl [--seeds 60]

생성기(테스트 방식 §6): ties(태그 공유) · comention(같은 청크 발췌에 둘 다 언급) · search(검색 상위 node_hits) ·
merge(동일성 후보 + 책 장의 한영 미러) · random(다른 도메인 무작위). 같은 쌍이 여러 생성기에서 나오면 source 에
전부 적는다(`|` 로 잇는다). text 는 생성 시점의 제목과 첫 문장을 얼린다. 모델은 쓰지 않는다. 키는 출력에 남지 않는다.
근거: [[vinc.plan.laya-test-method-2026-10-07 §3 §6]]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

from .pins import SEED, detect_lang

BASE = os.environ.get("VINC_HUB", "https://mcp.vincs.io")
KINDS = ("concept", "decision", "record")  # episode 는 사건 기록이라 명제 쌍에 안 맞는다
ID_RE = re.compile(r"\b(?:decision|record|concept):[a-z0-9][a-z0-9._-]{6,}")
KO_SEED_QUERIES = ["검색 품질", "배포 사고", "디자인 토큰", "허브 저장소", "한국어 색인", "계정 모드", "결정 기록",
                   "리랭커", "임베딩", "로그인 실패", "요금", "팀 공유", "문구 규칙", "테스트 실패"]
MAX_TEXT = 400  # 한쪽 상한. 제목+첫 문장이 이걸 넘으면 자른다(state 65~300 토큰 예산)
MIN_TEXT = 12   # 이보다 짧은 제목은 명제가 아니다(코드명·약어). 한국어는 글자당 정보가 많아 절반이다
TEXT_KEYS = ("one_liner", "summary", "statement", "note", "rationale", "what", "fix", "finding", "outcome", "reason")


class Hub:
    def __init__(self, token: str):
        self.token, self.calls, self.cache = token, 0, {}

    def _req(self, method: str, path: str, body: dict | None = None):
        key = (method, path, json.dumps(body, sort_keys=True) if body else None)
        if key in self.cache:
            return self.cache[key]
        req = urllib.request.Request(BASE + path, method=method,
                                     headers={"Authorization": f"Bearer {self.token}", "content-type": "application/json"},
                                     data=json.dumps(body).encode() if body else None)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read().decode("utf-8"))
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    print(f"  ! {method} {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
                    data = {"error": str(exc)}
                else:
                    time.sleep(1.5 * (attempt + 1))
        self.calls += 1
        self.cache[key] = data
        return data

    def get(self, path: str):
        return self._req("GET", path)

    def post(self, path: str, body: dict):
        return self._req("POST", path, body)

    def node(self, nid: str) -> dict | None:
        d = self.get(f"/v1/nodes/{urllib.parse.quote(nid, safe=':')}")
        return d.get("node") if d.get("found") else None

    def neighbors(self, nid: str, limit: int = 500) -> list[dict]:
        return self.get(f"/v1/nodes/{urllib.parse.quote(nid, safe=':')}/neighbors?limit={limit}").get("neighbors", [])

    def ties(self, nid: str, limit: int = 5) -> list[dict]:
        return self.get(f"/v1/nodes/{urllib.parse.quote(nid, safe=':')}/tie-suggestions?limit={limit}").get("suggestions", [])

    def search(self, query: str, limit: int = 5) -> dict:
        return self.post("/v1/search", {"query": query[:200], "limit": limit})


def first_sentence(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    m = re.match(r"(.+?[.!?。]|.+?다\.)(\s|$)", s)
    return (m.group(1) if m else s)[:240]


def node_text(n: dict) -> str:
    title = (n.get("title") or "").strip()
    body = ""
    for k in TEXT_KEYS:
        v = n.get(k) if k == "one_liner" else (n.get("props") or {}).get(k)
        if isinstance(v, str) and v.strip():
            body = first_sentence(v)
            break
    if body and body.lower() != title.lower():
        text = f"{title}. {body}" if not title.endswith((".", "다", "!", "?")) else f"{title} {body}"
    else:
        text = title
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1].rstrip() + "…"


def kind_of(nid: str) -> str:
    return nid.split(":", 1)[0]


class Pool:
    """노드 캐시와 쌍 모음. 같은 쌍은 (정렬된 id 쌍) 하나로 합친다."""

    def __init__(self, hub: Hub):
        self.hub, self.nodes, self.pairs = hub, {}, {}
        self.misses = 0

    def fetch(self, nid: str) -> dict | None:
        if nid not in self.nodes:
            n = self.hub.node(nid)
            title = (n.get("title") or "").strip() if n else ""
            min_len = MIN_TEXT // 2 if detect_lang(title) == "ko" else MIN_TEXT
            if n and len(title) >= min_len and kind_of(nid) in KINDS:
                self.nodes[nid] = {"id": nid, "rev": int(n.get("rev") or 0), "text": node_text(n),
                                   "domain": n.get("domain") or "", "kind": kind_of(nid)}
            else:
                self.nodes[nid] = None
                self.misses += 1
        return self.nodes[nid]

    def add(self, a: str, b: str, source: str, context: str | None = None, note: str | None = None, **extra):
        if a == b:
            return
        na, nb = self.fetch(a), self.fetch(b)
        if not na or not nb:
            return
        key = tuple(sorted((a, b)))
        p = self.pairs.get(key)
        if p is None:
            p = self.pairs[key] = {"a": na, "b": nb, "sources": [], "context": None, "notes": [], "extra": {}}
        if source not in p["sources"]:
            p["sources"].append(source)
        if context and not p["context"]:
            p["context"] = context[:400]
        if note and note not in p["notes"]:
            p["notes"].append(note)
        p["extra"].update({k: v for k, v in extra.items() if v is not None})


def pick_seeds(hub: Hub, rng: random.Random, n_seeds: int) -> list[str]:
    rows = hub.get("/v1/domains").get("rows", [])
    domains = [r["domain"] for r in rows if r.get("domain", "").startswith("vinc/")]
    rng.shuffle(domains)
    per = max(2, n_seeds // max(1, min(len(domains), 15)))
    seeds: list[str] = []
    for dom in domains[:15]:
        members = [m["id"] for m in hub.neighbors(f"domain:{dom}") if kind_of(m["id"]) in KINDS]
        rng.shuffle(members)
        # 종류를 섞는다: concept 하나는 꼭 넣어 ties 가 나오게 한다
        concepts = [m for m in members if kind_of(m) == "concept"][:1]
        rest = [m for m in members if m not in concepts]
        seeds += concepts + rest[: per - len(concepts)]
    # 한국어 제목 시드: 검색으로 찾는다(그래프 제목은 대개 영어라 ko·mixed 가 모자란다)
    for q in KO_SEED_QUERIES:
        for h in hub.search(q, limit=8).get("node_hits", []):
            if kind_of(h["id"]) in KINDS and detect_lang(h.get("title", "")) in ("ko", "mixed"):
                seeds.append(h["id"])
    seen, out = set(), []
    for s in seeds:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def book_mirror_pairs(hub: Hub, pool: Pool):
    """vinc.book.NN-xxx 와 .en 의 미러 개념 쌍: 번역이라 same_as 양성 후보이고 mixed 언어다."""
    hits = {}
    for q in ("vinc.book", "Vinc book chapter"):
        for h in hub.search(q, limit=50).get("node_hits", []):
            if h["id"].startswith("concept:doc.vinc.book."):
                hits[h["id"]] = h
    for nid in list(hits):
        if nid.endswith(".en"):
            ko = nid[: -len(".en")]
            if pool.fetch(ko) and pool.fetch(nid):
                pool.add(ko, nid, "merge", note="book-mirror")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--seeds", type=int, default=60)
    ap.add_argument("--random", type=int, default=60)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args(argv)
    token = os.environ.get("VINC_RO")
    if not token:
        print("VINC_RO is not set", file=sys.stderr)
        return 2
    rng = random.Random(args.seed)
    hub, pool = Hub(token), Pool(Hub(token))
    pool.hub = hub
    me = hub.get("/v1/me")
    print("hub:", {k: me.get(k) for k in ("owner_id", "scope", "role") if k in me} or list(me)[:5])

    seeds = pick_seeds(hub, rng, args.seeds)
    print(f"seeds: {len(seeds)}")
    for i, s in enumerate(seeds):
        ns = pool.fetch(s)
        if not ns:
            continue
        # ties: 개념에만 있다
        if ns["kind"] == "concept":
            for t in hub.ties(s, limit=5):
                pool.add(s, t["id"], "ties", tie_score=t.get("score"), tie_reason=t.get("reason"))
        # search: 제목으로 검색한 상위 node_hits 와 쌍. 같은 질의의 hit 끼리도 한 쌍(주제 같지만 근거 아닌 음성 후보)
        res = hub.search(ns["text"].split(". ")[0], limit=6)
        hits = [h["id"] for h in res.get("node_hits", []) if h["id"] != s and kind_of(h["id"]) in KINDS][:5]
        for h in hits:
            pool.add(s, h, "search", arms=",".join(res.get("arms") or []))
        if len(hits) >= 2:
            pool.add(hits[0], hits[1], "search", note="co-hit")
        # comention: 같은 청크 발췌가 둘 이상 id 를 적고 있으면 그 쌍. 발췌가 Context 가 된다
        for row in res.get("rows", [])[:6]:
            snippet = row.get("snippet") or ""
            ids = [x for x in dict.fromkeys(ID_RE.findall(snippet))
                   if kind_of(x) in KINDS and not snippet.rstrip("…. ").endswith(x)]  # 발췌 끝에서 잘린 id 제외
            if s not in ids and (s in snippet or row.get("cite_id") == s):
                ids = [s] + ids
            ids = ids[:4]
            for x in range(len(ids)):
                for y in range(x + 1, len(ids)):
                    pool.add(ids[x], ids[y], "comention", context=snippet, chunk_id=row.get("id"))
        # comention(보강): 같은 개념에 ABOUT 으로 매달린 기록·결정끼리. 청크 발췌가 id 를 적는 일이 드물어
        # (1차 실행 12쌍) 그래프 수준의 공동 언급을 더한다. Context 는 그 개념의 문장, note 에 anchor 를 적는다.
        nbrs = hub.neighbors(s, limit=50)
        anchors = [s] if ns["kind"] == "concept" else             [m["id"] for m in nbrs if m.get("rel") == "ABOUT" and kind_of(m["id"]) == "concept"][:2]
        for anchor in anchors:
            na = pool.fetch(anchor)
            if not na:
                continue
            about = [m["id"] for m in (nbrs if anchor == s else hub.neighbors(anchor, limit=50))
                     if m.get("rel") == "ABOUT" and kind_of(m["id"]) in ("record", "decision")]
            if s not in about and ns["kind"] != "concept":
                about.append(s)
            rng.shuffle(about)
            about = about[:5]  # 청크 규칙 n<=6 과 같은 상한
            for x in range(len(about)):
                for y in range(x + 1, len(about)):
                    pool.add(about[x], about[y], "comention", context=na["text"], note=f"shared-anchor:{anchor}")
        if i % 10 == 9:
            print(f"  seed {i + 1}/{len(seeds)} pairs={len(pool.pairs)} calls={hub.calls}")

    # merge: 동일성 후보 전부(상한 100). same_as 양성과 어휘만 겹치는 음성이 섞여 있다
    for ms in ("0.6", "0.5"):  # 0.6 이 §6 의 값. 100개 상한에 걸려 모자라서 0.5 를 한 번 더 긁는다(점수는 gen 에)
        for c in hub.get(f"/v1/merge-candidates?min_score={ms}&limit=100").get("candidates", []):
            pool.add(c["a_id"], c["b_id"], "merge", merge_score=c.get("score"), merge_reason=c.get("reason"))
    book_mirror_pairs(hub, pool)

    # random: 지금까지 모인 노드 중 다른 도메인끼리
    ids = [k for k, v in pool.nodes.items() if v]
    tries = 0
    added = 0
    while added < args.random and tries < args.random * 20 and len(ids) > 2:
        a, b = rng.sample(ids, 2)
        tries += 1
        if pool.nodes[a]["domain"].split("/")[:2] != pool.nodes[b]["domain"].split("/")[:2]:
            before = len(pool.pairs)
            pool.add(a, b, "random")
            added += len(pool.pairs) > before

    # 쓰기
    args.out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for n, (key, p) in enumerate(sorted(pool.pairs.items()), 1):
        a, b = p["a"], p["b"]
        rows.append({"pair_id": f"c{n:04d}",
                     "a": {"id": a["id"], "rev": a["rev"], "text": a["text"]},
                     "b": {"id": b["id"], "rev": b["rev"], "text": b["text"]},
                     "context": p["context"], "lang": detect_lang(a["text"], b["text"]),
                     "source": "|".join(p["sources"]),
                     "labels": {"supports": None, "contradicts": None, "same_as": None},
                     "hard_kind": None, "author": None, "labeled_at": None,
                     "note": "; ".join(p["notes"]) or None,
                     "gen": {"a_domain": a["domain"], "b_domain": b["domain"], **p["extra"]}})
    with args.out.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {"pairs": len(rows), "seeds": len(seeds), "calls": hub.calls, "node_misses": pool.misses,
               "by_source": dict(Counter(s for r in rows for s in r["source"].split("|"))),
               "by_lang": dict(Counter(r["lang"] for r in rows)),
               "with_context": sum(1 for r in rows if r["context"]),
               "by_kind_pair": dict(Counter("+".join(sorted((kind_of(r["a"]["id"]), kind_of(r["b"]["id"])))) for r in rows)),
               "hub": BASE, "seed": args.seed, "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    args.out.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
