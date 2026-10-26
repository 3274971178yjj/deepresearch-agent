"""ModelRouter 配置解析测试（不发起任何网络请求，仅测配置装配与缓存）。"""
import pytest

from src.models.model_router import ModelRouter
from src.models.vllm_policy import VLLMPolicy


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """隔离环境变量并清空后端缓存，避免用例间串扰。"""
    for var in list(__import__("os").environ):
        if var.startswith(("DEEPSEEK_", "VLLM_", "OPENAI_", "MIMO_", "DEFAULT_LLM_BACKEND")):
            monkeypatch.delenv(var, raising=False)
    ModelRouter.clear_cache()
    yield
    ModelRouter.clear_cache()


class TestLoadBackendConfig:
    def test_vllm_defaults_filled(self, monkeypatch):
        monkeypatch.setenv("VLLM_API_KEY", "EMPTY")
        cfg = ModelRouter._load_backend_config("vllm")
        assert cfg["model_name"] == "Qwen/Qwen2.5-7B-Instruct"
        assert cfg["base_url"] == "http://localhost:8000/v1"

    def test_deepseek_defaults_filled(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        cfg = ModelRouter._load_backend_config("deepseek")
        assert cfg["model_name"] == "deepseek-chat"
        assert cfg["base_url"] == "https://api.deepseek.com/v1"
        assert cfg["api_key"] == "sk-test"

    def test_mimo_defaults_filled(self, monkeypatch):
        monkeypatch.setenv("MIMO_API_KEY", "k")
        cfg = ModelRouter._load_backend_config("mimo")
        assert cfg["model_name"] == "mimo-v2.5-pro"

    def test_env_overrides_defaults(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
        monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-reasoner")
        cfg = ModelRouter._load_backend_config("deepseek")
        assert cfg["model_name"] == "deepseek-reasoner"

    def test_optional_temperature_parsed(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
        monkeypatch.setenv("DEEPSEEK_TEMPERATURE", "0.3")
        cfg = ModelRouter._load_backend_config("deepseek")
        assert cfg["temperature"] == 0.3

    def test_unconfigured_backend_raises(self):
        with pytest.raises(ValueError, match="未配置"):
            ModelRouter._load_backend_config("nonexistent")


class TestCreateBackend:
    def test_creates_policy_with_overrides(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        policy = ModelRouter.create_backend("deepseek", temperature=0.2)
        assert isinstance(policy, VLLMPolicy)
        assert policy.temperature == 0.2

    def test_uses_default_backend_env(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        monkeypatch.setenv("DEFAULT_LLM_BACKEND", "deepseek")
        policy = ModelRouter.create_backend()
        assert isinstance(policy, VLLMPolicy)

    def test_backend_cached(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        p1 = ModelRouter.create_backend("deepseek", temperature=0.5)
        p2 = ModelRouter.create_backend("deepseek", temperature=0.5)
        assert p1 is p2

    def test_clear_cache_invalidates(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        p1 = ModelRouter.create_backend("deepseek", temperature=0.5)
        ModelRouter.clear_cache()
        p2 = ModelRouter.create_backend("deepseek", temperature=0.5)
        assert p1 is not p2


class TestIsConfigured:
    def test_configured_by_api_key(self, monkeypatch):
        monkeypatch.setenv("MIMO_API_KEY", "k")
        assert ModelRouter._is_backend_configured("mimo")

    def test_not_configured(self):
        assert not ModelRouter._is_backend_configured("never_configured_xyz")
