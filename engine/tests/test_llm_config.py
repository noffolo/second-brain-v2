import os
import pytest
import tempfile
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from google.antigravity import LocalAgentConfig
from engine.utils.llm_fallback import (
    parse_provider_model, 
    call_llm_with_fallback, 
    transcribe_audio_with_fallback,
    fetch_keys_from_free4all,
    save_key_to_env
)

def test_parse_provider_model():
    assert parse_provider_model("google/gemini-3.5-pro") == ("google", "gemini-3.5-pro")
    assert parse_provider_model("openai/gpt-5") == ("openai", "gpt-5")
    assert parse_provider_model("together/qwen-3.7max") == ("together", "qwen-3.7max")
    assert parse_provider_model("gemini-3.5-flash") == ("google", "gemini-3.5-flash")
    assert parse_provider_model("gpt-4o") == ("openai", "gpt-4o")
    assert parse_provider_model("deepseek-reasoner") == ("deepseek", "deepseek-reasoner")
    assert parse_provider_model("ollama/granite4.1:3b") == ("ollama", "granite4.1:3b")
    assert parse_provider_model(None) == (None, None)

@pytest.mark.asyncio
async def test_call_llm_with_fallback_success_first_try():
    # Mock settings.md loading to return a structured fallback chain
    mock_settings = {
        "models": {
            "ontology_agent": {
                "primary": "google/gemini-3.5-pro",
                "fallback": ["openai/gpt-5", "deepseek/deepseek-reasoner"]
            }
        }
    }
    
    config = LocalAgentConfig(
        model="gemini-3.5-pro",
        system_instructions="Instructions"
    )
    
    with patch("engine.utils.markdown.load_settings", return_value=mock_settings), \
         patch("engine.utils.llm_fallback.get_gemini_keys", return_value=["local_gemini_key"]), \
         patch("engine.utils.llm_fallback.load_rate_limited_models", return_value=set()), \
         patch("engine.utils.llm_fallback.is_key_rate_limited", return_value=False), \
         patch("engine.utils.llm_fallback.fetch_keys_from_free4all", new_callable=AsyncMock, return_value=[]), \
         patch("engine.utils.llm_fallback.call_native_gemini_api", new_callable=AsyncMock) as mock_gemini:
        
        mock_gemini.return_value = "Proposals JSON"
        
        resp = await call_llm_with_fallback(
            prompt="Generate proposals",
            system_instructions="Instructions",
            gemini_config=config,
            agent_name="ontology_agent"
        )
        
        assert resp == "Proposals JSON"
        mock_gemini.assert_called_once()

@pytest.mark.asyncio
async def test_call_llm_with_fallback_secondary_fallback():
    # Primary Google fails, falls back to OpenAI GPT-5
    mock_settings = {
        "models": {
            "ontology_agent": {
                "primary": "google/gemini-3.5-pro",
                "fallback": ["openai/gpt-5"]
            }
        }
    }
    
    config = LocalAgentConfig(
        model="gemini-3.5-pro",
        system_instructions="Instructions"
    )
    
    def mock_getenv(key, default=None):
        if key == "OPENAI_API_KEY":
            return "fake_openai_key"
        return ""
        
    with patch("engine.utils.markdown.load_settings", return_value=mock_settings), \
         patch("engine.utils.llm_fallback.get_gemini_keys", return_value=["local_gemini_key"]), \
         patch("engine.utils.llm_fallback.load_rate_limited_models", return_value=set()), \
         patch("engine.utils.llm_fallback.is_key_rate_limited", return_value=False), \
         patch("os.getenv", side_effect=mock_getenv), \
         patch("engine.utils.llm_fallback.call_native_gemini_api", side_effect=RuntimeError("Google rate limit")), \
         patch("engine.utils.llm_fallback.call_openai_compatible_api", new_callable=AsyncMock) as mock_openai, \
         patch("engine.utils.llm_fallback.fetch_keys_from_free4all", new_callable=AsyncMock, return_value=[]):
        
        mock_openai.return_value = "OpenAI response"
        
        resp = await call_llm_with_fallback(
            prompt="Generate proposals",
            system_instructions="Instructions",
            gemini_config=config,
            agent_name="ontology_agent"
        )
        
        assert resp == "OpenAI response"
        mock_openai.assert_called_once()

@pytest.mark.asyncio
async def test_free4all_integration_fallback_and_save():
    # Both local keys fail, falls back to Free4All keys and saves the working one
    mock_settings = {
        "models": {
            "ontology_agent": {
                "primary": "openai/gpt-5"
            }
        }
    }
    
    config = LocalAgentConfig(
        model="gpt-5",
        system_instructions="Instructions"
    )
    
    def mock_getenv(key, default=None):
        # Initial status: no local API keys
        if key == "FREE4ALL_PASSWORD":
            return "W3ar3pirat3s!2026"
        return ""
        
    with patch("os.getenv", side_effect=mock_getenv), \
         patch("engine.utils.markdown.load_settings", return_value=mock_settings), \
         patch("engine.utils.llm_fallback.load_rate_limited_models", return_value=set()), \
         patch("engine.utils.llm_fallback.is_key_rate_limited", return_value=False), \
         patch("engine.utils.llm_fallback.fetch_keys_from_free4all", new_callable=AsyncMock) as mock_free4all, \
         patch("engine.utils.llm_fallback.call_openai_compatible_api", new_callable=AsyncMock) as mock_openai, \
         patch("engine.utils.llm_fallback.save_key_to_env") as mock_save:
        
        mock_free4all.return_value = ["fresh_free4all_key_1"]
        mock_openai.return_value = "API Success"
        
        resp = await call_llm_with_fallback(
            prompt="Generate proposals",
            system_instructions="Instructions",
            gemini_config=config,
            agent_name="ontology_agent"
        )
        
        assert resp == "API Success"
        mock_free4all.assert_called_with("openai")
        mock_openai.assert_called_once()
        mock_save.assert_called_with("openai", "fresh_free4all_key_1")
