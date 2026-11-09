import pytest

from stellar_intelligence_bot.app.config import Settings


def test_settings_use_default_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_LOG_LEVEL", raising=False)

    settings = Settings.from_env()
    assert settings.log_level == "INFO"
    assert [entry.pair.quote.code for entry in settings.watchlist if entry.enabled] == [
        "USDC",
        "SHX",
        "VELO",
        "AQUA",
    ]


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


def test_retention_defaults_to_30_days_and_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BOT_RETENTION_DAYS", raising=False)
    assert Settings().retention_days == 30
    assert Settings.from_env().retention_days == 30
    monkeypatch.setenv("BOT_RETENTION_DAYS", "90")
    assert Settings.from_env().retention_days == 90


def test_price_windows_are_configurable_and_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_PRICE_WINDOWS_MINUTES", "5,60")
    assert Settings.from_env().price_windows_minutes == (5, 60)
    monkeypatch.setenv("BOT_PRICE_WINDOWS_MINUTES", "5,7")
    with pytest.raises(ValueError, match="BOT_PRICE_WINDOWS_MINUTES"):
        Settings.from_env()


def test_watchlist_can_enable_tier_two_or_disable_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_WATCHLIST_ENABLED", "BTC,XRP")
    settings = Settings.from_env()
    assert [entry.pair.quote.code for entry in settings.watchlist if entry.enabled] == [
        "BTC",
        "XRP",
    ]
    assert all(entry.tier == 2 for entry in settings.watchlist if entry.enabled)
    monkeypatch.setenv("BOT_WATCHLIST_ENABLED", "")
    assert not Settings.from_env().monitored_pairs


def test_watchlist_rejects_unknown_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_WATCHLIST_ENABLED", "USDC,UNKNOWN")
    with pytest.raises(ValueError, match="Unknown BOT_WATCHLIST_ENABLED"):
        Settings.from_env()


def test_watchlist_exact_identities_and_legacy_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_MONITORED_PAIRS", raising=False)
    settings = Settings.from_env()
    assert [entry.pair.key for entry in settings.watchlist] == [
        "native/USDC:GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN",
        "native/SHX:GDSTRSHXHGJ7ZIVRBXEYE5Q74XUVCUSEKEBR7UCHEUUEK72N7I7KJ6JH",
        "native/VELO:GDM4RQUQQUVSKQA7S6EM7XBZP3FCGH4Q7CL6TABQ7B2BEJ5ERARM2M5M",
        "native/AQUA:GBNZILSTVQZ4R7IKQDGHYGY2QXL5QOFJYQMXPKWRRM5PAV7Y4M67AQUA",
        "native/BTC:GDPJALI4AZKUU2W426U5WKMAT6CN3AJRPIIRYR2YM54TL2GDWO5O2MZM",
        "native/XRP:GBXRPL45NPHCVMFFAYZVUVFFVKSIZ362ZXFP7I2ETNQ3QKZMFLPRDTD5",
    ]
    custom = "native/USDC:GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"
    monkeypatch.setenv("BOT_MONITORED_PAIRS", custom)
    assert [pair.key for pair in Settings.from_env().monitored_pairs] == [custom]
