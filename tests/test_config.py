import pytest

from paybot.config import ConfigError, load_settings

BASE = {"BOT_TOKEN": "1:x", "PROVIDER_TOKEN": "1:TEST:abc", "ADMIN_IDS": "5"}


def test_test_token_enables_test_mode():
    s = load_settings(BASE)
    assert s.test_mode and s.currency == "UAH" and s.admin_ids == {5}


def test_live_token_needs_explicit_switch():
    live = {**BASE, "PROVIDER_TOKEN": "1:LIVE:abc"}
    with pytest.raises(ConfigError):
        load_settings(live)
    assert load_settings({**live, "ALLOW_LIVE_PAYMENTS": "yes"}).test_mode is False


@pytest.mark.parametrize("key, value", [("BOT_TOKEN", ""), ("PROVIDER_TOKEN", ""), ("CURRENCY", "гривна"), ("ADMIN_IDS", "a")])
def test_bad_settings(key, value):
    with pytest.raises(ConfigError):
        load_settings({**BASE, key: value})
