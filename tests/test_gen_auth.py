"""Credential rotation must not resurrect the stale singleton auth file."""
from types import ModuleType
import sys

import pytest

from lib import gen_bridge as bridge


def install_resolver(monkeypatch, resolve):
    module = ModuleType("tools.xai_http")
    module.resolve_xai_http_credentials = resolve
    module.hermes_xai_user_agent = lambda: "Hermes-Agent/test"
    monkeypatch.setitem(sys.modules, "tools.xai_http", module)
    monkeypatch.delenv("XAI_API_KEY", raising=False)


def test_managed_pool_rotation_and_provider_route(monkeypatch):
    tokens = iter(["pool-token-one", "pool-token-two"])
    install_resolver(monkeypatch, lambda: {"api_key": next(tokens), "base_url": "https://api.x.ai/v1/"})
    calls = []
    monkeypatch.setattr(bridge.requests, "request", lambda *a, **kw: calls.append((a, kw)))
    bridge._api_request("GET", "/models", timeout=5)
    bridge._api_request("GET", "/videos/job-1", timeout=5)
    assert calls[0][0] == ("GET", "https://api.x.ai/v1/models")
    assert calls[0][1]["headers"]["Authorization"] == "Bearer pool-token-one"
    assert calls[1][1]["headers"]["Authorization"] == "Bearer pool-token-two"
    assert calls[0][1]["headers"]["User-Agent"] == "Hermes-Agent/test"


def test_missing_managed_credential_does_not_submit_or_read_raw_store(monkeypatch):
    install_resolver(monkeypatch, lambda: {"api_key": ""})
    monkeypatch.setattr(bridge.Path, "read_text", lambda *a, **k: pytest.fail("raw auth store read"))
    monkeypatch.setattr(bridge.requests, "request", lambda *a, **k: pytest.fail("unexpected API call"))
    with pytest.raises(RuntimeError, match="No xAI credential"):
        bridge._api_request("POST", "/videos/generations", json={})


def test_ambiguous_paid_submission_is_never_retried(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "explicit-test-key")
    calls = []
    def timeout(*args, **kwargs):
        calls.append(args)
        raise bridge.requests.Timeout()
    monkeypatch.setattr(bridge.requests, "request", timeout)
    with pytest.raises(bridge.requests.Timeout):
        bridge._api_request("POST", "/videos/generations", json={})
    assert len(calls) == 1
