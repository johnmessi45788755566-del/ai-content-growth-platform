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


def test_xiaohongshu_login_page_cookie_is_not_authenticated(tmp_path):
    manager = BrowserAuthManager(tmp_path)
    manager._cdp_call = lambda page, method, params=None: {
        "cookies": [{"name": "access-token-creator.xiaohongshu.com", "value": "token"}]
    }

    assert manager._is_authenticated(
        "小红书",
        {
            "url": (
                "https://creator.xiaohongshu.com/login?source=&redirectReason=401"
                "&lastUrl=%252Fpublish%252Fpublish%253Ftarget%253Dimage"
            )
        },
    ) is False


def test_xiaohongshu_publish_access_rejects_login_redirect(tmp_path, monkeypatch):
    manager = BrowserAuthManager(tmp_path)
    page = {
        "url": "https://creator.xiaohongshu.com/new/home",
        "webSocketDebuggerUrl": "ws://page",
    }
    calls = []

    def fake_cdp_call(current_page, method, params=None):
        calls.append((method, params))
        if method == "Page.navigate":
            page["url"] = "https://creator.xiaohongshu.com/login?redirectReason=401"
            return {}
        return {
            "cookies": [
                {"name": "access-token-creator.xiaohongshu.com", "value": "token"}
            ]
        }

    monkeypatch.setattr(browser_auth.time, "sleep", lambda seconds: None)
    manager._platform_page = lambda port, platform: page
    manager._cdp_call = fake_cdp_call

    with pytest.raises(RuntimeError, match="发布页登录态未通过"):
        manager._verify_xiaohongshu_publish_access(9222, page)

    assert calls[0][0] == "Page.navigate"


def test_xiaohongshu_publish_access_accepts_publish_page(tmp_path, monkeypatch):
    manager = BrowserAuthManager(tmp_path)
    page = {
        "url": "https://creator.xiaohongshu.com/new/home",
        "webSocketDebuggerUrl": "ws://page",
    }

    def fake_cdp_call(current_page, method, params=None):
        if method == "Page.navigate":
            page["url"] = "https://creator.xiaohongshu.com/publish/publish?from=homepage&target=image"
            return {}
        return {
            "cookies": [
                {"name": "access-token-creator.xiaohongshu.com", "value": "token"}
            ]
        }

    monkeypatch.setattr(browser_auth.time, "sleep", lambda seconds: None)
    manager._platform_page = lambda port, platform: page
    manager._cdp_call = fake_cdp_call

    verified = manager._verify_xiaohongshu_publish_access(9222, page)

    assert verified["url"].startswith("https://creator.xiaohongshu.com/publish/publish")


def test_xiaohongshu_identity_detection_returns_cookie_id_without_waiting_for_name(tmp_path, monkeypatch):
    manager = BrowserAuthManager(tmp_path)
    sleep_calls = []
    times = iter([0, 0, 13])
    monkeypatch.setattr(browser_auth.time, "time", lambda: next(times))
    monkeypatch.setattr(browser_auth.time, "sleep", lambda seconds: sleep_calls.append(seconds))
    manager._platform_page = lambda port, platform: {"webSocketDebuggerUrl": "ws://page"}
    manager._cdp_call = lambda page, method, params=None: {
        "cookies": [{"name": "x-user-id-creator.xiaohongshu.com", "value": "xhs-user-id"}]
    }
    manager._detect_account_name = lambda platform, page: ""

    identity = manager._detect_account_identity("小红书", 9222)

    assert identity == {"account_name": "", "external_id": "xhs-user-id"}
    assert sleep_calls == []


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


