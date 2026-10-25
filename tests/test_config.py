import pytest

from cdc_bot.config import ConfigError, load_config


def test_keepalive_defaults_to_five_minutes(monkeypatch):
    monkeypatch.delenv("KEEPALIVE_MINUTES", raising=False)
    assert load_config().keepalive_minutes == 5.0


@pytest.mark.parametrize("raw, expected", [("0", 0.0), ("1", 2.0), ("10", 10.0), ("-3", 0.0)])
def test_keepalive_is_clamped_or_disabled(monkeypatch, raw, expected):
    monkeypatch.setenv("KEEPALIVE_MINUTES", raw)
    assert load_config().keepalive_minutes == expected


def test_nonsense_keepalive_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("KEEPALIVE_MINUTES", "soon")
    with pytest.raises(ConfigError, match="KEEPALIVE_MINUTES"):
        load_config()
