# -*- coding: utf-8 -*-
"""남이 고른 주소로 붙기 전에 그 주소가 공인인지 본다.

하는 것:

  - `public_address(text)` — 전 세계에서 닿는 주소인가. 사설·루프백·링크로컬·멀티캐스트·예약은
    아니다. IPv6 안에 IPv4 를 싣는 모양(매핑·6to4·Teredo)은 안의 주소까지 본다.
  - `public_ips(host, port, resolver)` — 이름을 풀어 주소들을 돌려준다. 못 풀면 `Unresolved`,
    풀린 주소 가운데 **하나라도** 공인이 아니면 `NotPublic` 이다.
  - `tls_connect(ip, port, host, timeout)` — 확인한 주소로 붙고 인증서는 이름으로 본다.
    `timeout` 은 연결과 TLS 악수를 합친 상한이다.

하면 안 되는 것:

  - 확인한 뒤에 이름을 다시 풀어 붙으면 안 된다. 두 번째 풀이가 사설 주소를 줄 수 있다.
    호출부는 `public_ips` 가 준 주소로 `tls_connect` 한다.
  - 이 모듈은 표준 라이브러리만 쓰고 `vinc_mcp` 의 다른 모듈을 들이지 않는다(어디서든 들여도
    순환이 생기지 않게).
"""
from __future__ import annotations

import ipaddress
import socket
import ssl
import time


class Unresolved(Exception):
    """이름이 풀리지 않았다."""


class NotPublic(Exception):
    """풀린 주소 가운데 공인이 아닌 것이 있다."""


def public_address(text: str) -> bool:
    """전 세계에서 닿는 주소인가. 사설·루프백·링크로컬·멀티캐스트·예약은 아니다."""
    try:
        ip = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6:
        for inner in (ip.ipv4_mapped, ip.sixtofour, (ip.teredo or (None, None))[1]):
            if inner is not None and not public_address(str(inner)):
                return False
    return bool(ip.is_global and not (ip.is_multicast or ip.is_private or ip.is_loopback
                                      or ip.is_link_local or ip.is_reserved
                                      or ip.is_unspecified))


def public_ips(host: str, port: int, resolver=socket.getaddrinfo) -> list[str]:
    """풀린 주소들(중복 없이, 풀린 순서). 하나라도 공인이 아니면 `NotPublic` 이다."""
    try:
        infos = resolver(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise Unresolved(host) from None
    addrs = list(dict.fromkeys(info[4][0] for info in infos))
    if not addrs:
        raise Unresolved(host)
    if not all(public_address(a) for a in addrs):
        raise NotPublic(host)
    return addrs


def tls_connect(ip: str, port: int, host: str, timeout: float):
    """`timeout` 은 TCP 연결과 TLS 악수를 **합친** 상한(초)이다. 넘기면 `TimeoutError`.

    악수에 거는 소켓 시간 초과는 악수 전체의 상한이다(ssl 문서, 바이트마다 다시 재지 않는다).
    그래서 연결에 쓰고 남은 시간을 악수 전에 건다. 돌려주는 소켓의 시간 초과는 `timeout` 이다."""
    end = time.monotonic() + timeout
    raw = socket.create_connection((ip, port), timeout=timeout)
    try:
        raw.settimeout(max(0.001, end - time.monotonic()))
        tls = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
    except Exception:
        raw.close()
        raise
    tls.settimeout(timeout)
    return tls