def test_xiaohongshu_login_uses_patchright_context_instead_of_system_chrome(tmp_path, monkeypatch):
    class Page:
        def __init__(self):
            self.url = ""

        def goto(self, url, **kwargs):
            self.url = url

        def wait_for_timeout(self, timeout):
            return None

        def evaluate(self, expression):
            return "找房子的派大星" if "document.querySelector" in expression else self.url

    class Context:
        def __init__(self):
            self.page = Page()
            self.closed = False

        def new_page(self):
            return self.page

        def cookies(self):
            return [
                {"name": "access-token-creator.xiaohongshu.com", "value": "token"},
                {"name": "x-user-id-creator.xiaohongshu.com", "value": "xhs-user-id"},
            ]

        def close(self):
            self.closed = True

    class Chromium:
        def __init__(self, context):
            self.context = context
            self.launch_calls = []

        def launch_persistent_context(self, **kwargs):
            self.launch_calls.append(kwargs)
            return self.context

    class Playwright:
        def __init__(self):
            self.context = Context()
            self.chromium = Chromium(self.context)
            self.stopped = False

        def stop(self):
            self.stopped = True

    class Thread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            self.kwargs["target"](*self.kwargs["args"])

    playwright = Playwright()
    monkeypatch.setattr(browser_auth.threading, "Thread", Thread)
    manager = BrowserAuthManager(
        tmp_path / "profiles",
        chrome_path=tmp_path / "missing-chrome",
        xiaohongshu_playwright_factory=lambda: playwright,
    )

    session = manager.start("小红书", "找房子的派大星", lambda account: {"id": 7, **account})

    assert session["status"] == "success"
    assert playwright.chromium.launch_calls
    assert playwright.context.page.url.startswith("https://creator.xiaohongshu.com/publish/publish")
    assert playwright.context.closed is True
    assert playwright.stopped is True


def test_xiaohongshu_login_retries_transient_home_goto_failure(tmp_path, monkeypatch):
    class Page:
        def __init__(self):
            self.url = ""
            self.goto_calls = []

        def goto(self, url, **kwargs):
            self.goto_calls.append((url, kwargs))
            if url == "https://creator.xiaohongshu.com/new/home" and len(self.goto_calls) == 1:
                raise Exception("Page.goto: net::ERR_CONNECTION_CLOSED")
            self.url = url

        def wait_for_timeout(self, timeout):
            return None

        def evaluate(self, expression):
            return "找房子的派大星" if "document.querySelector" in expression else self.url

    class Context:
        def __init__(self):
            self.page = Page()
            self.closed = False

        def new_page(self):
            return self.page

        def cookies(self):
            return [
                {"name": "access-token-creator.xiaohongshu.com", "value": "token"},
                {"name": "x-user-id-creator.xiaohongshu.com", "value": "xhs-user-id"},
            ]

        def close(self):
            self.closed = True

    class Chromium:
        def __init__(self, context):
            self.context = context

        def launch_persistent_context(self, **kwargs):
            return self.context

    class Playwright:
        def __init__(self):
            self.context = Context()
            self.chromium = Chromium(self.context)
            self.stopped = False

        def stop(self):
            self.stopped = True

    class Thread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            self.kwargs["target"](*self.kwargs["args"])

    playwright = Playwright()
    monkeypatch.setattr(browser_auth.threading, "Thread", Thread)
    monkeypatch.setattr(browser_auth.time, "sleep", lambda seconds: None)
    manager = BrowserAuthManager(
        tmp_path / "profiles",
        chrome_path=tmp_path / "missing-chrome",
        xiaohongshu_playwright_factory=lambda: playwright,
    )

    session = manager.start("小红书", "找房子的派大星", lambda account: {"id": 7, **account})

    assert session["status"] == "success"
    home_gotos = [
        call for call in playwright.context.page.goto_calls
        if call[0] == "https://creator.xiaohongshu.com/new/home"
    ]
    assert len(home_gotos) == 2
    assert playwright.context.closed is True
    assert playwright.stopped is True


def test_xiaohongshu_login_defers_patchright_start_to_worker_thread(tmp_path, monkeypatch):
    class Thread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            return None

    calls = []
    monkeypatch.setattr(browser_auth.threading, "Thread", Thread)
    manager = BrowserAuthManager(
        tmp_path / "profiles",
        chrome_path=tmp_path / "missing-chrome",
        xiaohongshu_playwright_factory=lambda: calls.append("started"),
    )

    session = manager.start("小红书", "找房子的派大星", lambda account: account)

    assert session["status"] == "waiting"
    assert calls == []


