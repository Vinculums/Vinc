# -*- coding: utf-8 -*-
"""시간 가지 배치 — 허브 밖 배치 컨테이너(`vinc-layout`)에 맡기고 결과를 들고 있는다.

화면이 시간 가지를 열 때마다 브라우저가 전체를 다시 배치하면 1만 노드에서 1분을 넘긴다.
그래서 허브가 쓰기 뒤에 미리 배치를 맡기고, 화면은 들고 있는 결과를 받아 바로 그린다.
배치 컨테이너는 주제 조각·통로·상자 자리를 공간별로 기억하므로 쓰기 뒤의 재배치는 바뀐
주제만 다시 한다(`app/src/layout-service.ts`).
근거: [[decision:time-branch-layout-is-computed-on-the-hub-per-topic-2026-10-12]]

계약:

  - **결과는 변경 표식(`api.changes`)에 묶인다.** 배치에 쓴 읽기의 표식을 읽기 **전에**
    잡아 함께 둔다. 화면은 자기 그래프 읽기의 표식과 같을 때만 그대로 쓴다.
  - **관심 있는 공간만 미리 한다.** 화면이 한 번 물은 (소유자, 시간 기준)만 쓰기 뒤에
    다시 배치한다. 아무도 안 보는 공간을 위해 컨테이너를 깨우지 않는다.
  - **한 번에 하나.** 일꾼 스레드 하나가 차례로 보낸다. 허브는 1코어이고 컨테이너도 작다.
  - **보내는 것은 id·종류·주제·시각·관계 끝점뿐이다.** 제목·본문·props 는 나가지 않는다.
    공간 이름도 원문 대신 해시를 보낸다.
  - **꺼져 있으면(`VINC_LAYOUT_REMOTE` 없음) `state:"off"`.** 화면은 예전처럼 직접 배치한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.request
from collections import OrderedDict
from typing import Callable

BASES = ("recorded", "content")


def _space_key(owner: str) -> str:
    return hashlib.sha256(("vinc-layout:" + owner).encode("utf-8")).hexdigest()[:32]


def slim_payload(owner: str, basis: str, graph: dict) -> dict:
    """배치에 필요한 것만 남긴다. 앱의 `slimNodes`·`slimEdges` 와 같은 모양이다."""
    nodes = []
    for n in graph.get("nodes") or []:
        item = {"id": str(n.get("id")), "kind": str(n.get("kind") or "")}
        for k in ("domain", "ts", "content_ts"):
            if n.get(k):
                item[k] = str(n[k])
        nodes.append(item)
    edges = [{"source": str(e.get("source")), "target": str(e.get("target")), "type": str(e.get("type") or "")}
             for e in graph.get("edges") or []]
    return {"space": _space_key(owner), "basis": basis, "nodes": nodes, "edges": edges}


def _post(url: str, token: str, payload: dict, timeout: float) -> dict:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class _Item:
    __slots__ = ("marker", "text", "nodes")

    def __init__(self, marker: str, text: str, nodes: int) -> None:
        self.marker, self.text, self.nodes = marker, text, nodes


class RootsLayouts:
    def __init__(self, *, remote: str, token: str,
                 read_graph: Callable[[str], dict | None], marker: Callable[[str], str],
                 post: Callable[[dict], dict] | None = None, timeout: float = 300.0,
                 debounce: float = 5.0, interest_s: float = 1800.0, retry_s: float = 600.0,
                 max_bytes: int = 96 * 1024 * 1024, clock: Callable[[], float] = time.monotonic) -> None:
        self.remote = remote.rstrip("/")
        self._read_graph, self._marker, self._clock = read_graph, marker, clock
        self._post = post or (lambda payload: _post(f"{self.remote}/v1/layout", token, payload, timeout))
        self.debounce, self.interest_s, self.retry_s, self.max_bytes = debounce, interest_s, retry_s, max_bytes
        self._lock = threading.Condition()
        self._items: "OrderedDict[tuple[str, str], _Item]" = OrderedDict()
        self._bytes = 0
        self._interest: dict[tuple[str, str], float] = {}
        self._due: dict[tuple[str, str], float] = {}
        self._running: tuple[str, str] | None = None
        self._failed: dict[tuple[str, str], tuple[str, float]] = {}   # 슬롯 -> (표식, 시각)
        self._closed = False
        self._thread: threading.Thread | None = None
        self.runs = 0

    @property
    def enabled(self) -> bool:
        return bool(self.remote)

    # ── 화면이 묻는다 ────────────────────────────────────────────────────
    def get(self, owner: str, basis: str) -> tuple[int, object]:
        if not self.enabled:
            return 200, {"executed": True, "state": "off"}
        if basis not in BASES:
            return 400, {"executed": False, "error": "bad_basis"}
        slot, current, now = (owner, basis), self._marker(owner), self._clock()
        with self._lock:
            self._interest[slot] = now
            item = self._items.get(slot)
            if item is not None:
                self._items.move_to_end(slot)
            if item is None or item.marker != current:
                failed = self._failed.get(slot)
                if failed and failed[0] == current and now - failed[1] < self.retry_s:
                    state = "failed"
                else:
                    state = "stale" if item else "pending"
                    self._schedule(slot, now)
            else:
                state = "ready"
        if item is None:
            return 200, {"executed": True, "state": state}
        from .graph_loading import GraphWire
        head = json.dumps({"executed": True, "state": state, "marker": item.marker, "nodes": item.nodes},
                          ensure_ascii=False, separators=(",", ":"))
        return 200, GraphWire((head[:-1] + ',"answer":' + item.text + "}\n").encode("utf-8"))

    # ── 쓰기 관문이 알린다 ───────────────────────────────────────────────
    def note_write(self, owner: str | None) -> None:
        """관심 있는 공간이면 잠시 뒤 다시 배치한다. 쓰기가 몰리면 마지막 뒤로 한 번만 한다."""
        if not self.enabled or not owner:
            return
        now = self._clock()
        with self._lock:
            for slot, seen in list(self._interest.items()):
                if now - seen > self.interest_s:
                    del self._interest[slot]
                elif slot[0] == owner:
                    self._schedule(slot, now)

    def _schedule(self, slot, now) -> None:
        """락을 쥔 상태로 부른다."""
        self._due[slot] = now + self.debounce
        if self._thread is None and not self._closed:
            self._thread = threading.Thread(target=self._work, name="vinc-layout-remote", daemon=True)
            self._thread.start()
        self._lock.notify_all()

    # ── 일꾼 ────────────────────────────────────────────────────────────
    def _work(self) -> None:
        while True:
            with self._lock:
                ready: list = []
                while not self._closed:
                    now = self._clock()
                    ready = [s for s, t in self._due.items() if t <= now]
                    if ready:
                        break
                    wait = min(self._due.values()) - now if self._due else None
                    self._lock.wait(timeout=wait)
                if self._closed:
                    return
                slot = min(ready, key=lambda s: self._due[s])
                del self._due[slot]
                self._running = slot
            try:
                self.run_once(*slot)
            finally:
                with self._lock:
                    self._running = None

    def run_once(self, owner: str, basis: str) -> str:
        """한 공간을 배치해 들고 있는다. 결과: ready·same·failed·empty."""
        slot = (owner, basis)
        marker = self._marker(owner)          # 읽기 전에 잡는다(`api.graph_cache` 와 같은 이유)
        with self._lock:
            held = self._items.get(slot)
        if held is not None and held.marker == marker:
            return "same"
        started = self._clock()
        try:
            graph = self._read_graph(owner)
            if not isinstance(graph, dict) or graph.get("executed") is not True:
                raise RuntimeError("graph read failed")
            reply = self._post(slim_payload(owner, basis, graph))
            answer = reply.get("answer") if isinstance(reply, dict) else None
            if not isinstance(answer, dict) or not isinstance(answer.get("nodes"), list):
                raise RuntimeError("layout service gave no answer")
            text = json.dumps(answer, ensure_ascii=False, separators=(",", ":"))
        except Exception as exc:   # noqa: BLE001 — 실패는 화면이 직접 배치로 넘어가는 신호다
            with self._lock:
                self._failed[slot] = (marker, self._clock())
            print(json.dumps({"event": "layout_remote_failed", "basis": basis,
                              "error": str(exc)[:200]}), flush=True)
            return "failed"
        stats = reply.get("stats") if isinstance(reply, dict) else None
        print(json.dumps({"event": "layout_remote", "basis": basis, "nodes": len(answer["nodes"]),
                          "ms": round((self._clock() - started) * 1000),
                          "service": stats if isinstance(stats, dict) else None}), flush=True)
        with self._lock:
            self._failed.pop(slot, None)
            self._put(slot, _Item(marker, text, len(answer["nodes"])))
            self.runs += 1
            # 배치 도중 쓰기가 끝났으면(비동기 잡) 표식이 이미 넘어갔다. 아직 보는 공간이면 한 번 더 한다.
            if self._marker(owner) != marker and slot in self._interest:
                self._schedule(slot, self._clock())
        return "ready"

    def _put(self, slot, item: _Item) -> None:
        """락을 쥔 상태로 부른다. 크기 상한을 넘으면 오래 안 물은 것부터 버린다."""
        old = self._items.pop(slot, None)
        if old is not None:
            self._bytes -= len(old.text)
        if len(item.text) > self.max_bytes:
            return
        self._items[slot] = item
        self._bytes += len(item.text)
        while self._bytes > self.max_bytes and self._items:
            _, dropped = self._items.popitem(last=False)
            self._bytes -= len(dropped.text)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._lock.notify_all()


def from_env(*, read_graph: Callable[[str], dict | None], marker: Callable[[str], str]) -> RootsLayouts:
    remote = os.environ.get("VINC_LAYOUT_REMOTE", "").strip()
    token = os.environ.get("VINC_LAYOUT_TOKEN", "").strip()
    if remote and not token:
        # 토큰 없이 부르면 컨테이너가 401 로 전부 거절한다. 켜지 않는 편이 낫다.
        print(json.dumps({"event": "layout_remote_off", "reason": "VINC_LAYOUT_TOKEN missing"}), flush=True)
        remote = ""
    return RootsLayouts(remote=remote, token=token, read_graph=read_graph, marker=marker,
                        timeout=float(os.environ.get("VINC_LAYOUT_TIMEOUT", "300") or 300))
