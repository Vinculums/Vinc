# -*- coding: utf-8 -*-
"""변경 표식 — 열린 화면이 "그 뒤로 이 그래프에 쓰기가 있었나"를 싸게 묻는 값(VIN-129).

화면은 이 값을 몇 초마다 읽고, 지난번과 다르면 지금 보던 것을 다시 부른다. 그래서
MCP 가 쓴 것이 갱신 버튼 없이 열린 View 에 나타난다(웹과 데스크톱 계정 모드 둘 다).

**세는 곳은 사이드카의 쓰기 관문이다**(`api.server` 의 POST 처리). 이 값은 데이터가
아니라 "쓰기 요청이 성공했다"를 센다:

  - 소유자별이다. 허브는 모든 사람의 그래프를 한 프로세스에서 돌리므로, 전역 번호면
    누가 쓰든 모든 열린 화면이 다시 그려진다.
  - 메모리에만 있다. 읽기가 스토어를 전혀 안 건드리므로 폴링이 허브의 단일 writer 에
    짐을 얹지 않는다. 값 앞에 프로세스마다 새로 뽑는 세대(epoch)를 붙여, 재시작하면
    표식이 바뀐다. 재시작은 쓰기를 되감을 수 있으므로(스냅샷 창) 그때 다시 읽는 것이 맞다.
  - 비교만 한다. 값의 크기나 차이에는 뜻이 없다. 같으면 그대로, 다르면 다시 읽는다.

⚠ 여기서 멀티 레플리카를 가정하지 않는다. 임베디드 스토어는 단일 writer 라 쓰기를
받는 프로세스가 하나뿐이고, 표식도 그 프로세스 하나가 센다.
근거: [[record:the-change-marker-is-counted-at-the-sidecar-write-gate-2026-09-24]]
"""
from __future__ import annotations

import secrets
import threading
from typing import Callable

#: 소유자 없이 들어온 쓰기의 칸. 어느 소유자의 표식에도 더해진다: 누구의 것인지
#: 모르는 쓰기를 한 사람에게만 알리면 나머지가 놓친다(더 자주 다시 읽는 편이 안전측).
ANY = "*"


class ChangeMarker:
    """소유자별 쓰기 세대. 스레드 안전(HTTP 핸들러 스레드와 잡 워커가 같이 쓴다)."""

    def __init__(self) -> None:
        self._epoch = secrets.token_hex(4)
        self._n: dict[str, int] = {}
        self._pending: list = []   # (owner, Job, changed) — 끝나면 센다
        self._lock = threading.Lock()

    def bump(self, owner: str | None) -> None:
        key = owner or ANY
        with self._lock:
            self._n[key] = self._n.get(key, 0) + 1

    def bump_when_done(self, owner: str | None, job,
                       changed: Callable[[dict], bool] | None = None) -> None:
        """비동기 잡(202)은 **끝난 뒤에** 센다. 접수 때 세면 화면이 데이터가 들어오기
        전에 다시 읽고, 그 뒤로 표식이 안 바뀌어 결과가 영영 안 나타난다.

        ``changed`` 는 성공한 결과를 보고 셀지 정한다. 주기적으로 도는 잡(동기화)이
        아무것도 안 받았을 때까지 세면, 열린 화면이 10분마다 까닭 없이 다시 그려진다.

        스레드를 따로 두지 않는다. 표식을 읽을 때 끝난 잡을 걷어 센다(`read`)."""
        if job is None:
            return
        with self._lock:
            self._pending.append((owner or ANY, job, changed))

    def _settle(self) -> None:
        """끝난 잡을 센다. 호출부가 락을 쥔 상태여야 한다."""
        if not self._pending:
            return
        keep = []
        for owner, job, changed in self._pending:
            done = getattr(job, "_done", None)
            if done is None or not done.is_set():
                keep.append((owner, job, changed))
                continue
            # 실패로 끝난 잡은 판정 없이 센다. 중간까지 쓰고 실패했을 수 있고, 다시
            # 읽어서 손해 볼 것은 왕복 한 번이다. 판정이 터져도 같은 쪽으로 기운다.
            count = True
            if changed is not None and getattr(job, "status", "") != "error":
                try:
                    count = bool(changed(getattr(job, "result", None) or {}))
                except Exception:   # noqa: BLE001
                    count = True
            if count:
                self._n[owner] = self._n.get(owner, 0) + 1
        self._pending = keep

    def read(self, owner: str | None) -> str:
        """이 소유자의 표식. 소유자 없는 쓰기(`ANY`)도 함께 센다."""
        with self._lock:
            self._settle()
            n = self._n.get(owner or ANY, 0)
            if owner and owner != ANY:
                n += self._n.get(ANY, 0)
            return f"{self._epoch}.{n}"


#: 성공해도 **화면에 보이는 것을 바꾸지 않는** 쓰기. 여기 없는 쓰기는 전부 센다
#: (새 라우트를 더하고 잊으면 한 번 더 다시 읽을 뿐이고, 반대로 빠뜨리면 화면이 낡는다).
#:
#:   /touch            조회 빈도 신호. 노드를 열 때마다 앱이 부른다. 세면 노드를 누를
#:                     때마다 지도가 다시 그려진다
#:   /doc/reembed      벡터만 바꾼다
#:   /embed/apply      임베딩 워커의 결과. 허브에서 쉬지 않고 돈다
#:   /node-mirror/rebuild  배포 경계의 색인 재구축(그때는 세대가 이미 바뀌었다)
#:   /backup           파일을 만들 뿐이다
#:   /sync/domains     동기화할 주제 선택. 그래프는 그대로다
#:   /connect/start    기기 연결 논스
#:   /space/proxy      계정으로 나가는 통과로. 이 스토어에 쓰지 않는다(표식은 계정 쪽
#:                     허브가 센다)
#:   /conventions/build  GRAPH.md 재생성. 그래프가 아니다
QUIET_WRITES = frozenset({
    "/touch", "/doc/reembed", "/embed/apply", "/node-mirror/rebuild", "/backup",
    "/sync/domains", "/connect/start", "/space/proxy", "/conventions/build",
})


def counts(path: str) -> bool:
    """이 POST 가 성공하면 표식을 올리는가."""
    return path not in QUIET_WRITES


def sync_brought_something(result: dict) -> bool:
    """동기화 세션(`api.sync.run`)이 이 스토어에 무엇이든 들였는가. 올려 보내기만 한
    세션은 이 기기의 그래프를 바꾸지 않는다."""
    pull = result.get("pull") or {}
    docs = result.get("docs_pull") or {}
    moved = sum(int(pull.get(k) or 0) for k in ("applied", "deleted"))
    moved += sum(int(docs.get(k) or 0) for k in ("applied", "updated", "deleted"))
    return moved > 0 or bool(result.get("birth_resync")) \
        or bool(result.get("account_switched"))