def test_xiaohongshu_login_cancel_is_deferred_to_patchright_worker(tmp_path, monkeypatch):
    class Page:
        def goto(self, url, **kwargs):
            return None

    class Context:
        def __init__(self):
            self.closed = False

        def new_page(self):
            return Page()

        def close(self):
            self.closed = True

    class Chromium:
        def __init__(self, context):
            self.context = context

        def launch_persistent_context(self, **kwargs):
            return self.context

    class Playwright:
        def __init__(self):
            self.context = Context()
            self.chromium = Chromium(self.context)
            self.stopped = False

        def stop(self):
            self.stopped = True

    class Thread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            return None

    playwright = Playwright()
    monkeypatch.setattr(browser_auth.threading, "Thread", Thread)
    manager = BrowserAuthManager(
        tmp_path / "profiles",
        chrome_path=tmp_path / "missing-chrome",
        xiaohongshu_playwright_factory=lambda: playwright,
    )
    session = manager.start("小红书", "", lambda account: account)

    cancelled = manager.cancel(session["session_id"])

    assert cancelled["status"] == "cancelled"
    assert playwright.context.closed is False
    assert playwright.stopped is False


def test_xiaohongshu_login_monitor_marks_current_window_preflight_verified(tmp_path, monkeypatch):
    class Page:
        def __init__(self):
            self.url = "https://creator.xiaohongshu.com/new/home"

        def goto(self, url, **kwargs):
            self.url = url

        def wait_for_timeout(self, timeout):
            return None

        def evaluate(self, expression):
            return "找房子的派大星" if "document.querySelector" in expression else self.url

    class Context:
        def __init__(self):
            self.closed = False

        def cookies(self):
            return [
                {"name": "access-token-creator.xiaohongshu.com", "value": "token"},
                {"name": "x-user-id-creator.xiaohongshu.com", "value": "xhs-user-id"},
            ]

        def close(self):
            self.closed = True

    class Playwright:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    captured = {}
    manager = BrowserAuthManager(tmp_path / "profiles", timeout_seconds=1)
    context = Context()
    playwright = Playwright()
    session_id = "xhs-session"
    with manager._lock:
        manager.sessions[session_id] = {
            "session_id": session_id,
            "platform": "小红书",
            "status": "waiting",
            "message": "等待扫码",
            "login_url": "https://creator.xiaohongshu.com/new/home",
            "profile_path": str(tmp_path / "profiles" / "小红书" / "profile"),
            "account": None,
            "error": "",
            "process": None,
            "port": None,
            "context": context,
            "playwright": playwright,
            "page": Page(),
        }

    def on_success(payload):
        captured.update(payload)
        return {"id": 7, "platform": "小红书", "name": payload["account_name"]}

    manager._monitor_xiaohongshu_patchright(session_id, "", on_success)

    assert captured["publish_preflight_verified"] is True
    assert captured["login_url"].startswith("https://creator.xiaohongshu.com/publish/publish")
    assert manager.get_status(session_id)["status"] == "success"
    assert context.closed is True
    assert playwright.stopped is True


def test_xiaohongshu_login_monitor_exports_storage_state_for_reusable_auth(tmp_path, monkeypatch):
    class Page:
        def __init__(self):
            self.url = "https://creator.xiaohongshu.com/new/home"

        def goto(self, url, **kwargs):
            self.url = url

        def wait_for_timeout(self, timeout):
            return None

        def evaluate(self, expression):
            return "找房子的派大星" if "document.querySelector" in expression else self.url

    class Context:
        def __init__(self):
            self.closed = False
            self.storage_state_paths = []

        def cookies(self):
            return [
                {"name": "access-token-creator.xiaohongshu.com", "value": "token"},
                {"name": "x-user-id-creator.xiaohongshu.com", "value": "xhs-user-id"},
            ]

        def storage_state(self, path):
            self.storage_state_paths.append(path)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"cookies": self.cookies(), "origins": []}, handle)

        def close(self):
            self.closed = True

    class Playwright:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    captured = {}
    manager = BrowserAuthManager(tmp_path / "profiles", timeout_seconds=1)
    context = Context()
    playwright = Playwright()
    session_id = "xhs-session"
    with manager._lock:
        manager.sessions[session_id] = {
            "session_id": session_id,
            "platform": "小红书",
            "status": "waiting",
            "message": "等待扫码",
            "login_url": "https://creator.xiaohongshu.com/new/home",
            "profile_path": str(tmp_path / "profiles" / "小红书" / "profile"),
            "account": None,
            "error": "",
            "process": None,
            "port": None,
            "context": context,
            "playwright": playwright,
            "page": Page(),
        }

    def on_success(payload):
        captured.update(payload)
        return {"id": 7, "platform": "小红书", "name": payload["account_name"]}

    manager._monitor_xiaohongshu_patchright(session_id, "", on_success)

    assert captured["storage_state_path"].endswith(".json")
    assert context.storage_state_paths == [captured["storage_state_path"]]
    assert (tmp_path / "profiles").joinpath("小红书").exists()


