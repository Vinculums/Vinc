# -*- coding: utf-8 -*-
"""시간 가지 배치 — 허브 밖 배치 컨테이너(`vinc-layout`)에 맡기고 결과를 들고 있는다.

화면이 시간 가지를 열 때마다 브라우저가 전체를 다시 배치하면 1만 노드에서 1분을 넘긴다.
그래서 허브가 쓰기 뒤에 미리 배치를 맡기고, 화면은 들고 있는 결과를 받아 바로 그린다.
배치 컨테이너는 주제 조각·통로·상자 자리를 공간별로 기억하므로 쓰기 뒤의 재배치는 바뀐
주제만 다시 한다(`app/src/layout-service.ts`).
은퇴 1단계: 허브에 배치 환경변수가 없어 이 경로는 꺼져 있고, 2단계에서 지운다.
근거: [[decision:vinc-layout-is-retired-in-two-steps-2026-11-07]]

계약:

  - **결과는 변경 표식(`api.changes`)에 묶인다.** 배치에 쓴 읽기의 표식을 읽기 **전에**
    잡아 함께 둔다. 표식이 지금 것이면 `ready`, 아니면 `stale` 이다.
  - **결과는 배치 코드 판(`version`)에도 묶인다.** 컨테이너가 답과 `/health` 에 자기 판을
    싣는다. 판이 바뀌면(layout-deploy) 쓰기가 없어도 들고 있던 결과는 `stale` 이 되고
    다시 배치한다. 컨테이너가 판을 싣지 않으면 표식만 본다.
  - **`stale` 도 답을 싣는다.** 화면은 그것을 먼저 그리고 `ready` 가 되면 갈아 끼운다
    (`app/src/view-graph-loader.ts`). `digest` 는 답 본문의 해시이고, 화면이 `have` 로 같은
    값을 보내면 본문을 빼고 상태만 준다. 따라 묻는 요청이 몇 MB 를 다시 받지 않게 하려는 것이다.
  - **재시작에 살아남는다.** 결과를 영속 볼륨에 슬롯마다 파일 하나로 둔다(`store_dir_from_env`).
    읽어 들인 결과는 표식을 모르므로 늘 `stale` 이고, 처음 물을 때 다시 배치한다.
    임베디드 DB 를 SMB 에 두지 말라는 규칙([[vinc.manual.core]] §3)과 다른 경우다: 이것은
    통째로 임시 파일에 쓰고 `os.replace` 로 옮기는 캐시이고, 잠금·WAL·부분 갱신이 없으며,
    깨지거나 없어도 한 번 더 배치할 뿐이다. 쓰기가 몰려도 슬롯마다 `save_every` 에 한 번만 쓴다.
  - **관심 있는 공간만 미리 한다.** 화면이 한 번 물은 (소유자, 시간 기준)만 쓰기 뒤에
    다시 배치한다. 아무도 안 보는 공간을 위해 컨테이너를 깨우지 않는다.
  - **한 번에 하나.** 일꾼 스레드 하나가 차례로 보낸다. 허브는 1코어이고 컨테이너도 작다.
  - **보내는 것은 id·종류·주제·시각·관계 끝점뿐이다.** 제목·본문·props 는 나가지 않는다.
    공간 이름도 원문 대신 해시를 보낸다. 파일 이름도 해시다.
  - **꺼져 있으면(`VINC_LAYOUT_REMOTE` 없음) `state:"off"`.** 화면은 예전처럼 직접 배치한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from collections import OrderedDict
from typing import Callable

from vinc_mcp import hubhttp

BASES = ("recorded", "content")

#: 파일 머리의 형식 판. 다른 형식의 파일은 읽지 않는다(다시 배치할 뿐이다).
_FILE_V = 1


def _space_key(owner: str) -> str:
    return hashlib.sha256(("vinc-layout:" + owner).encode("utf-8")).hexdigest()[:32]


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


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
    return hubhttp.post_json(url, token=token, payload=payload, timeout=timeout)


class _Item:
    __slots__ = ("marker", "text", "nodes", "version", "digest")

    def __init__(self, marker: str, text: str, nodes: int, version: str = "") -> None:
        self.marker, self.text, self.nodes, self.version = marker, text, nodes, version
        self.digest = _digest(text)


class RootsLayouts:
    def __init__(self, *, remote: str, token: str,
                 read_graph: Callable[[str], dict | None], marker: Callable[[str], str],
                 post: Callable[[dict], dict] | None = None, timeout: float = 300.0,
                 debounce: float = 5.0, interest_s: float = 1800.0, retry_s: float = 600.0,
                 max_bytes: int = 96 * 1024 * 1024, clock: Callable[[], float] = time.monotonic,
                 health: Callable[[], dict] | None = None, probe_s: float = 60.0,
                 store_dir: str | None = None, save_every: float = 300.0,
                 keep_s: float = 30 * 86400.0) -> None:
        self.remote = remote.rstrip("/")
        self._read_graph, self._marker, self._clock = read_graph, marker, clock
        self._post = post or (lambda payload: _post(f"{self.remote}/v1/layout", token, payload, timeout))
        self._health = health or (lambda: hubhttp.get_json(f"{self.remote}/health", token=None, timeout=2.0))
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
        # 배치 코드 판: 컨테이너가 마지막으로 알려 준 값. 모르면 None 이고 이 경우 표식만 본다.
        self._version: str | None = None
        self.probe_s, self._probed = probe_s, float("-inf")
        # 영속 파일. 없으면(데스크톱·시험) 메모리에만 든다.
        self._dir, self.save_every, self.keep_s = store_dir or None, save_every, keep_s
        self._tried: set[tuple[str, str]] = set()          # 이 프로세스에서 파일을 읽어 본 슬롯
        self._saved: dict[tuple[str, str], float] = {}     # 슬롯 -> 마지막 저장 시각
        self._dirty: set[tuple[str, str]] = set()          # 저장 주기에 걸려 아직 안 쓴 슬롯
        self._swept = False

    @property
    def enabled(self) -> bool:
        return bool(self.remote)

    # ── 화면이 묻는다 ────────────────────────────────────────────────────
    def get(self, owner: str, basis: str, have: str | None = None) -> tuple[int, object]:
        """``have``: 화면이 이미 그린 답의 `digest`. 같으면 본문을 빼고 상태만 준다."""
        if not self.enabled:
            return 200, {"executed": True, "state": "off"}
        if basis not in BASES:
            return 400, {"executed": False, "error": "bad_basis"}
        slot = (owner, basis)
        self._check_version()
        self._load(slot)
        current, now = self._marker(owner), self._clock()
        with self._lock:
            self._interest[slot] = now
            item = self._items.get(slot)
            if item is not None:
                self._items.move_to_end(slot)
            if item is None or not self._fresh(item, current):
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
        head = {"executed": True, "state": state, "marker": item.marker, "nodes": item.nodes,
                "version": item.version, "digest": item.digest}
        if have and have == item.digest:
            return 200, head
        from .graph_loading import GraphWire
        text = json.dumps(head, ensure_ascii=False, separators=(",", ":"))
        return 200, GraphWire((text[:-1] + ',"answer":' + item.text + "}\n").encode("utf-8"))

    def _fresh(self, item: _Item, current: str) -> bool:
        """락을 쥔 상태로 부른다. 표식과 배치 코드 판이 둘 다 지금 것인가."""
        return item.marker == current and (self._version is None or item.version == self._version)

    def _check_version(self) -> None:
        """배치 컨테이너의 판을 가끔(``probe_s``) 묻는다. 답이 없으면 아는 값으로 계속한다.

        요청 길에서 묻는 이유: layout-deploy 뒤 쓰기가 없어도 다음 화면이 새 배치로 넘어가야 한다."""
        now = self._clock()
        with self._lock:
            if now - self._probed < self.probe_s:
                return
            self._probed = now
        try:
            reply = self._health()
        except Exception:   # noqa: BLE001 — 판을 못 물으면 아는 값으로 계속한다
            return
        version = reply.get("version") if isinstance(reply, dict) else None
        if isinstance(version, str) and version:
            with self._lock:
                self._version = version

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
            same = held is not None and self._fresh(held, marker)
        if same:
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
        stats = reply.get("stats")
        version = reply.get("version") if isinstance(reply.get("version"), str) else ""
        print(json.dumps({"event": "layout_remote", "basis": basis, "nodes": len(answer["nodes"]),
                          "ms": round((self._clock() - started) * 1000), "version": version or None,
                          "service": stats if isinstance(stats, dict) else None}), flush=True)
        item = _Item(marker, text, len(answer["nodes"]), version)
        with self._lock:
            if version:
                self._version = version
            self._failed.pop(slot, None)
            changed = held is None or held.digest != item.digest or held.version != item.version
            self._put(slot, item)
            self.runs += 1
            # 배치 도중 쓰기가 끝났으면(비동기 잡) 표식이 이미 넘어갔다. 아직 보는 공간이면 한 번 더 한다.
            if self._marker(owner) != marker and slot in self._interest:
                self._schedule(slot, self._clock())
        if changed:
            self._persist(slot, item)
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

    # ── 영속 파일 ───────────────────────────────────────────────────────
    def _file(self, slot) -> str:
        name = hashlib.sha256(("vinc-layout-file:" + slot[0] + "\0" + slot[1]).encode("utf-8")).hexdigest()[:32]
        return os.path.join(self._dir, name + ".json")   # type: ignore[arg-type]

    def _load(self, slot) -> None:
        """메모리에 없는 슬롯은 프로세스마다 한 번 파일에서 읽는다. 표식을 모르므로 늘 `stale` 로 나간다."""
        if not self._dir:
            return
        with self._lock:
            if slot in self._items or slot in self._tried:
                return
            self._tried.add(slot)
        try:
            with open(self._file(slot), encoding="utf-8") as fh:
                head = json.loads(fh.readline())
                text = fh.read()
            if head.get("v") != _FILE_V or head.get("basis") != slot[1]:
                return
            answer = json.loads(text)
            if not isinstance(answer, dict) or not isinstance(answer.get("nodes"), list):
                return
        except (OSError, ValueError, AttributeError):
            return   # 없거나 깨진 파일: 다시 배치하면 된다
        with self._lock:
            if slot not in self._items:
                self._put(slot, _Item("", text, len(answer["nodes"]), str(head.get("version") or "")))

    def _persist(self, slot, item: _Item, *, force: bool = False) -> None:
        """답을 파일로 둔다. 슬롯마다 ``save_every`` 에 한 번만 쓰고, 그 사이 것은 닫을 때 쓴다."""
        if not self._dir:
            return
        now = self._clock()
        with self._lock:
            if not force and now - self._saved.get(slot, float("-inf")) < self.save_every:
                self._dirty.add(slot)
                return
            self._saved[slot] = now
            self._dirty.discard(slot)
        path = self._file(slot)
        tmp = f"{path}.{secrets.token_hex(4)}.tmp"
        head = json.dumps({"v": _FILE_V, "basis": slot[1], "version": item.version, "nodes": item.nodes},
                          separators=(",", ":"))
        try:
            os.makedirs(self._dir, exist_ok=True)
            self._sweep()
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(head + "\n" + item.text)
            os.replace(tmp, path)
        except OSError as exc:
            try:
                os.remove(tmp)
            except OSError:
                pass
            print(json.dumps({"event": "layout_remote_save_failed", "error": str(exc)[:200]}), flush=True)

    def _sweep(self) -> None:
        """프로세스마다 한 번, ``keep_s`` 넘게 안 쓴 파일을 지운다(아무도 안 여는 공간·지운 계정)."""
        if self._swept:
            return
        self._swept = True
        cutoff = time.time() - self.keep_s
        try:
            names = os.listdir(self._dir)   # type: ignore[arg-type]
        except OSError:
            return
        for name in names:
            path = os.path.join(self._dir, name)   # type: ignore[arg-type]
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass

    def close(self) -> None:
        """일꾼을 멈추고, 저장 주기에 걸려 아직 안 쓴 답을 파일로 둔다(배포의 정상 종료)."""
        with self._lock:
            self._closed = True
            self._lock.notify_all()
            pending = [(slot, self._items[slot]) for slot in self._dirty if slot in self._items]
        for slot, item in pending:
            self._persist(slot, item, force=True)


def store_dir_from_env() -> str | None:
    """영속 파일 자리. `VINC_LAYOUT_STORE` 가 우선이고, 없으면 그래프 스냅샷(영속 볼륨)의 형제다.
    스냅샷이 없으면 두지 않는다: 작업본 옆은 컨테이너 디스크라 배포마다 사라진다."""
    explicit = os.environ.get("VINC_LAYOUT_STORE", "").strip()
    if explicit:
        return explicit
    snapshot = os.environ.get("VINC_STORE_SNAPSHOT", "").strip().rstrip("/\\")
    return snapshot + ".layouts" if snapshot else None


def from_env(*, read_graph: Callable[[str], dict | None], marker: Callable[[str], str]) -> RootsLayouts:
    remote = os.environ.get("VINC_LAYOUT_REMOTE", "").strip()
    token = os.environ.get("VINC_LAYOUT_TOKEN", "").strip()
    if remote and not token:
        # 토큰 없이 부르면 컨테이너가 401 로 전부 거절한다. 켜지 않는 편이 낫다.
        print(json.dumps({"event": "layout_remote_off", "reason": "VINC_LAYOUT_TOKEN missing"}), flush=True)
        remote = ""
    return RootsLayouts(remote=remote, token=token, read_graph=read_graph, marker=marker,
                        timeout=float(os.environ.get("VINC_LAYOUT_TIMEOUT", "300") or 300),
                        store_dir=store_dir_from_env() if remote else None)
