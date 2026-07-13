import json
import subprocess

import pytest

import app.browser_auth as browser_auth
from app.browser_auth import BrowserAuthManager, parse_metric_number


def test_xiaohongshu_creator_cookie_is_authenticated(tmp_path):
    manager = BrowserAuthManager(tmp_path)
    manager._cdp_call = lambda page, method, params=None: {
        "cookies": [{"name": "access-token-creator.xiaohongshu.com", "value": "token"}]
    }

    assert manager._is_authenticated("小红书", {"url": "https://creator.xiaohongshu.com/"}) is True


def test_fallback_account_name_uses_real_platform_id():
    assert BrowserAuthManager._fallback_account_name("抖音", "1234567890") == "抖音账号（ID 34567890）"


def test_parse_metric_number_supports_platform_units():
    assert parse_metric_number("1.2万") == 12000
    assert parse_metric_number("2,345") == 2345
    assert parse_metric_number("+27") == 27


def test_platform_data_page_must_finish_rendering_before_sync(tmp_path):
    manager = BrowserAuthManager(tmp_path)

    assert manager._data_page_ready("微信公众号", "首页\n总用户数\n60\n昨日阅读(人)\n8") is True
    assert manager._data_page_ready("抖音", "首页\n粉丝\n100\n数据中心\n播放量\n0") is True
    assert manager._data_page_ready("微信公众号", "") is False


def test_enable_session_restore_preserves_existing_chrome_preferences(tmp_path):
    profile_path = tmp_path / "profile"
    preferences_path = profile_path / "Default" / "Preferences"
    preferences_path.parent.mkdir(parents=True)
    preferences_path.write_text(
        json.dumps(
            {
                "browser": {"show_home_button": True},
                "profile": {"content_settings": {"exceptions": {}}},
            }
        ),
        encoding="utf-8",
    )

    BrowserAuthManager._enable_session_restore(profile_path)

    assert json.loads(preferences_path.read_text(encoding="utf-8")) == {
        "browser": {"show_home_button": True},
        "profile": {
            "content_settings": {"exceptions": {}},
            "session": {"restore_on_startup": 1},
        },
    }


def test_enable_session_restore_creates_valid_preferences_for_new_profile(tmp_path):
    profile_path = tmp_path / "profile"

    BrowserAuthManager._enable_session_restore(profile_path)

    assert json.loads((profile_path / "Default" / "Preferences").read_text(encoding="utf-8")) == {
        "profile": {"session": {"restore_on_startup": 1}}
    }


def test_login_launch_restores_the_last_browser_session(tmp_path, monkeypatch):
    chrome_path = tmp_path / "chrome"
    chrome_path.touch()
    calls = []

    class Process:
        def poll(self):
            return 0

    class Thread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            return None

    def popen(command, **kwargs):
        calls.append(command)
        return Process()

    monkeypatch.setattr(browser_auth.subprocess, "Popen", popen)
    monkeypatch.setattr(browser_auth.threading, "Thread", Thread)
    monkeypatch.setattr(manager := BrowserAuthManager(tmp_path / "profiles", chrome_path=chrome_path), "_free_port", lambda: 9222)

    manager.start("微信公众号", "", lambda account: account)

    assert "--restore-last-session" in calls[0]
    preferences_paths = list((tmp_path / "profiles").glob("**/Default/Preferences"))
    assert len(preferences_paths) == 1
    assert json.loads(preferences_paths[0].read_text(encoding="utf-8"))["profile"]["session"] == {
        "restore_on_startup": 1
    }


def test_sync_launch_restores_the_last_browser_session(tmp_path, monkeypatch):
    chrome_path = tmp_path / "chrome"
    chrome_path.touch()
    profile_path = tmp_path / "profile"
    profile_path.mkdir()
    calls = []

    class Process:
        def poll(self):
            return 1

    def popen(command, **kwargs):
        calls.append(command)
        return Process()

    monkeypatch.setattr(browser_auth.subprocess, "Popen", popen)
    manager = BrowserAuthManager(tmp_path / "profiles", chrome_path=chrome_path)
    monkeypatch.setattr(manager, "_free_port", lambda: 9222)

    with pytest.raises(RuntimeError, match="启动失败"):
        manager.sync({"platform": "微信公众号", "profile_path": str(profile_path)})

    assert "--restore-last-session" in calls[0]
    assert json.loads((profile_path / "Default" / "Preferences").read_text(encoding="utf-8"))["profile"]["session"] == {
        "restore_on_startup": 1
    }


def test_close_browser_terminates_and_kills_when_process_does_not_exit():
    class Process:
        def __init__(self):
            self.terminated = False
            self.killed = False
            self.wait_timeouts = []

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

        def wait(self, timeout):
            self.wait_timeouts.append(timeout)
            if len(self.wait_timeouts) == 1:
                raise subprocess.TimeoutExpired("chrome", timeout)

    process = Process()

    BrowserAuthManager._close_browser(process, None)

    assert process.terminated is True
    assert process.killed is True
    assert process.wait_timeouts == [3, 3]