def test_xiaohongshu_login_monitor_rejects_storage_state_that_cannot_reopen_publish_page(tmp_path):
    class Page:
        def __init__(self, url="https://creator.xiaohongshu.com/new/home"):
            self.url = url

        def goto(self, url, **kwargs):
            self.url = url

        def wait_for_timeout(self, timeout):
            return None

        def evaluate(self, expression):
            return "找房子的派大星" if "document.querySelector" in expression else self.url

    class LoginContext:
        def __init__(self):
            self.closed = False

        def cookies(self):
            return [
                {"name": "access-token-creator.xiaohongshu.com", "value": "token"},
                {"name": "x-user-id-creator.xiaohongshu.com", "value": "xhs-user-id"},
            ]

        def storage_state(self, path):
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"cookies": self.cookies(), "origins": []}, handle)

        def close(self):
            self.closed = True

    class ReopenContext:
        def __init__(self):
            self.page = Page("https://creator.xiaohongshu.com/login?redirectReason=401")
            self.page.goto = lambda url, **kwargs: setattr(
                self.page,
                "url",
                "https://creator.xiaohongshu.com/login?redirectReason=401",
            )
            self.page.evaluate = lambda expression: self.page.url
            self.closed = False

        def new_page(self):
            return self.page

        def cookies(self):
            return []

        def close(self):
            self.closed = True

    class Browser:
        def __init__(self):
            self.reopen_context = ReopenContext()
            self.closed = False

        def new_context(self, **kwargs):
            return self.reopen_context

        def close(self):
            self.closed = True

    class Chromium:
        def __init__(self):
            self.browser = Browser()

        def launch(self, **kwargs):
            return self.browser

    class Playwright:
        def __init__(self):
            self.chromium = Chromium()
            self.stopped = False

        def stop(self):
            self.stopped = True

    captured = {}
    manager = BrowserAuthManager(tmp_path / "profiles", timeout_seconds=1)
    context = LoginContext()
    playwright = Playwright()
    session_id = "xhs-session"
    with manager._lock:
        manager.sessions[session_id] = {
            "session_id": session_id,
            "platform": "小红书",
            "status": "waiting",
            "message": "等待扫码",
            "login_url": "https://creator.xiaohongshu.com/new/home",
            "profile_path": str(tmp_path / "profiles" / "小红书" / "profile"),
            "account": None,
            "error": "",
            "process": None,
            "port": None,
            "context": context,
            "playwright": playwright,
            "page": Page(),
        }

    manager._monitor_xiaohongshu_patchright(
        session_id,
        "",
        lambda payload: captured.update(payload) or payload,
    )

    status = manager.get_status(session_id)
    assert status["status"] == "failed"
    assert "发布页登录态未通过" in status["error"]
    assert captured == {}
    assert playwright.chromium.browser.reopen_context.closed is True
    assert playwright.chromium.browser.closed is True


