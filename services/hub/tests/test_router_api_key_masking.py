import pytest
from orion.kernel.config import mask_api_key, is_masked_key, _normalize_overrides, RuntimeSettings

def test_mask_api_key():
    assert mask_api_key("") == ""
    assert mask_api_key(None) == ""
    assert mask_api_key("sk-60f3eaf169d7c485-0icocf-0a3db541") == ""
    assert mask_api_key("12345678") == "••••••••"
    assert mask_api_key("sk-orion-123456789abc") == "sk-o••••••••9abc"

def test_is_masked_key():
    assert is_masked_key("sk-o••••••••9abc") is True
    assert is_masked_key("••••••••") is True
    assert is_masked_key("sk-orion-real-key") is False
    assert is_masked_key("") is False

def test_normalize_overrides_drops_masked_key():
    # If client sends back masked key, it should not overwrite real key
    updates = {"router_api_key": "sk-o••••••••9abc"}
    normalized = _normalize_overrides(updates)
    assert "router_api_key" not in normalized

    # Real key should be accepted
    updates = {"router_api_key": "sk-orion-new-key"}
    normalized = _normalize_overrides(updates)
    assert normalized["router_api_key"] == "sk-orion-new-key"

    # Clearing key should be accepted
    updates = {"router_api_key": ""}
    normalized = _normalize_overrides(updates)
    assert normalized["router_api_key"] == ""
