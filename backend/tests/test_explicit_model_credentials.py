"""An explicit connection key never falls through to another account's environment."""

import pytest

from tradingagents.llm_clients.anthropic_client import AnthropicClient
from tradingagents.llm_clients.google_client import GoogleClient
from tradingagents.llm_clients.openai_client import OpenAIClient


def test_explicit_deepseek_key_works_without_environment_and_overrides_it(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    model = OpenAIClient("deepseek-chat", provider="deepseek", api_key="connection-one").get_llm()
    assert model.openai_api_key.get_secret_value() == "connection-one"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "other-account")
    model = OpenAIClient("deepseek-chat", provider="deepseek", api_key="connection-two").get_llm()
    assert model.openai_api_key.get_secret_value() == "connection-two"


@pytest.mark.parametrize("client,args,env", [
    (OpenAIClient, {"model": "deepseek-chat", "provider": "deepseek"}, "DEEPSEEK_API_KEY"),
    (AnthropicClient, {"model": "claude-sonnet-5"}, "ANTHROPIC_API_KEY"),
    (GoogleClient, {"model": "gemini-3.5-flash"}, "GOOGLE_API_KEY"),
])
def test_explicit_empty_key_does_not_fall_back_to_environment(monkeypatch, client, args, env):
    monkeypatch.setenv(env, "other-account")
    with pytest.raises(ValueError, match="Explicit API key"):
        client(**args, api_key="").get_llm()