def test_xiaohongshu_login_monitor_accepts_login_page_after_cookie_lands(
    tmp_path, monkeypatch
):
    class Page:
        def __init__(self):
            self.url = "https://creator.xiaohongshu.com/login"

        def goto(self, url, **kwargs):
            self.url = url

        def wait_for_timeout(self, timeout):
            return None

        def evaluate(self, expression):
            return "找房子的派大星" if "document.querySelector" in expression else self.url

    class Context:
        def __init__(self):
            self.closed = False

        def cookies(self):
            return [
                {"name": "access-token-creator.xiaohongshu.com", "value": "token"},
                {"name": "x-user-id-creator.xiaohongshu.com", "value": "xhs-user-id"},
            ]

        def close(self):
            self.closed = True

    class Playwright:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    captured = {}
    manager = BrowserAuthManager(tmp_path / "profiles", timeout_seconds=1)
    context = Context()
    playwright = Playwright()
    session_id = "xhs-session"
    with manager._lock:
        manager.sessions[session_id] = {
            "session_id": session_id,
            "platform": "小红书",
            "status": "waiting",
            "message": "等待扫码",
            "login_url": "https://creator.xiaohongshu.com/new/home",
            "profile_path": str(tmp_path / "profiles" / "小红书" / "profile"),
            "account": None,
            "error": "",
            "process": None,
            "port": None,
            "context": context,
            "playwright": playwright,
            "page": Page(),
        }

    def on_success(payload):
        captured.update(payload)
        return {"id": 7, "platform": "小红书", "name": payload["account_name"]}

    manager._monitor_xiaohongshu_patchright(session_id, "", on_success)

    assert captured["publish_preflight_verified"] is True
    assert captured["login_url"].startswith("https://creator.xiaohongshu.com/publish/publish")
    assert manager.get_status(session_id)["status"] == "success"
    assert context.closed is True
    assert playwright.stopped is True


def test_xiaohongshu_login_monitor_reports_preflight_errors_without_waiting_for_timeout(
    tmp_path, monkeypatch
):
    class Page:
        url = "https://creator.xiaohongshu.com/new/home"

        def goto(self, url, **kwargs):
            raise Exception("goto timed out")

        def evaluate(self, expression):
            return self.url

    class Context:
        def __init__(self):
            self.closed = False

        def cookies(self):
            return [
                {"name": "access-token-creator.xiaohongshu.com", "value": "token"},
            ]

        def close(self):
            self.closed = True

    class Playwright:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    times = iter([0, 0, 0, 13])
    monkeypatch.setattr(browser_auth.time, "time", lambda: next(times))
    monkeypatch.setattr(browser_auth.time, "sleep", lambda seconds: None)
    manager = BrowserAuthManager(tmp_path / "profiles", timeout_seconds=3)
    context = Context()
    playwright = Playwright()
    session_id = "xhs-session"
    profile_path = tmp_path / "profiles" / "小红书" / "profile"
    with manager._lock:
        manager.sessions[session_id] = {
            "session_id": session_id,
            "platform": "小红书",
            "status": "waiting",
            "message": "等待扫码",
            "login_url": "https://creator.xiaohongshu.com/new/home",
            "profile_path": str(profile_path),
            "account": None,
            "error": "",
            "process": None,
            "port": None,
            "context": context,
            "playwright": playwright,
            "page": Page(),
        }

    manager._monitor_xiaohongshu_patchright(session_id, "", lambda payload: payload)

    status = manager.get_status(session_id)
    assert status["status"] == "failed"
    assert "小红书发布页预检失败" in status["error"]
    assert context.closed is True
    assert playwright.stopped is True


def test_xiaohongshu_patchright_publish_preflight_accepts_publish_url_after_goto_timeout(
    tmp_path, monkeypatch
):
    class Page:
        def __init__(self):
            self.url = "https://creator.xiaohongshu.com/new/home"

        def goto(self, url, **kwargs):
            self.url = url
            raise Exception("navigation timed out")

        def wait_for_timeout(self, timeout):
            return None

        def evaluate(self, expression):
            return self.url

    class Context:
        def cookies(self):
            return [
                {"name": "access-token-creator.xiaohongshu.com", "value": "token"},
            ]

    manager = BrowserAuthManager(tmp_path / "profiles")
    page = Page()

    verified = manager._verify_xiaohongshu_patchright_publish_access(Context(), page)

    assert verified.startswith("https://creator.xiaohongshu.com/publish/publish")


