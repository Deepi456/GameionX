import pytest


@pytest.fixture(autouse=True)
def clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests independent from local .env files and developer-specific provider secrets."""
    for key in [
        "GAMEIONX_PROVIDER",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "GEMINI_FALLBACK_MODEL",
        "GROQ_API_KEY",
        "GROQ_MODEL",
    ]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GAMEIONX_PROVIDER", "development-mock")
    from app import main

    monkeypatch.setattr(main, "provider", None)
    monkeypatch.setattr(main, "designer_agent", None)
