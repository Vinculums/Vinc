"""Each CrewAI helper takes exactly the parameters of the client method it wraps.

A helper names the arguments it needs and forwards the rest as ``**fields``, so a parameter
the client gains or has reaches the host without a second copy of the list. The check reads
the signatures, then calls every helper with every client parameter set.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

pytest.importorskip("crewai")

from vinc.integrations.crewai import helpers  # noqa: E402
from vinc.sdk import AsyncVincClient, VincClient  # noqa: E402

# 도우미 이름 -> (클라이언트 메서드, {도우미 인자: 클라이언트 인자}, {인자: 도우미가 일부러 다르게 두는 기본값}).
# 비동기 짝은 "a" 접두 이름으로 같은 메서드에 대응한다.
HELPERS = {
    "get_vinc_package_context": ("get_package_context", {}, {}),
    "prefetch": ("prefetch", {}, {}),
    "record_episode": ("record_episode", {}, {}),
    "record_run": ("record_run", {}, {}),
    "write_package": ("write_package", {}, {}),
}
# 클라이언트 메서드를 감싸지 않는 도우미. 첫 인자가 client 여도 위 표에 들지 않는다.
NOT_WRAPPERS = set()

CASES = [pytest.param(name, is_async, id=("a" if is_async else "") + name)
         for name in HELPERS for is_async in (False, True)]


def parameters(function):
    """The parameters after the first one (``client`` or ``self``)."""
    return list(inspect.signature(function).parameters.values())[1:]


def lookup(name, is_async):
    method, renamed, defaults = HELPERS[name]
    helper = getattr(helpers, ("a" if is_async else "") + name)
    return helper, getattr(AsyncVincClient if is_async else VincClient, method), renamed, defaults


@pytest.mark.parametrize("name, is_async", CASES)
def test_helper_signature_is_the_client_methods(name, is_async):
    helper, method, renamed, defaults = lookup(name, is_async)
    own, theirs = parameters(helper), {p.name: p for p in parameters(method)}
    named = [p for p in own if p.kind is not p.VAR_KEYWORD]
    target = {p.name: renamed.get(p.name, p.name) for p in named}
    for p in named:
        assert target[p.name] in theirs, f"{p.name} is not a parameter of {method.__qualname__}"
        q = theirs[target[p.name]]
        assert p.kind is q.kind, f"{p.name}: {p.kind.name} here, {q.kind.name} in the client"
        if p.name in renamed:
            continue
        if p.name in defaults:
            # 일부러 다른 기본값은 표의 값과 같아야 하고, 클라이언트와 같아지면 표에서 지운다.
            assert p.default == defaults[p.name] != q.default, p.name
        else:
            assert (p.default, p.annotation) == (q.default, q.annotation), p.name
    forwarded = set(theirs) - set(target.values())
    assert all(theirs[n].kind is inspect.Parameter.KEYWORD_ONLY for n in forwarded), \
        "a parameter the helper does not name must be keyword only to travel in **fields"
    assert not forwarded or len(named) < len(own), f"{helper.__name__} drops {sorted(forwarded)}"
    # 필수 인자는 도우미가 이름으로 받는다. 빠지면 TypeError 가 도우미 이름 없이 클라이언트에서 난다.
    assert {n for n, q in theirs.items() if q.default is q.empty} <= set(target.values())
    assert inspect.signature(helper).return_annotation == inspect.signature(method).return_annotation


@pytest.mark.parametrize("name, is_async", CASES)
def test_helper_hands_every_client_parameter_to_the_method(name, is_async):
    helper, method, renamed, _ = lookup(name, is_async)
    back = {client_name: host_name for host_name, client_name in renamed.items()}
    own = [p for p in parameters(helper) if p.kind is not p.VAR_KEYWORD]
    # 이름을 바꾼 첫 인자는 문자열이다. 도우미가 메시지 목록에서 마지막 사용자 글을 뽑는 자리다.
    sent = {q.name: "probe" if q.name in back else object() for q in parameters(method)}
    positional = [p.name for p in own if p.kind is p.POSITIONAL_OR_KEYWORD]
    seen = []

    class Recorder:
        def __getattr__(self, attribute):
            if attribute != method.__name__:
                raise AttributeError(attribute)

            def call(*args, **kwargs):
                seen.append((args, kwargs))
                return "ok"

            async def acall(*args, **kwargs):
                return call(*args, **kwargs)

            return acall if is_async else call

    args = [sent[renamed.get(n, n)] for n in positional]
    kwargs = {back.get(n, n): v for n, v in sent.items() if back.get(n, n) not in positional}
    result = helper(Recorder(), *args, **kwargs)
    assert (asyncio.run(result) if is_async else result) == "ok"
    (got_args, got_kwargs), = seen
    bound = dict(inspect.signature(method).bind(None, *got_args, **got_kwargs).arguments)
    bound.pop("self")
    assert bound == sent


def test_every_client_helper_is_listed():
    found = {n for n, f in vars(helpers).items()
             if inspect.isfunction(f) and f.__module__ == helpers.__name__ and not n.startswith("_")
             and next(iter(inspect.signature(f).parameters), "") == "client"}
    listed = set(HELPERS) | {"a" + n for n in HELPERS} | NOT_WRAPPERS
    assert found == listed, f"unlisted: {sorted(found - listed)}, gone: {sorted(listed - found)}"
