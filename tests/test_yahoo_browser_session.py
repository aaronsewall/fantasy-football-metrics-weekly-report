from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ffmwr.dao.platforms.yahoo import YahooPlatform
from ffmwr.utilities.settings import PlatformSettings


def test_yahoo_browser_session_uses_cookie_jar_without_oauth_or_persistence():
    platform = object.__new__(YahooPlatform)
    platform_settings = PlatformSettings()
    platform_settings.yahoo_browser_cookie_jar_path = Path("private/cookies.txt")
    platform_settings.yahoo_browser_api_base_url = "https://example.invalid"
    platform_settings.yahoo_consumer_key = "not-used"
    platform_settings.yahoo_consumer_secret = "not-used"
    platform_settings.yahoo_access_token_json = {"not": "used"}
    platform.settings = SimpleNamespace(
        platform_settings=platform_settings
    )
    platform.league = SimpleNamespace(league_id="league-id", offline=False)
    platform.game_id = "nfl"
    platform.root_dir = Path(".")

    with patch("ffmwr.dao.platforms.yahoo.YahooFantasySportsQuery") as query_class:
        query = Mock()
        query_class.return_value = query
        platform._authenticate()

    kwargs = query_class.call_args.kwargs
    assert kwargs["browser_cookie_jar_path"] == Path("private/cookies.txt")
    assert kwargs["browser_api_base_url"] == "https://example.invalid"
    assert kwargs["yahoo_consumer_key"] is None
    assert kwargs["yahoo_consumer_secret"] is None
    assert kwargs["yahoo_access_token_json"] is None
    assert kwargs["env_var_fallback"] is False
    query.save_access_token_data_to_env_file.assert_not_called()
