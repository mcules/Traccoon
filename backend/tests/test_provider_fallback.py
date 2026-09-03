"""What a refused credential does to the provider chain.

On 2026-09-02 two assistant runs died within half a second on

    claude: HTTP 403: {"error":{"type":"permission_error",
                       "message":"OAuth authentication is currently not allowed …"}}

and the next run eight minutes later went through on the same token. The 403 was one bad
minute at the provider, but it ended the runs outright: a non-retryable error raised straight
out of the chain, so the fallback provider — which has a token and an account of its own, and
is the only reason a chain exists — was never asked. These tests hold that door open, and hold
the other one shut: a request the provider refuses on its merits must NOT be paid for twice.
"""
import pytest

from app.worker.providers.base import ChatResponse, ProviderError
from app.worker.providers.router import Router

FORBIDDEN = ('claude: HTTP 403: {"type":"error","error":{"type":"permission_error",'
             '"message":"OAuth authentication is currently not allowed for this organization."}}')


class _Refuses:
    """A provider that answers every call with the same error."""

    def __init__(self, exc: ProviderError) -> None:
        self.exc, self.calls = exc, 0

    async def chat(self, **kw):
        self.calls += 1
        raise self.exc


class _Answers:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, **kw):
        self.calls += 1
        return ChatResponse(text="ok")


def _chain(monkeypatch, primary, fallback):
    """A router whose two providers are the fakes above, addressed by name."""
    router = Router()
    impls = {"claude_code": primary, "codex": fallback}
    monkeypatch.setattr(router, "_impl", lambda prov, *a, **k: impls[prov])
    return router


async def test_a_refused_credential_moves_on_to_the_fallback(monkeypatch):
    """The token is what was refused, not the request — and the fallback has another one."""
    primary = _Refuses(ProviderError(FORBIDDEN, status=403, retryable=False))
    fallback = _Answers()
    router = _chain(monkeypatch, primary, fallback)

    resp = await router.chat(provider="claude_code", model="opus", messages=[],
                             fallback="codex", fallback_model="gpt")

    assert resp.text == "ok"
    assert (resp.provider, resp.model) == ("codex", "gpt")
    # Once, not four times: repeating a refused credential at the same provider buys nothing.
    assert primary.calls == 1


async def test_the_refusing_provider_is_set_aside_for_a_while(monkeypatch):
    """The next run should not walk into the same wall first — that is what the cooldown is for."""
    primary = _Refuses(ProviderError(FORBIDDEN, status=403, retryable=False))
    router = _chain(monkeypatch, primary, _Answers())

    await router.chat(provider="claude_code", model="opus", messages=[],
                      fallback="codex", fallback_model="gpt")

    assert "claude_code" in router.cooldown_status()


async def test_without_a_fallback_the_error_still_reaches_the_run(monkeypatch):
    """Going on is the point, not swallowing: the last error is what the run is told."""
    primary = _Refuses(ProviderError(FORBIDDEN, status=403, retryable=False))
    router = _chain(monkeypatch, primary, _Answers())

    with pytest.raises(ProviderError) as caught:
        await router.chat(provider="claude_code", model="opus", messages=[])

    assert caught.value.status == 403


async def test_a_refused_request_is_not_paid_for_twice(monkeypatch):
    """A 400 is about what was sent. The fallback would build the same body and fail the same
    way, so the chain ends here — this is the branch the credential case used to fall into."""
    primary = _Refuses(ProviderError("claude: HTTP 400: bad request", status=400,
                                     retryable=False))
    fallback = _Answers()
    router = _chain(monkeypatch, primary, fallback)

    with pytest.raises(ProviderError):
        await router.chat(provider="claude_code", model="opus", messages=[],
                          fallback="codex", fallback_model="gpt")

    assert fallback.calls == 0
