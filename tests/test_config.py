import pytest

from stellar_intelligence_bot.app.config import Settings


def test_settings_use_default_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_LOG_LEVEL", raising=False)

    assert Settings.from_env() == Settings(log_level="INFO")


def test_settings_normalize_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_LOG_LEVEL", "debug")

    assert Settings.from_env().log_level == "DEBUG"


def test_settings_reject_unknown_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_LOG_LEVEL", "TRACE")

    with pytest.raises(ValueError, match="Unsupported BOT_LOG_LEVEL"):
        Settings.from_env()


def test_settings_parse_pairs_and_asset_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    issuer = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"
    monkeypatch.setenv("BOT_MONITORED_PAIRS", f"native/USDC:{issuer}")
    monkeypatch.setenv("BOT_WHALE_THRESHOLDS", f"native=100,USDC:{issuer}=500")

    settings = Settings.from_env()

    assert settings.monitored_pairs[0].key == f"native/USDC:{issuer}"
    assert settings.whale_thresholds == {
        "native": 100,
        f"USDC:{issuer}": 500,
    }


def test_settings_reject_invalid_whale_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_WHALE_THRESHOLDS", "native=0")

    with pytest.raises(ValueError, match="positive finite amount"):
        Settings.from_env()


def test_settings_reject_zero_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_RETENTION_DAYS", "0")

    with pytest.raises(ValueError, match="BOT_RETENTION_DAYS"):
        Settings.from_env()