def test_xiaohongshu_patchright_auth_accepts_visible_creator_identity_without_cookie_marker(
    tmp_path,
):
    class Page:
        url = "https://creator.xiaohongshu.com/new/home"

        def evaluate(self, expression):
            if "document.querySelector" in expression:
                return "找房子的派大星"
            return self.url

    class Context:
        def cookies(self):
            return []

    manager = BrowserAuthManager(tmp_path / "profiles")

    assert manager._patchright_xiaohongshu_authenticated(Context(), Page()) is True


def test_xiaohongshu_patchright_auth_accepts_cookie_marker_with_empty_value(
    tmp_path,
):
    class Page:
        url = "https://creator.xiaohongshu.com/new/home"

        def evaluate(self, expression):
            if "document.querySelector" in expression:
                return ""
            return self.url

    class Context:
        def cookies(self):
            return [
                {"name": "access-token-creator.xiaohongshu.com", "value": ""},
            ]

    manager = BrowserAuthManager(tmp_path / "profiles")

    assert manager._patchright_xiaohongshu_authenticated(Context(), Page()) is True


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


def test_public_session_redacts_sensitive_account_fields_and_sanitizes_login_url():
    public_session = BrowserAuthManager._public_session(
        {
            "session_id": "session-1",
            "platform": "微信公众号",
            "status": "success",
            "message": "登录成功",
            "login_url": "https://mp.weixin.qq.com/cgi-bin/home?token=secret#fragment",
            "account": {
                "id": 7,
                "platform": "微信公众号",
                "name": "测试账号",
                "account_name": "测试账号",
                "auth_status": "已授权",
                "monitor_status": "监测中",
                "sync_status": "已同步",
                "last_sync_at": "2026-07-14 09:00:00",
                "profile_path": "/private/profile",
                "external_id": "external-credential",
                "cookies": [{"name": "session", "value": "secret"}],
                "login_url": "https://mp.weixin.qq.com/cgi-bin/home?token=secret",
            },
            "error": "",
        }
    )

    assert public_session == {
        "session_id": "session-1",
        "platform": "微信公众号",
        "status": "success",
        "message": "登录成功",
        "login_url": "https://mp.weixin.qq.com/cgi-bin/home",
        "account": {
            "id": 7,
            "platform": "微信公众号",
            "name": "测试账号",
            "account_name": "测试账号",
            "auth_status": "已授权",
            "monitor_status": "监测中",
            "sync_status": "已同步",
            "last_sync_at": "2026-07-14 09:00:00",
        },
        "error": "",
    }


def test_enable_session_restore_cleans_up_temporary_file_when_replace_fails(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile"
    preferences_path = profile_path / "Default" / "Preferences"
    preferences_path.parent.mkdir(parents=True)
    preferences_path.write_text('{"browser":{"show_home_button":true}}', encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(browser_auth.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        BrowserAuthManager._enable_session_restore(profile_path)

    assert preferences_path.read_text(encoding="utf-8") == '{"browser":{"show_home_button":true}}'
    assert list(preferences_path.parent.glob(".Preferences-*")) == []


def test_close_browser_waits_for_normal_browser_close(monkeypatch):
    class Response:
        def read(self):
            return b'{"webSocketDebuggerUrl":"ws://browser"}'

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    class Connection:
        def __init__(self):
            self.messages = []
            self.closed = False

        def send(self, message):
            self.messages.append(json.loads(message))

        def close(self):
            self.closed = True

    class Process:
        def __init__(self):
            self.wait_timeouts = []
            self.terminated = False
            self.killed = False

        def poll(self):
            return None

        def wait(self, timeout):
            self.wait_timeouts.append(timeout)

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    connection = Connection()
    process = Process()
    monkeypatch.setattr(browser_auth.urllib.request, "urlopen", lambda request, timeout: Response())
    monkeypatch.setattr(browser_auth.websocket, "create_connection", lambda url, timeout: connection)

    BrowserAuthManager._close_browser(process, 9222)

    assert connection.messages == [{"id": 1, "method": "Browser.close"}]
    assert connection.closed is True
    assert process.wait_timeouts == [3]
    assert process.terminated is False
    assert process.killed is False
