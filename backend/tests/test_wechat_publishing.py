import json
import subprocess
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.publishing import browser_gateway as browser_gateway_module
from app.publishing.browser_gateway import (
    COVER_FILE_SELECTORS,
    INLINE_FILE_SELECTORS,
    SAVE_DRAFT_TEXTS,
    STATE_TRAVERSAL_MAX_DEPTH,
    STATE_TRAVERSAL_MAX_ENTRIES,
    BrowserSession,
    ChromeCDPBrowserGateway,
)
from app.publishing.models import PublishResult
from app.publishing.wechat import WeChatBrowserPublisher, WeChatPublishingError


class FakeWeChatGateway:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes) if isinstance(outcomes, list) else [outcomes]
        self.actions = []
        self.closed_sessions = []
        self.filled_payload = None
        self.retained = {}
        self.cancelled = []
        self.snapshots = []

    def open_profile(self, profile_path, url):
        self.actions.append(("open_profile", profile_path, url))
        return {"session_id": "session-1", "url": url}

    def resume_profile(self, profile_path, resume_handle, account_key):
        self.actions.append(("resume_profile", profile_path, resume_handle, account_key))
        pending = self.retained.pop(resume_handle)
        if pending["account_key"] != account_key:
            raise WeChatPublishingError("恢复句柄与账号不匹配")
        return pending["session"], pending["title"], pending["baseline"]

    def require_authenticated(self, session):
        self.actions.append("require_authenticated")

    def require_account_identity(self, session, account):
        self.actions.append(("require_account_identity", account.get("external_id"), account.get("name")))

    def open_article_editor(self, session):
        self.actions.append("open_article_editor")

    def fill_article(self, session, payload):
        self.actions.append("fill_article")
        self.filled_payload = payload

    def fill_article_content(self, session, payload):
        self.actions.append("fill_article_content")
        self.filled_payload = payload

    def upload_article_assets(self, session, payload):
        self.actions.append("upload_article_assets")
        self.filled_payload = payload

    def capture_baseline(self, session, title, action):
        self.actions.append("capture_baseline")
        return {
            "baseline_id": "baseline-1",
            "baseline_complete": True,
            "baseline_incomplete_reasons": [],
            "receipt_ids": ["old-receipt"],
            "title": title,
            "action": action,
        }

    def click_save_draft(self, session):
        self.actions.append("click_save_draft")

    def click_publish(self, session):
        self.actions.append("click_publish")

    def verify_draft(self, session, title, baseline):
        self.actions.append("verify_draft")
        return self.outcomes.pop(0)

    def verify_publish_or_confirmation(self, session, title, baseline):
        self.actions.append("verify_publish_or_confirmation")
        return self.outcomes.pop(0)

    def retain_confirmation(self, session, account_key, title, baseline):
        handle = "opaque-resume-handle"
        self.actions.append("retain_confirmation")
        self.retained[handle] = {
            "session": session,
            "account_key": account_key,
            "title": title,
            "baseline": baseline,
        }
        return handle

    def cancel_pending(self, resume_handle, account_key):
        self.cancelled.append((resume_handle, account_key))
        self.retained.pop(resume_handle, None)

    def close(self, session):
        self.closed_sessions.append(session)

    def capture_debug_state(self, session, label, include_screenshot=True):
        self.actions.append(("capture_debug_state", label, include_screenshot))
        self.snapshots.append((label, include_screenshot))


def execute_baseline_state_collector(state_setup: str):
    collector = ChromeCDPBrowserGateway._baseline_state_collector_source()
    script = f"""
    const collectBaselineState = {collector};
    {state_setup}
    const result = collectBaselineState(
      [['test_state', state]],
      (value) => String(value || '').normalize('NFKC').replace(/\\s+/gu, ' ').trim(),
      {STATE_TRAVERSAL_MAX_DEPTH},
      {STATE_TRAVERSAL_MAX_ENTRIES}
    );
    process.stdout.write(JSON.stringify(result));
    """
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


class CollectorBackedFakeGateway(FakeWeChatGateway):
    def __init__(self, outcomes, state_setup):
        super().__init__(outcomes)
        self.state_setup = state_setup
        self.collector_result = None

    def capture_baseline(self, session, title, action):
        self.actions.append("capture_baseline")
        self.collector_result = execute_baseline_state_collector(self.state_setup)
        return {
            "baseline_id": "baseline-1",
            **self.collector_result,
            "receipt_ids": [
                receipt["id"] for receipt in self.collector_result["receipts"]
            ],
            "title": title,
            "action": action,
        }


def article_payload(tmp_path: Path):
    cover = tmp_path / "cover.jpg"
    inline = tmp_path / "inline.png"
    cover.write_bytes(b"cover")
    inline.write_bytes(b"inline")
    return {
        "title": "浏览器发布测试",
        "author": "理想说AI",
        "digest": "摘要",
        "cover_path": str(cover),
        "blocks": [
            {"type": "intro", "content": "引言", "image_path": ""},
            {"type": "body", "content": "正文", "image_path": str(inline)},
        ],
    }


def article_payload_without_cover(tmp_path: Path):
    payload = article_payload(tmp_path)
    payload["cover_path"] = ""
    return payload


def browser_account(tmp_path: Path):
    profile_path = tmp_path / "profile"
    profile_path.mkdir()
    return {
        "id": 4,
        "platform": "微信公众号",
        "name": "理想说AI",
        "external_id": "wechat-bizuin-4",
        "auth_status": "已授权",
        "profile_path": str(profile_path),
    }


def draft_outcome():
    return {
        "status": "platform_draft",
        "draft_id": "draft-42",
        "platform_url": "https://mp.weixin.qq.com/cgi-bin/appmsg?action=list&token=secret-token",
        "baseline_id": "baseline-1",
        "receipt": {"id": "draft-42", "title": "浏览器发布测试", "source": "dom"},
        "post_action_signal": {"kind": "draft_saved", "observed_after_action": True},
    }


def published_outcome():
    return {
        "status": "published",
        "publish_id": "publish-88",
        "platform_url": "https://mp.weixin.qq.com/cgi-bin/masssendpage?token=secret-token",
        "baseline_id": "baseline-1",
        "receipt": {"id": "publish-88", "title": "浏览器发布测试", "source": "dom"},
        "post_action_signal": {"kind": "publish_success", "observed_after_action": True},
    }


def test_wechat_browser_publisher_creates_verified_platform_draft(tmp_path: Path):
    gateway = FakeWeChatGateway(draft_outcome())
    result = WeChatBrowserPublisher(gateway=gateway).publish(
        article_payload(tmp_path), browser_account(tmp_path), action="draft"
    )

    assert isinstance(result, PublishResult)
    assert result.status == "platform_draft"
    assert result.platform_draft_id == "draft-42"
    assert result.platform_url == "https://mp.weixin.qq.com/cgi-bin/appmsg"
    assert result.response == {
        "source": "wechat_platform",
        "event": "draft_created",
        "receipt_id": "draft-42",
        "title": "浏览器发布测试",
        "corroboration": "draft_saved",
    }
    assert gateway.actions.index("capture_baseline") < gateway.actions.index("click_save_draft")
    assert gateway.actions[-1] == "verify_draft"
    assert len(gateway.closed_sessions) == 1


def test_wechat_browser_publisher_upload_payload_reaches_gateway(tmp_path: Path):
    payload = article_payload(tmp_path)
    gateway = FakeWeChatGateway(draft_outcome())

    WeChatBrowserPublisher(gateway=gateway).publish(
        payload, browser_account(tmp_path), action="draft"
    )

    assert gateway.filled_payload["cover_path"] == payload["cover_path"]
    assert gateway.filled_payload["blocks"][1]["image_path"] == payload["blocks"][1]["image_path"]


def test_wechat_browser_publisher_reports_publish_progress_at_real_browser_steps(tmp_path: Path):
    gateway = FakeWeChatGateway(published_outcome())
    statuses = []

    result = WeChatBrowserPublisher(gateway=gateway).publish_with_progress(
        article_payload(tmp_path),
        browser_account(tmp_path),
        action="publish",
        report_progress=statuses.append,
    )

    assert statuses == ["filling_content", "uploading_assets", "publishing"]
    assert gateway.actions.index("fill_article_content") < gateway.actions.index(
        "upload_article_assets"
    )
    assert gateway.actions.index("upload_article_assets") < gateway.actions.index(
        "capture_baseline"
    )
    assert gateway.actions.index("capture_baseline") < gateway.actions.index(
        "click_publish"
    )
    assert result.status == "published"


def test_wechat_browser_publisher_skips_cover_asset_upload_for_platform_draft(tmp_path: Path):
    gateway = FakeWeChatGateway(draft_outcome())
    statuses = []

    result = WeChatBrowserPublisher(gateway=gateway).publish_with_progress(
        article_payload(tmp_path),
        browser_account(tmp_path),
        action="draft",
        report_progress=statuses.append,
    )

    assert result.status == "platform_draft"
    assert statuses == ["filling_content", "publishing"]
    assert "upload_article_assets" not in gateway.actions
    assert gateway.actions.index("fill_article_content") < gateway.actions.index(
        "capture_baseline"
    )


def test_wechat_browser_publisher_verifies_selected_account_before_platform_changes(tmp_path: Path):
    class WrongAccountGateway(FakeWeChatGateway):
        def require_account_identity(self, session, account):
            super().require_account_identity(session, account)
            raise WeChatPublishingError("当前登录公众号与所选账号不一致")

    gateway = WrongAccountGateway(draft_outcome())

    with pytest.raises(WeChatPublishingError, match="当前登录公众号与所选账号不一致"):
        WeChatBrowserPublisher(gateway=gateway).publish(
            article_payload(tmp_path), browser_account(tmp_path), action="draft"
        )

    assert ("require_account_identity", "wechat-bizuin-4", "理想说AI") in gateway.actions
    assert "open_article_editor" not in gateway.actions
    assert "click_save_draft" not in gateway.actions
    assert len(gateway.closed_sessions) == 1


def test_wechat_browser_publisher_returns_verified_publish_receipt(tmp_path: Path):
    gateway = FakeWeChatGateway(published_outcome())
    result = WeChatBrowserPublisher(gateway=gateway).publish(
        article_payload(tmp_path), browser_account(tmp_path), action="publish"
    )

    assert result.status == "published"
    assert result.platform_publish_id == "publish-88"
    assert result.platform_url == "https://mp.weixin.qq.com/cgi-bin/masssendpage"
    assert result.response["receipt_id"] == "publish-88"
    assert "token" not in str(result.response).lower()
    assert gateway.actions.index("capture_baseline") < gateway.actions.index("click_publish")
    assert gateway.actions[-1] == "verify_publish_or_confirmation"
    assert len(gateway.closed_sessions) == 1


def test_wechat_browser_publisher_captures_failure_snapshot_before_closing_session(
    tmp_path: Path,
):
    class FailingPublishGateway(FakeWeChatGateway):
        def click_publish(self, session):
            super().click_publish(session)
            raise WeChatPublishingError("微信发布按钮失败")

    gateway = FailingPublishGateway(published_outcome())

    with pytest.raises(WeChatPublishingError, match="微信发布按钮失败"):
        WeChatBrowserPublisher(gateway=gateway).publish(
            article_payload(tmp_path), browser_account(tmp_path), action="publish"
        )

    assert ("capture_debug_state", "wechat_publish_error", True) in gateway.actions
    assert gateway.actions.index(
        ("capture_debug_state", "wechat_publish_error", True)
    ) < len(gateway.actions)
    assert len(gateway.closed_sessions) == 1


def test_wechat_browser_publish_requires_cover_before_opening_platform(tmp_path: Path):
    gateway = FakeWeChatGateway(published_outcome())

    with pytest.raises(WeChatPublishingError, match="一键发布必须上传封面图"):
        WeChatBrowserPublisher(gateway=gateway).publish(
            article_payload_without_cover(tmp_path),
            browser_account(tmp_path),
            action="publish",
        )

    assert gateway.actions == []
    assert gateway.closed_sessions == []


def test_wechat_browser_draft_allows_article_without_cover(tmp_path: Path):
    gateway = FakeWeChatGateway(draft_outcome())

    result = WeChatBrowserPublisher(gateway=gateway).publish(
        article_payload_without_cover(tmp_path),
        browser_account(tmp_path),
        action="draft",
    )

    assert result.status == "platform_draft"
    assert "click_save_draft" in gateway.actions


@pytest.mark.parametrize(
    ("action", "state_setup", "expected_reason"),
    [
        (
            "draft",
            "let state = {}; let cursor = state; "
            f"for (let depth = 0; depth < {STATE_TRAVERSAL_MAX_DEPTH + 1}; depth += 1) {{ "
            "cursor.child = {}; cursor = cursor.child; }",
            {
                "root": "test_state",
                "kind": "depth_budget_exceeded",
                "depth": STATE_TRAVERSAL_MAX_DEPTH + 1,
            },
        ),
        (
            "publish",
            "const state = {}; "
            f"for (let entry = 0; entry < {STATE_TRAVERSAL_MAX_ENTRIES + 1}; entry += 1) "
            "state[`item_${entry}`] = entry;",
            {
                "root": "test_state",
                "kind": "entry_budget_exceeded",
                "entries": STATE_TRAVERSAL_MAX_ENTRIES + 1,
            },
        ),
    ],
    ids=["depth-8", "entries-501"],
)
def test_actual_state_collector_aborts_publisher_before_click_at_budget_boundary(
    tmp_path: Path, action, state_setup, expected_reason
):
    gateway = CollectorBackedFakeGateway(
        draft_outcome() if action == "draft" else published_outcome(),
        state_setup,
    )

    with pytest.raises(WeChatPublishingError, match="操作前基线采集不完整"):
        WeChatBrowserPublisher(gateway=gateway).publish(
            article_payload(tmp_path), browser_account(tmp_path), action=action
        )

    assert "click_save_draft" not in gateway.actions
    assert "click_publish" not in gateway.actions
    assert gateway.collector_result == {
        "receipts": [],
        "baseline_complete": False,
        "baseline_incomplete_reasons": [expected_reason],
    }
    assert len(gateway.closed_sessions) == 1


def test_wechat_browser_publisher_rejects_operator_confirmation_without_second_scan(
    tmp_path: Path,
):
    gateway = FakeWeChatGateway(
        {
            "status": "awaiting_confirmation",
            "platform_url": "https://mp.weixin.qq.com/cgi-bin/masssendpage?token=secret-token",
            "baseline_id": "baseline-1",
            "confirmation": {"kind": "operator_qr", "screenshot_available": True},
        }
    )

    with pytest.raises(WeChatPublishingError, match="微信平台要求二次安全确认"):
        WeChatBrowserPublisher(gateway=gateway).publish(
            article_payload(tmp_path), browser_account(tmp_path), action="publish"
        )

    assert "retain_confirmation" not in gateway.actions
    assert len(gateway.closed_sessions) == 1


def test_wechat_browser_publisher_resumes_preserved_confirmation_session(tmp_path: Path):
    gateway = FakeWeChatGateway(published_outcome())
    account = browser_account(tmp_path)
    session = {"session_id": "session-1", "url": "https://mp.weixin.qq.com/cgi-bin/masssendpage"}
    gateway.retained["opaque-resume-handle"] = {
        "session": session,
        "account_key": "4",
        "title": "浏览器发布测试",
        "baseline": {
            "baseline_id": "baseline-1",
            "receipt_ids": ["old-receipt"],
            "title": "浏览器发布测试",
            "action": "publish",
        },
    }
    result = WeChatBrowserPublisher(gateway=gateway).resume(
        account,
        {"resume_handle": "opaque-resume-handle"},
    )

    assert result.status == "published"
    assert result.platform_publish_id == "publish-88"
    assert gateway.actions[0][0] == "resume_profile"
    assert gateway.actions[0][2] == "opaque-resume-handle"
    assert gateway.actions[-1] == "verify_publish_or_confirmation"
    assert len(gateway.closed_sessions) == 1


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        (
            {
                "status": "platform_draft",
                "draft_id": "",
                "platform_url": "https://mp.weixin.qq.com/",
                "baseline_id": "baseline-1",
                "receipt": {"id": "", "title": "浏览器发布测试", "source": "dom"},
                "post_action_signal": {"kind": "draft_saved", "observed_after_action": True},
            },
            "草稿 ID",
        ),
        (
            {
                "status": "published",
                "publish_id": "publish-88",
                "platform_url": "https://example.com/receipt",
                "baseline_id": "baseline-1",
                "receipt": {"id": "publish-88", "title": "浏览器发布测试", "source": "dom"},
                "post_action_signal": {"kind": "publish_success", "observed_after_action": True},
            },
            "微信页面 URL",
        ),
        (
            {
                "status": "published",
                "publish_id": "publish-88",
                "platform_url": "https://mp.weixin.qq.com/",
                "baseline_id": "baseline-1",
                "receipt": {"id": "publish-88", "title": "浏览器发布测试", "source": "dom"},
            },
            "后置信号",
        ),
    ],
)
def test_wechat_browser_publisher_rejects_unverified_platform_result(
    tmp_path: Path, outcome, message
):
    gateway = FakeWeChatGateway(outcome)

    with pytest.raises(WeChatPublishingError, match=message):
        WeChatBrowserPublisher(gateway=gateway).publish(
            article_payload(tmp_path),
            browser_account(tmp_path),
            action="draft" if outcome["status"] == "platform_draft" else "publish",
        )

    assert len(gateway.closed_sessions) == 1


def test_wechat_browser_publisher_rejects_receipt_seen_before_this_action(tmp_path: Path):
    outcome = draft_outcome()
    outcome["draft_id"] = "old-receipt"
    outcome["receipt"]["id"] = "old-receipt"
    gateway = FakeWeChatGateway(outcome)

    with pytest.raises(WeChatPublishingError, match="本次操作新产生"):
        WeChatBrowserPublisher(gateway=gateway).publish(
            article_payload(tmp_path), browser_account(tmp_path), action="draft"
        )


@pytest.mark.parametrize(
    ("receipt", "message"),
    [
        ({"id": "draft-42", "title": "", "source": "dom"}, "文章标题"),
        ({"id": "draft-42", "title": "其他文章", "source": "dom"}, "文章标题"),
        ({"id": "draft-42", "title": "浏览器发布测试", "source": "local"}, "平台 DOM"),
    ],
)
def test_wechat_browser_publisher_rejects_unbound_receipt(tmp_path: Path, receipt, message):
    outcome = draft_outcome()
    outcome["receipt"] = receipt
    gateway = FakeWeChatGateway(outcome)

    with pytest.raises(WeChatPublishingError, match=message):
        WeChatBrowserPublisher(gateway=gateway).publish(
            article_payload(tmp_path), browser_account(tmp_path), action="draft"
        )


def test_wechat_browser_publisher_rejects_mismatched_baseline(tmp_path: Path):
    outcome = draft_outcome()
    outcome["baseline_id"] = "another-operation"

    with pytest.raises(WeChatPublishingError, match="操作基线"):
        WeChatBrowserPublisher(gateway=FakeWeChatGateway(outcome)).publish(
            article_payload(tmp_path), browser_account(tmp_path), action="draft"
        )


def test_wechat_browser_publisher_cancel_uses_only_opaque_handle(tmp_path: Path):
    gateway = FakeWeChatGateway(draft_outcome())
    account = browser_account(tmp_path)

    WeChatBrowserPublisher(gateway=gateway).cancel(
        account, {"resume_handle": "opaque-resume-handle"}
    )

    assert gateway.cancelled == [("opaque-resume-handle", "4")]


def test_wechat_browser_publisher_rejects_invalid_action_before_opening_browser(tmp_path: Path):
    gateway = FakeWeChatGateway(draft_outcome())

    with pytest.raises(WeChatPublishingError, match="draft 或 publish"):
        WeChatBrowserPublisher(gateway=gateway).publish(
            article_payload(tmp_path), browser_account(tmp_path), action="preview"
        )

    assert gateway.actions == []


def test_wechat_browser_publisher_requires_saved_profile(tmp_path: Path):
    gateway = FakeWeChatGateway(draft_outcome())
    account = browser_account(tmp_path)
    account["profile_path"] = str(tmp_path / "missing-profile")

    with pytest.raises(WeChatPublishingError, match="浏览器 Profile"):
        WeChatBrowserPublisher(gateway=gateway).publish(
            article_payload(tmp_path), account, action="draft"
        )

    assert gateway.actions == []


def test_browser_gateway_rejects_profile_with_only_prelogin_cookies(tmp_path: Path):
    class ExpiredCookieGateway(ChromeCDPBrowserGateway):
        def __init__(self):
            super().__init__(auth_timeout=0)

        def _current_url(self, session):
            return "https://mp.weixin.qq.com/"

        @staticmethod
        def _cdp_call(session, method, params=None):
            assert method == "Network.getAllCookies"
            return {
                "cookies": [
                    {"name": name}
                    for name in ("mm_lang", "ua_id", "uuid", "wxuin", "xid")
                ]
            }

    session = BrowserSession(
        session_id="expired-session",
        profile_path=tmp_path,
        port=9222,
        target_id="page-1",
        websocket_url="ws://127.0.0.1/devtools/page/page-1",
    )

    with pytest.raises(WeChatPublishingError, match="登录态失效，请重新扫码"):
        ExpiredCookieGateway().require_authenticated(session)


class AuthenticationGateway(ChromeCDPBrowserGateway):
    def __init__(self, url, cookies, dashboard_verified=False):
        super().__init__(auth_timeout=0)
        self.url = url
        self.cookies = cookies
        self.dashboard_verified = dashboard_verified

    def _current_url(self, session):
        return self.url

    def _cdp_call(self, session, method, params=None):
        assert method == "Network.getAllCookies"
        return {"cookies": self.cookies}

    def _dashboard_token_verified(self, session):
        return self.dashboard_verified


def auth_cookie(name, *, value="valid", domain=".mp.weixin.qq.com", expires=None):
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "expires": expires if expires is not None else time.time() + 300,
    }


class IdentityGateway(ChromeCDPBrowserGateway):
    def __init__(self, cookies, account_name):
        super().__init__()
        self.cookies = cookies
        self.account_name = account_name

    def _cdp_call(self, session, method, params=None):
        assert method == "Network.getAllCookies"
        return {"cookies": self.cookies}

    def evaluate(self, session, expression):
        return self.account_name


def browser_session(tmp_path: Path, url="https://mp.weixin.qq.com/"):
    return BrowserSession(
        session_id="session-1",
        profile_path=tmp_path.resolve(),
        port=9222,
        target_id="page-1",
        websocket_url="ws://127.0.0.1/devtools/page/page-1",
        url=url,
    )


def test_cdp_call_refreshes_target_and_retries_when_target_id_is_stale(
    tmp_path: Path,
    monkeypatch,
):
    gateway = ChromeCDPBrowserGateway()
    session = BrowserSession(
        session_id="session-1",
        profile_path=tmp_path.resolve(),
        port=9322,
        target_id="old-target",
        websocket_url="ws://old-target",
        url="https://mp.weixin.qq.com/cgi-bin/appmsg",
    )
    attempts = []

    class FakeConnection:
        def __init__(self):
            self.sent = []
            self.closed = False

        def send(self, payload):
            self.sent.append(json.loads(payload))

        def recv(self):
            return json.dumps({"id": self.sent[-1]["id"], "result": {"value": "ok"}})

        def close(self):
            self.closed = True

    def fake_create_connection(url, timeout):
        attempts.append(url)
        if len(attempts) == 1:
            raise browser_gateway_module.websocket.WebSocketException(
                "Handshake status 500 Internal Server Error -+-+- {'content-length': '51'} -+-+- "
                "b'No such target id: old-target'"
            )
        return FakeConnection()

    monkeypatch.setattr(
        browser_gateway_module.websocket,
        "create_connection",
        fake_create_connection,
    )
    monkeypatch.setattr(
        gateway,
        "_targets",
        lambda port: [
            {
                "id": "new-target",
                "type": "page",
                "url": "https://mp.weixin.qq.com/cgi-bin/appmsg",
                "webSocketDebuggerUrl": "ws://new-target",
            }
        ],
    )

    result = gateway._cdp_call(session, "Runtime.evaluate", {"expression": "1"})

    assert result == {"value": "ok"}
    assert attempts == ["ws://old-target", "ws://new-target"]
    assert session.target_id == "new-target"
    assert session.websocket_url == "ws://new-target"


def test_browser_gateway_auth_requires_both_scoped_nonexpired_cookies(tmp_path: Path):
    gateway = AuthenticationGateway(
        "https://mp.weixin.qq.com/",
        [auth_cookie("slave_sid"), auth_cookie("bizuin")],
    )

    gateway.require_authenticated(browser_session(tmp_path))


@pytest.mark.parametrize(
    "cookies",
    [
        [auth_cookie("slave_sid")],
        [auth_cookie("slave_sid"), auth_cookie("bizuin", value="")],
        [auth_cookie("slave_sid"), auth_cookie("bizuin", domain="example.com")],
        [
            auth_cookie("slave_sid", expires=1),
            auth_cookie("bizuin", expires=1),
        ],
    ],
)
def test_browser_gateway_auth_rejects_partial_empty_wrong_domain_or_expired_cookies(
    tmp_path: Path, cookies
):
    gateway = AuthenticationGateway("https://mp.weixin.qq.com/", cookies)

    with pytest.raises(WeChatPublishingError, match="登录态失效，请重新扫码"):
        gateway.require_authenticated(browser_session(tmp_path))


def test_browser_gateway_auth_rejects_valid_cookie_names_on_wrong_host(tmp_path: Path):
    gateway = AuthenticationGateway(
        "https://example.com/cgi-bin/home?token=stolen",
        [auth_cookie("slave_sid"), auth_cookie("bizuin")],
    )

    with pytest.raises(WeChatPublishingError, match="不是 mp.weixin.qq.com"):
        gateway.require_authenticated(browser_session(tmp_path))


def test_browser_gateway_accepts_authenticated_dashboard_token(tmp_path: Path):
    gateway = AuthenticationGateway(
        "https://mp.weixin.qq.com/cgi-bin/home?t=home/index&token=dashboard-token",
        [],
        dashboard_verified=True,
    )

    gateway.require_authenticated(browser_session(tmp_path))


def test_browser_gateway_rejects_wrong_bizuin_even_when_account_name_matches(tmp_path: Path):
    gateway = IdentityGateway(
        [auth_cookie("bizuin", value="logged-in-bizuin")],
        "理想说AI",
    )

    with pytest.raises(WeChatPublishingError, match="当前登录公众号与所选账号不一致"):
        gateway.require_account_identity(
            browser_session(tmp_path),
            {"external_id": "selected-bizuin", "name": "理想说AI"},
        )


@pytest.mark.parametrize(
    ("cookies", "external_id"),
    [
        ([], "selected-bizuin"),
        ([auth_cookie("bizuin", value="logged-in-bizuin")], ""),
        ([], ""),
    ],
)
def test_browser_gateway_rejects_missing_stable_identity_even_when_account_name_matches(
    tmp_path: Path, cookies, external_id
):
    gateway = IdentityGateway(cookies, "理想说AI")

    with pytest.raises(WeChatPublishingError, match="无法验证.*稳定身份"):
        gateway.require_account_identity(
            browser_session(tmp_path),
            {"external_id": external_id, "name": "理想说AI"},
        )


def test_browser_gateway_rejects_unverified_dashboard_token(tmp_path: Path):
    gateway = AuthenticationGateway(
        "https://mp.weixin.qq.com/cgi-bin/home?t=home/index&token=stale-token",
        [],
        dashboard_verified=False,
    )

    with pytest.raises(WeChatPublishingError, match="登录态失效，请重新扫码"):
        gateway.require_authenticated(browser_session(tmp_path))


class ObservationGateway(ChromeCDPBrowserGateway):
    def __init__(self, observations):
        super().__init__(result_timeout=0)
        self.observations = list(observations)
        self.baseline_id = ""

    def _install_operation_marker(self, session, baseline_id):
        self.baseline_id = baseline_id

    def evaluate(self, session, expression):
        observation = self.observations.pop(0)
        observation["baseline_id"] = self.baseline_id
        observation.setdefault("baseline_complete", True)
        observation.setdefault("baseline_incomplete_reasons", [])
        return observation


def test_browser_gateway_accepts_only_new_title_bound_receipt_with_post_action_signal(tmp_path: Path):
    baseline_observation = {
        "url": "https://mp.weixin.qq.com/cgi-bin/appmsg?token=secret",
        "receipts": [{"id": "old-id", "title": "浏览器发布测试", "source": "dom"}],
        "signals": [],
    }
    post_observation = {
        "url": "https://mp.weixin.qq.com/cgi-bin/appmsg?action=list&token=secret",
        "receipts": [
            {"id": "old-id", "title": "浏览器发布测试", "source": "dom"},
            {
                "id": "new-id",
                "title": "浏览器发布测试",
                "source": "dom",
                "observed_after_baseline": True,
            },
        ],
        "signals": [
            {"kind": "draft_saved", "observed_after_baseline": True}
        ],
    }
    gateway = ObservationGateway([baseline_observation, post_observation])
    session = browser_session(tmp_path)

    baseline = gateway.capture_baseline(session, "浏览器发布测试", "draft")
    outcome = gateway.verify_draft(session, "浏览器发布测试", baseline)

    assert baseline["receipt_ids"] == ["old-id"]
    assert outcome["draft_id"] == "new-id"
    assert outcome["receipt"]["title"] == "浏览器发布测试"
    assert outcome["post_action_signal"]["observed_after_action"] is True


def test_browser_gateway_accepts_manual_save_history_for_platform_draft(tmp_path: Path):
    same_url = "https://mp.weixin.qq.com/cgi-bin/appmsg"
    gateway = ObservationGateway(
        [
            {"url": same_url, "receipts": [], "signals": []},
            {
                "url": same_url,
                "receipts": [],
                "signals": [
                    {
                        "kind": "draft_manual_save_history",
                        "observed_after_baseline": True,
                        "saved_at": "07-22 18:20",
                    }
                ],
            },
        ]
    )
    session = browser_session(tmp_path)

    baseline = gateway.capture_baseline(session, "浏览器发布测试", "draft")
    outcome = gateway.verify_draft(session, "浏览器发布测试", baseline)

    assert outcome["status"] == "platform_draft"
    assert outcome["draft_id"].startswith("manual-save:")
    assert outcome["post_action_signal"]["kind"] == "draft_manual_save_history"


def test_browser_gateway_rejects_empty_title_or_preexisting_receipts(tmp_path: Path):
    baseline_observation = {
        "url": "https://mp.weixin.qq.com/cgi-bin/appmsg",
        "receipts": [{"id": "old-id", "title": "浏览器发布测试", "source": "dom"}],
        "signals": [],
    }
    post_observation = {
        "url": "https://mp.weixin.qq.com/cgi-bin/appmsg?action=list",
        "receipts": [
            {"id": "old-id", "title": "浏览器发布测试", "source": "dom"},
            {
                "id": "unbound-id",
                "title": "",
                "source": "dom",
                "observed_after_baseline": True,
            },
        ],
        "signals": [
            {"kind": "draft_saved", "observed_after_baseline": True}
        ],
    }
    gateway = ObservationGateway([baseline_observation, post_observation])
    session = browser_session(tmp_path)
    baseline = gateway.capture_baseline(session, "浏览器发布测试", "draft")

    with pytest.raises(WeChatPublishingError, match="未返回可验证的草稿结果"):
        gateway.verify_draft(session, "浏览器发布测试", baseline)


def test_browser_gateway_rejects_new_receipt_without_post_action_signal(tmp_path: Path):
    same_url = "https://mp.weixin.qq.com/cgi-bin/appmsg"
    gateway = ObservationGateway(
        [
            {"url": same_url, "receipts": [], "signals": []},
            {
                "url": same_url,
                "receipts": [
                    {"id": "new-id", "title": "浏览器发布测试", "source": "dom"}
                ],
                "signals": [],
            },
        ]
    )
    session = browser_session(tmp_path)
    baseline = gateway.capture_baseline(session, "浏览器发布测试", "draft")

    with pytest.raises(WeChatPublishingError, match="未返回可验证的草稿结果"):
        gateway.verify_draft(session, "浏览器发布测试", baseline)


def test_browser_gateway_rejects_receipt_not_mutated_after_baseline(tmp_path: Path):
    same_url = "https://mp.weixin.qq.com/cgi-bin/appmsg"
    gateway = ObservationGateway(
        [
            {"url": same_url, "receipts": [], "signals": []},
            {
                "url": same_url,
                "receipts": [
                    {"id": "old-list-id", "title": "浏览器发布测试", "source": "dom"}
                ],
                "signals": [
                    {"kind": "draft_saved", "observed_after_baseline": True}
                ],
            },
        ]
    )
    session = browser_session(tmp_path)
    baseline = gateway.capture_baseline(session, "浏览器发布测试", "draft")

    with pytest.raises(WeChatPublishingError, match="未返回可验证的草稿结果"):
        gateway.verify_draft(session, "浏览器发布测试", baseline)


def test_browser_gateway_baseline_keeps_all_receipt_ids_regardless_title_or_visibility(
    tmp_path: Path,
):
    gateway = ObservationGateway(
        [
            {
                "url": "https://mp.weixin.qq.com/cgi-bin/appmsg",
                "receipts": [
                    {"id": "same-title", "title": "浏览器发布测试", "source": "dom"},
                    {"id": "other-title", "title": "其他文章", "source": "dom"},
                    {"id": "hidden-no-title", "title": "", "source": "dom", "visible": False},
                ],
                "signals": [],
            }
        ]
    )

    baseline = gateway.capture_baseline(
        browser_session(tmp_path), "浏览器发布测试", "draft"
    )

    assert set(baseline["receipt_ids"]) == {
        "same-title",
        "other-title",
        "hidden-no-title",
    }
    assert {record["id"] for record in baseline["receipts"]} == set(
        baseline["receipt_ids"]
    )


def test_browser_gateway_rejects_baseline_record_reinserted_with_matching_title(
    tmp_path: Path,
):
    same_url = "https://mp.weixin.qq.com/cgi-bin/appmsg"
    gateway = ObservationGateway(
        [
            {
                "url": same_url,
                "receipts": [
                    {"id": "existing-id", "title": "", "source": "dom", "visible": False}
                ],
                "signals": [],
            },
            {
                "url": same_url,
                "receipts": [
                    {
                        "id": "existing-id",
                        "title": "浏览器发布测试",
                        "source": "dom",
                        "observed_after_baseline": True,
                    }
                ],
                "signals": [
                    {"kind": "draft_saved", "observed_after_baseline": True}
                ],
            },
        ]
    )
    session = browser_session(tmp_path)
    baseline = gateway.capture_baseline(session, "浏览器发布测试", "draft")

    with pytest.raises(WeChatPublishingError, match="未返回可验证的草稿结果"):
        gateway.verify_draft(session, "浏览器发布测试", baseline)


def test_receipt_title_uses_nfkc_whitespace_normalized_exact_equality():
    observation = {
        "receipts": [
            {
                "id": "new-id",
                "title": "ＡＩ　 发布测试",
                "source": "dom",
                "observed_after_baseline": True,
            }
        ]
    }
    baseline = {"receipt_ids": []}

    receipt = ChromeCDPBrowserGateway._new_receipt(
        observation, baseline, "AI 发布测试"
    )

    assert receipt and receipt["id"] == "new-id"
    observation["receipts"][0]["title"] = "AI 发布测试扩展"
    assert (
        ChromeCDPBrowserGateway._new_receipt(
            observation, baseline, "AI 发布测试"
        )
        is None
    )


def test_receipt_dom_expression_never_uses_substring_or_synthesized_title():
    expression = ChromeCDPBrowserGateway._observation_expression(
        "浏览器发布测试", "draft", include_hidden=True
    )

    assert ".includes(expectedTitle)" not in expression
    assert "title: expectedTitle" not in expression
    assert "includeHidden || visible(node)" in expression


def test_baseline_expression_collects_unrendered_platform_state_records():
    expression = ChromeCDPBrowserGateway._observation_expression(
        "浏览器发布测试", "draft", include_hidden=True
    )

    assert "window.__INITIAL_STATE__" in expression
    assert "window.cgiData" in expression
    assert "collectStateRecords" in expression
    assert "depth_budget_exceeded" in expression
    assert "entry_budget_exceeded" in expression
    assert "baseline_complete" in expression
    assert ".slice(0, 500)" not in expression
    assert ChromeCDPBrowserGateway._baseline_state_collector_source().strip() in expression


@pytest.mark.parametrize(
    ("action", "signal_kind"),
    [("draft", "publish_success"), ("publish", "draft_saved")],
)
def test_action_specific_signal_rejects_other_action(action, signal_kind):
    observation = {
        "signals": [
            {"kind": signal_kind, "observed_after_baseline": True}
        ]
    }

    assert (
        ChromeCDPBrowserGateway._post_action_signal(
            observation, {"signal_kinds": []}, action
        )
        is None
    )


@pytest.mark.parametrize(
    ("outcome_factory", "action", "wrong_signal"),
    [
        (draft_outcome, "draft", "publish_success"),
        (published_outcome, "publish", "draft_saved"),
    ],
)
def test_publisher_rejects_other_action_success_signal(
    tmp_path: Path, outcome_factory, action, wrong_signal
):
    outcome = outcome_factory()
    outcome["post_action_signal"]["kind"] = wrong_signal

    with pytest.raises(WeChatPublishingError, match="动作不匹配"):
        WeChatBrowserPublisher(gateway=FakeWeChatGateway(outcome)).publish(
            article_payload(tmp_path), browser_account(tmp_path), action=action
        )


def test_cover_and_inline_upload_selectors_are_disjoint_and_not_generic():
    assert set(COVER_FILE_SELECTORS).isdisjoint(INLINE_FILE_SELECTORS)
    assert "input[type='file'][accept*='image']" not in COVER_FILE_SELECTORS
    assert "input[type='file'][accept*='image']" not in INLINE_FILE_SELECTORS


def test_file_upload_locator_requires_unique_visible_image_file_input():
    expression = ChromeCDPBrowserGateway._file_input_expression(COVER_FILE_SELECTORS)

    assert "ambiguous file input" in expression
    assert "node.type !== 'file'" in expression
    assert "visibleControl(node)" in expression
    assert "requireVisibleControl" in expression
    assert "accept.includes('image')" in expression
    assert "priority" in expression


def test_file_upload_locator_prefers_input_with_visible_upload_host():
    expression = ChromeCDPBrowserGateway._file_input_expression(INLINE_FILE_SELECTORS)

    assert "hostVisible" in expression
    assert "hostArea" in expression
    assert "right.hostVisible - left.hostVisible" in expression
    assert "right.hostArea - left.hostArea" in expression


def test_file_upload_locator_can_allow_hidden_input_after_upload_entry_is_opened():
    expression = ChromeCDPBrowserGateway._file_input_expression(
        COVER_FILE_SELECTORS,
        require_visible_control=False,
    )

    assert "const requireVisibleControl = false" in expression
    assert "(!requireVisibleControl || visibleControl(node))" in expression


def test_save_draft_action_supports_visible_wechat_text_button():
    expression = ChromeCDPBrowserGateway._click_text_expression(SAVE_DRAFT_TEXTS)

    assert "保存为草稿" in expression
    assert "button, a, [role=button]" in expression
    assert "node.click()" in expression


def test_file_upload_can_click_wechat_upload_entry_before_resolving_input():
    expression = ChromeCDPBrowserGateway._upload_trigger_expression("cover")

    assert "拖拽或选择封面" in expression
    assert ".js_cover_area" in expression
    assert "body *" not in expression
    assert "closest('label, button, a, [role=button]')" in expression
    assert "textLength" in expression
    assert "const triggerWithDomClick = false" in expression
    assert "if (triggerWithDomClick) node.click()" in expression
    assert "getBoundingClientRect" in expression


def test_inline_upload_trigger_keeps_dom_click_for_editor_menu():
    expression = ChromeCDPBrowserGateway._upload_trigger_expression("inline")

    assert "const triggerWithDomClick = true" in expression
    assert "if (triggerWithDomClick) node.click()" in expression


class MissingFileInputGateway(ChromeCDPBrowserGateway):
    def __init__(self, trigger_result=False):
        super().__init__()
        self.trigger_result = trigger_result

    def _remote_image_state(self, session, role):
        return {"urls": [], "loading": False}

    def _cdp_call(self, session, method, params=None):
        if method == "Runtime.evaluate":
            return {"result": {}}
        return {}

    def _trigger_upload_entry(self, session, role):
        return self.trigger_result


class CoverTriggerMouseGateway(ChromeCDPBrowserGateway):
    def __init__(self):
        super().__init__()
        self.mouse_events = []

    def _cdp_call(self, session, method, params=None):
        if method == "Input.dispatchMouseEvent":
            self.mouse_events.append(params)
            return {}
        return {}

    def evaluate(self, session, expression):
        return {
            "ok": True,
            "count": 1,
            "rect": {"x": 100, "y": 200, "width": 240, "height": 100},
        }


def test_cover_upload_trigger_uses_real_mouse_click_on_cover_area(tmp_path: Path):
    gateway = CoverTriggerMouseGateway()

    assert gateway._trigger_upload_entry(browser_session(tmp_path), "cover") is True

    assert gateway.mouse_events == [
        {"type": "mouseMoved", "x": 220, "y": 250, "button": "none"},
        {"type": "mousePressed", "x": 220, "y": 250, "button": "left", "clickCount": 1},
        {"type": "mouseReleased", "x": 220, "y": 250, "button": "left", "clickCount": 1},
    ]


class FakeFileChooserConnection:
    def __init__(self):
        self.sent = []
        self.file_chooser_sent = False
        self.closed = False
        self.captured_input = False

    def send(self, value):
        self.sent.append(json.loads(value))

    def recv(self):
        last = self.sent[-1]
        method = last["method"]
        response_id = last["id"]
        if method == "Page.setInterceptFileChooserDialog":
            return json.dumps({"id": response_id, "result": {}})
        if method == "Runtime.evaluate":
            expression = last.get("params", {}).get("expression", "")
            if "__codexFileInputCapture" in expression and "captured" in expression:
                return json.dumps(
                    {
                        "id": response_id,
                        "result": {"result": {"value": {"ok": self.captured_input}}},
                    }
                )
            if "__codexFileInputCapture" in expression:
                return json.dumps(
                    {
                        "id": response_id,
                        "result": {"result": {"value": {"ok": True, "installed": True}}},
                    }
                )
            return json.dumps(
                {
                    "id": response_id,
                    "result": {
                        "result": {
                            "value": {
                                "ok": True,
                                "rect": {"x": 10, "y": 20, "width": 200, "height": 80},
                            }
                        }
                    },
                }
            )
        if method == "Input.dispatchMouseEvent":
            if not self.file_chooser_sent and last["params"]["type"] == "mouseReleased":
                self.file_chooser_sent = True
                return json.dumps(
                    {
                        "method": "Page.fileChooserOpened",
                        "params": {"backendNodeId": 321, "mode": "selectSingle"},
                    }
                )
            return json.dumps({"id": response_id, "result": {}})
        if method == "DOM.setFileInputFiles":
            return json.dumps({"id": response_id, "result": {}})
        if method == "DOM.getDocument":
            return json.dumps({"id": response_id, "result": {"root": {"nodeId": 1}}})
        if method == "DOM.querySelector":
            return json.dumps({"id": response_id, "result": {"nodeId": 0}})
        return json.dumps({"id": response_id, "result": {}})

    def close(self):
        self.closed = True


def test_cover_upload_can_use_file_chooser_event_backend_node_id(
    tmp_path: Path, monkeypatch
):
    image_path = tmp_path / "cover.png"
    image_path.write_bytes(b"fake image")
    connection = FakeFileChooserConnection()
    monkeypatch.setattr(
        browser_gateway_module.websocket,
        "create_connection",
        lambda url, timeout: connection,
    )
    gateway = ChromeCDPBrowserGateway()

    assert gateway._set_cover_file_via_file_chooser(
        browser_session(tmp_path),
        image_path.resolve(),
    )

    methods = [item["method"] for item in connection.sent]
    assert methods[:2] == ["Page.setInterceptFileChooserDialog", "Runtime.evaluate"]
    assert "Input.dispatchMouseEvent" in methods
    assert {
        "id": 6,
        "method": "DOM.setFileInputFiles",
        "params": {"files": [str(image_path.resolve())], "backendNodeId": 321},
    } in connection.sent
    assert connection.sent[-1]["method"] == "Page.setInterceptFileChooserDialog"
    assert connection.sent[-1]["params"] == {"enabled": False}
    assert connection.closed


def test_generic_file_chooser_sets_multiple_files_with_backend_node_id(
    tmp_path: Path, monkeypatch
):
    first = tmp_path / "first.png"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    connection = FakeFileChooserConnection()
    monkeypatch.setattr(
        browser_gateway_module.websocket,
        "create_connection",
        lambda url, timeout: connection,
    )
    gateway = ChromeCDPBrowserGateway()

    assert gateway.set_file_input_files_via_file_chooser(
        browser_session(tmp_path),
        "(() => ({ ok: true, rect: { x: 10, y: 20, width: 200, height: 80 } }))()",
        [first, second],
        "小红书图文图片",
        trace_prefix="xhs.upload",
    )

    methods = [item["method"] for item in connection.sent]
    assert methods[:4] == [
        "Page.enable",
        "Runtime.enable",
        "DOM.enable",
        "Page.setInterceptFileChooserDialog",
    ]
    assert {
        "method": "DOM.setFileInputFiles",
        "params": {"files": [str(first.resolve()), str(second.resolve())], "backendNodeId": 321},
    } in [
        {"method": item["method"], "params": item.get("params")}
        for item in connection.sent
    ]
    assert connection.sent[-1]["method"] == "Page.setInterceptFileChooserDialog"
    assert connection.sent[-1]["params"] == {"enabled": False}
    assert connection.closed


class FakeCapturedInputConnection(FakeFileChooserConnection):
    def __init__(self):
        super().__init__()
        self.captured_input = True

    def recv(self):
        last = self.sent[-1]
        method = last["method"]
        response_id = last["id"]
        if method == "DOM.querySelector":
            return json.dumps({"id": response_id, "result": {"nodeId": 456}})
        return super().recv()


def test_generic_file_chooser_sets_files_on_captured_programmatic_input(
    tmp_path: Path, monkeypatch
):
    image = tmp_path / "first.png"
    image.write_bytes(b"first")
    connection = FakeCapturedInputConnection()
    monkeypatch.setattr(
        browser_gateway_module.websocket,
        "create_connection",
        lambda url, timeout: connection,
    )
    gateway = ChromeCDPBrowserGateway()

    assert gateway.set_file_input_files_via_file_chooser(
        browser_session(tmp_path),
        "(() => ({ ok: true, rect: { x: 10, y: 20, width: 200, height: 80 } }))()",
        [image],
        "小红书图文图片",
        trace_prefix="xhs.upload",
    )

    assert {
        "method": "DOM.setFileInputFiles",
        "params": {"files": [str(image.resolve())], "nodeId": 456},
    } in [
        {"method": item["method"], "params": item.get("params")}
        for item in connection.sent
    ]
    assert "Input.dispatchMouseEvent" not in [item["method"] for item in connection.sent]
    assert connection.closed


class FakeInlineFileChooserConnection:
    def __init__(self):
        self.sent = []
        self.file_chooser_sent = False
        self.closed = False

    def send(self, value):
        self.sent.append(json.loads(value))

    def recv(self):
        last = self.sent[-1]
        method = last["method"]
        response_id = last["id"]
        if method == "Page.setInterceptFileChooserDialog":
            return json.dumps({"id": response_id, "result": {}})
        if method == "Runtime.evaluate":
            return json.dumps(
                {
                    "id": response_id,
                    "result": {
                        "result": {
                            "value": {
                                "ok": True,
                                "rect": {"x": 120, "y": 80, "width": 96, "height": 36},
                            }
                        }
                    },
                }
            )
        if method == "Input.dispatchMouseEvent":
            if not self.file_chooser_sent and last["params"]["type"] == "mouseReleased":
                self.file_chooser_sent = True
                return json.dumps(
                    {
                        "method": "Page.fileChooserOpened",
                        "params": {"backendNodeId": 654, "mode": "selectSingle"},
                    }
                )
            return json.dumps({"id": response_id, "result": {}})
        if method == "DOM.setFileInputFiles":
            return json.dumps({"id": response_id, "result": {}})
        return json.dumps({"id": response_id, "result": {}})

    def close(self):
        self.closed = True


class FakeInlineFileChooserDelayedConnection:
    def __init__(self):
        self.sent = []
        self.file_chooser_sent = False
        self.closed = False
        self.inline_trigger_returned = False
        self.timeout_after_inline_trigger = True

    def send(self, value):
        self.sent.append(json.loads(value))

    def recv(self):
        last = self.sent[-1]
        method = last["method"]
        response_id = last["id"]
        if method == "Page.setInterceptFileChooserDialog":
            return json.dumps({"id": response_id, "result": {}})
        if method == "Runtime.evaluate":
            expression = str(last["params"].get("expression") or "")
            if (
                "edui-for-insertimage" in expression
                and self.inline_trigger_returned
                and self.timeout_after_inline_trigger
            ):
                self.timeout_after_inline_trigger = False
                raise browser_gateway_module.websocket.WebSocketTimeoutException("timed out")
            if "edui-for-insertimage" in expression:
                self.inline_trigger_returned = True
            return json.dumps(
                {
                    "id": response_id,
                    "result": {
                        "result": {
                            "value": {
                                "ok": True,
                                "rect": {"x": 120, "y": 80, "width": 96, "height": 36},
                                "text": "上传图片",
                            }
                        }
                    },
                }
            )
        if method == "Input.dispatchMouseEvent":
            if not self.file_chooser_sent and last["params"]["type"] == "mouseReleased":
                self.file_chooser_sent = True
                return json.dumps(
                    {
                        "method": "Page.fileChooserOpened",
                        "params": {"backendNodeId": 987, "mode": "selectSingle"},
                    }
                )
            return json.dumps({"id": response_id, "result": {}})
        if method == "DOM.setFileInputFiles":
            return json.dumps({"id": response_id, "result": {}})
        return json.dumps({"id": response_id, "result": {}})

    def close(self):
        self.closed = True


class InlineChooserRecoveryGateway(ChromeCDPBrowserGateway):
    def __init__(self):
        super().__init__()
        self.image_states = [
            {"urls": ["https://mmbiz.qpic.cn/old-inline.jpg"], "loading": False},
            {
                "urls": [
                    "https://mmbiz.qpic.cn/old-inline.jpg",
                    "https://mmbiz.qpic.cn/uploaded-inline.jpg",
                ],
                "loading": False,
            },
        ]

    def _remote_image_state(self, session, role):
        return self.image_states.pop(0)

    def _resolve_file_input_reference(
        self, session, selectors, require_visible_control=True, role="inline", label="图片"
    ):
        return {"object_id": "stale-runtime-object", "backend_node_id": 0}

    def _backend_node_id_for_object(self, session, object_id):
        return 0

    def _cdp_call(self, session, method, params=None):
        if method == "DOM.setFileInputFiles":
            raise OSError("DOM.setFileInputFiles：Could not find object with given id")
        return {}


def test_inline_upload_uses_file_chooser_when_runtime_object_id_is_stale(
    tmp_path: Path, monkeypatch
):
    image_path = tmp_path / "inline.png"
    image_path.write_bytes(b"fake image")
    connection = FakeInlineFileChooserConnection()
    monkeypatch.setattr(
        browser_gateway_module.websocket,
        "create_connection",
        lambda url, timeout: connection,
    )
    gateway = InlineChooserRecoveryGateway()

    evidence = gateway.set_file_input(
        browser_session(tmp_path),
        INLINE_FILE_SELECTORS,
        image_path,
        "正文配图",
        "inline",
    )

    assert {
        "id": 7,
        "method": "DOM.setFileInputFiles",
        "params": {"files": [str(image_path.resolve())], "backendNodeId": 654},
    } in connection.sent
    assert evidence["remote_url"] == "https://mmbiz.qpic.cn/uploaded-inline.jpg"
    assert connection.closed


def test_inline_upload_clicks_visible_host_when_initial_chooser_wait_times_out(
    tmp_path: Path, monkeypatch
):
    image_path = tmp_path / "inline.png"
    image_path.write_bytes(b"fake image")
    connection = FakeInlineFileChooserDelayedConnection()
    monkeypatch.setattr(
        browser_gateway_module.websocket,
        "create_connection",
        lambda url, timeout: connection,
    )
    gateway = ChromeCDPBrowserGateway()

    assert gateway._set_inline_file_via_file_chooser(
        browser_session(tmp_path),
        image_path.resolve(),
        INLINE_FILE_SELECTORS,
        "正文配图",
    )

    methods = [item["method"] for item in connection.sent]
    assert methods.count("Runtime.evaluate") >= 2
    assert {
        "id": 7,
        "method": "DOM.setFileInputFiles",
        "params": {"files": [str(image_path.resolve())], "backendNodeId": 987},
    } in connection.sent
    assert connection.closed


def test_inline_file_chooser_runtime_clicks_use_user_gesture(tmp_path: Path, monkeypatch):
    image_path = tmp_path / "inline.png"
    image_path.write_bytes(b"fake image")
    connection = FakeInlineFileChooserDelayedConnection()
    monkeypatch.setattr(
        browser_gateway_module.websocket,
        "create_connection",
        lambda url, timeout: connection,
    )
    gateway = ChromeCDPBrowserGateway()

    assert gateway._set_inline_file_via_file_chooser(
        browser_session(tmp_path),
        image_path.resolve(),
        INLINE_FILE_SELECTORS,
        "正文配图",
    )

    runtime_calls = [
        item for item in connection.sent if item["method"] == "Runtime.evaluate"
    ]
    assert runtime_calls
    assert all(call["params"].get("userGesture") is True for call in runtime_calls)


def test_file_upload_error_reports_trigger_status_and_all_attempted_selectors(tmp_path: Path):
    image_path = tmp_path / "cover.png"
    image_path.write_bytes(b"fake image")
    gateway = MissingFileInputGateway(trigger_result=False)

    with pytest.raises(WeChatPublishingError) as error:
        gateway.set_file_input(
            browser_session(tmp_path),
            COVER_FILE_SELECTORS,
            image_path,
            "封面图",
            "cover",
        )

    message = str(error.value)
    assert "未触发上传入口" in message
    assert ".js_cover_area input[type='file']" in message
    assert "input[type='file'][accept*='image']" not in message


class StaleObjectIdFileInputGateway(ChromeCDPBrowserGateway):
    def __init__(self):
        super().__init__()
        self.set_file_params = None
        self.image_states = [
            {"urls": ["https://mmbiz.qpic.cn/old-cover.jpg"], "loading": False},
            {"urls": ["https://mmbiz.qpic.cn/old-cover.jpg", "https://mmbiz.qpic.cn/uploaded-cover.jpg"], "loading": False},
        ]

    def _remote_image_state(self, session, role):
        return self.image_states.pop(0)

    def _cdp_call(self, session, method, params=None):
        if method == "Runtime.evaluate":
            return {"result": {"objectId": "stale-object-id"}}
        if method == "DOM.describeNode":
            assert params == {"objectId": "stale-object-id"}
            return {"node": {"backendNodeId": 42}}
        if method == "DOM.setFileInputFiles":
            self.set_file_params = params
            if params and "objectId" in params:
                raise OSError("DOM.setFileInputFiles: Could not find object with given id")
            return {}
        return {}


def test_file_upload_uses_stable_backend_node_id_instead_of_runtime_object_id(tmp_path: Path):
    image_path = tmp_path / "cover.png"
    image_path.write_bytes(b"fake image")
    gateway = StaleObjectIdFileInputGateway()

    evidence = gateway.set_file_input(
        browser_session(tmp_path),
        COVER_FILE_SELECTORS,
        image_path,
        "封面图",
        "cover",
    )

    assert gateway.set_file_params == {
        "files": [str(image_path.resolve())],
        "backendNodeId": 42,
    }
    assert evidence["remote_url"] == "https://mmbiz.qpic.cn/uploaded-cover.jpg"


class RequestNodeFallbackFileInputGateway(ChromeCDPBrowserGateway):
    def __init__(self):
        super().__init__()
        self.set_file_params = None
        self.describe_object_calls = 0
        self.request_node_calls = 0
        self.image_states = [
            {"urls": ["https://mmbiz.qpic.cn/old-cover.jpg"], "loading": False},
            {"urls": ["https://mmbiz.qpic.cn/old-cover.jpg", "https://mmbiz.qpic.cn/uploaded-cover.jpg"], "loading": False},
        ]

    def _set_cover_file_via_file_chooser(self, session, path):
        return False

    def _remote_image_state(self, session, role):
        return self.image_states.pop(0)

    def _trigger_upload_entry(self, session, role):
        return True

    def _cdp_call(self, session, method, params=None):
        if method == "Runtime.evaluate":
            return {"result": {"objectId": "runtime-object-id"}}
        if method == "DOM.getDocument":
            return {"root": {"nodeId": 1}}
        if method == "DOM.querySelector":
            return {"nodeId": 0}
        if method == "DOM.describeNode" and params == {"objectId": "runtime-object-id"}:
            self.describe_object_calls += 1
            raise OSError("No node with given id")
        if method == "DOM.requestNode":
            assert params == {"objectId": "runtime-object-id"}
            self.request_node_calls += 1
            return {"nodeId": 88}
        if method == "DOM.describeNode" and params == {"nodeId": 88}:
            return {"node": {"backendNodeId": 880}}
        if method == "DOM.setFileInputFiles":
            self.set_file_params = params
            if params and "objectId" in params:
                raise OSError("DOM.setFileInputFiles: Could not find object with given id")
            return {}
        return {}


def test_file_upload_resolves_backend_node_id_via_request_node_when_object_id_is_stale(
    tmp_path: Path,
):
    image_path = tmp_path / "cover.png"
    image_path.write_bytes(b"fake image")
    gateway = RequestNodeFallbackFileInputGateway()

    evidence = gateway.set_file_input(
        browser_session(tmp_path),
        COVER_FILE_SELECTORS,
        image_path,
        "封面图",
        "cover",
    )

    assert gateway.request_node_calls == 1
    assert gateway.set_file_params == {
        "files": [str(image_path.resolve())],
        "backendNodeId": 880,
    }
    assert evidence["remote_url"] == "https://mmbiz.qpic.cn/uploaded-cover.jpg"


class InlineUploadReopensInputGateway(ChromeCDPBrowserGateway):
    def __init__(self):
        super().__init__()
        self.trigger_roles = []
        self.set_file_params = []
        self.image_states = [
            {"urls": ["https://mmbiz.qpic.cn/old-inline.jpg"], "loading": False},
            {
                "urls": [
                    "https://mmbiz.qpic.cn/old-inline.jpg",
                    "https://mmbiz.qpic.cn/uploaded-inline.jpg",
                ],
                "loading": False,
            },
        ]

    def _set_inline_file_via_file_chooser(self, session, path, selectors, label):
        return False

    def _remote_image_state(self, session, role):
        return self.image_states.pop(0)

    def _trigger_upload_entry(self, session, role):
        self.trigger_roles.append(role)
        return True

    def _resolve_file_input_reference(
        self, session, selectors, require_visible_control=True, role="inline", label="图片"
    ):
        if "inline" in self.trigger_roles:
            return {"object_id": "fresh-runtime-object", "backend_node_id": 404}
        return {"object_id": "stale-runtime-object", "backend_node_id": 0}

    def _backend_node_id_for_object(self, session, object_id):
        return 0

    def _cdp_call(self, session, method, params=None):
        if method == "DOM.setFileInputFiles":
            self.set_file_params.append(params)
            if params and "objectId" in params:
                raise OSError("DOM.setFileInputFiles: Could not find object with given id")
            return {}
        return {}


def test_inline_upload_reopens_upload_entry_before_retrying_stale_input(tmp_path: Path):
    image_path = tmp_path / "inline.png"
    image_path.write_bytes(b"fake image")
    gateway = InlineUploadReopensInputGateway()

    evidence = gateway.set_file_input(
        browser_session(tmp_path),
        INLINE_FILE_SELECTORS,
        image_path,
        "正文配图",
        "inline",
    )

    assert "inline" in gateway.trigger_roles
    assert gateway.set_file_params == [
        {"files": [str(image_path.resolve())], "objectId": "stale-runtime-object"},
        {"files": [str(image_path.resolve())], "backendNodeId": 404},
    ]
    assert evidence["remote_url"] == "https://mmbiz.qpic.cn/uploaded-inline.jpg"


class MarkerBackendNodeFileInputGateway(ChromeCDPBrowserGateway):
    def __init__(self):
        super().__init__()
        self.set_file_params = None
        self.queried_selector = ""
        self.image_states = [
            {"urls": ["https://mmbiz.qpic.cn/old-cover.jpg"], "loading": False},
            {"urls": ["https://mmbiz.qpic.cn/old-cover.jpg", "https://mmbiz.qpic.cn/backend-cover.jpg"], "loading": False},
        ]

    def _remote_image_state(self, session, role):
        return self.image_states.pop(0)

    def _trigger_upload_entry(self, session, role):
        return True

    def _cdp_call(self, session, method, params=None):
        if method == "Runtime.evaluate":
            return {"result": {"objectId": "runtime-object-id"}}
        if method == "DOM.getDocument":
            return {"root": {"nodeId": 1}}
        if method == "DOM.querySelector":
            self.queried_selector = params["selector"]
            return {"nodeId": 77}
        if method == "DOM.describeNode":
            assert params == {"nodeId": 77}
            return {"node": {"backendNodeId": 909}}
        if method == "DOM.setFileInputFiles":
            self.set_file_params = params
            return {}
        return {}


def test_file_upload_marks_runtime_input_and_uploads_by_dom_backend_node_id(
    tmp_path: Path,
):
    image_path = tmp_path / "cover.png"
    image_path.write_bytes(b"fake image")
    gateway = MarkerBackendNodeFileInputGateway()

    evidence = gateway.set_file_input(
        browser_session(tmp_path),
        COVER_FILE_SELECTORS,
        image_path,
        "封面图",
        "cover",
    )

    assert 'input[type="file"][data-codex-upload-marker="' in gateway.queried_selector
    assert gateway.set_file_params == {
        "files": [str(image_path.resolve())],
        "backendNodeId": 909,
    }
    assert evidence["remote_url"] == "https://mmbiz.qpic.cn/backend-cover.jpg"


class ReplacedFileInputGateway(ChromeCDPBrowserGateway):
    def __init__(self):
        super().__init__()
        self.runtime_object_ids = ["old-object-id", "fresh-object-id"]
        self.set_file_params = []
        self.image_states = [
            {"urls": ["https://mmbiz.qpic.cn/old-cover.jpg"], "loading": False},
            {"urls": ["https://mmbiz.qpic.cn/old-cover.jpg", "https://mmbiz.qpic.cn/fresh-cover.jpg"], "loading": False},
        ]

    def _remote_image_state(self, session, role):
        return self.image_states.pop(0)

    def _trigger_upload_entry(self, session, role):
        return True

    def _cdp_call(self, session, method, params=None):
        if method == "Runtime.evaluate":
            return {"result": {"objectId": self.runtime_object_ids.pop(0)}}
        if method == "DOM.describeNode":
            object_id = params["objectId"]
            return {
                "node": {
                    "backendNodeId": 101 if object_id == "old-object-id" else 202
                }
            }
        if method == "DOM.setFileInputFiles":
            self.set_file_params.append(params)
            if params == {"files": [params["files"][0]], "backendNodeId": 101}:
                raise OSError("DOM.setFileInputFiles：Could not find object with given id")
            return {}
        return {}


def test_file_upload_re_resolves_input_when_wechat_replaces_cover_control(tmp_path: Path):
    image_path = tmp_path / "cover.png"
    image_path.write_bytes(b"fake image")
    gateway = ReplacedFileInputGateway()

    evidence = gateway.set_file_input(
        browser_session(tmp_path),
        COVER_FILE_SELECTORS,
        image_path,
        "封面图",
        "cover",
    )

    assert gateway.set_file_params == [
        {"files": [str(image_path.resolve())], "backendNodeId": 101},
        {"files": [str(image_path.resolve())], "backendNodeId": 202},
    ]
    assert evidence["remote_url"] == "https://mmbiz.qpic.cn/fresh-cover.jpg"


class ExpressionCaptureGateway(ChromeCDPBrowserGateway):
    def __init__(self):
        super().__init__()
        self.expressions = []

    def evaluate(self, session, expression):
        self.expressions.append(expression)
        if "candidateRecords" in expression:
            return {"ok": True, "count": 2, "selected": "main-editor"}
        return {"ok": False, "reason": "ambiguous", "count": 2}


def test_upload_area_diagnostics_records_cover_inputs_dialogs_and_iframes(tmp_path: Path):
    gateway = ExpressionCaptureGateway()

    gateway._upload_area_diagnostics(browser_session(tmp_path), "cover")

    expression = gateway.expressions[-1]
    assert "input[type=\"file\"]" in expression
    assert "iframe" in expression
    assert "role=\"dialog\"" in expression
    assert "domPath" in expression
    assert "封面" in expression
    assert "拖拽" in expression


def test_all_control_resolvers_deduplicate_matches_across_every_selector(tmp_path: Path):
    gateway = ExpressionCaptureGateway()
    session = browser_session(tmp_path)

    with pytest.raises(WeChatPublishingError, match="不唯一"):
        gateway._click_first(session, (".primary", "button.primary"), "按钮")
    with pytest.raises(WeChatPublishingError, match="不唯一"):
        gateway._set_value(session, ("#title", "textarea#title"), "标题", "标题")
    gateway._set_editor_html(session, "<p>正文</p>")

    expressions = gateway.expressions + [
        ChromeCDPBrowserGateway._file_input_expression(
            (".cover input", "input.cover")
        )
    ]
    assert "candidateRecords" in gateway.expressions[-1]
    assert all(
        "new Set(selectors.flatMap" in expression or "candidateRecords" in expression
        for expression in expressions
    )


def test_editor_resolver_scores_multiple_visible_editors_instead_of_rejecting(tmp_path: Path):
    gateway = ExpressionCaptureGateway()

    gateway._set_editor_html(browser_session(tmp_path), "<p>正文</p>")

    expression = gateway.expressions[-1]
    assert "matches.sort" in expression
    assert "priority" in expression
    assert "area" in expression


class UploadRecordingGateway(ChromeCDPBrowserGateway):
    def __init__(self):
        super().__init__()
        self.uploads = []
        self.editor_html = ""
        self.selected_from_body = False

    def _set_value(self, *args, **kwargs):
        return None

    def _set_editor_html(self, session, content_html):
        self.editor_html = content_html
        return None

    def _focus_editor_for_upload(self, session):
        return None

    def set_file_input(self, session, selectors, file_path, label, role):
        self.uploads.append((tuple(selectors), file_path, label, role))
        return {"remote_url": f"https://mmbiz.qpic.cn/{role}.jpg"}

    def _set_cover_from_article_image_picker(self, session, cover_path, target_image_url=""):
        self.selected_from_body = True
        self.target_image_url = target_image_url
        return {"remote_url": "https://mmbiz.qpic.cn/cover-from-body.jpg"}


def test_fill_article_embeds_cover_candidate_before_inline_images_and_selects_cover_from_body(tmp_path: Path):
    gateway = UploadRecordingGateway()
    payload = article_payload(tmp_path)

    gateway.fill_article(browser_session(tmp_path), payload)

    assert gateway.uploads == []
    assert gateway.selected_from_body is True
    assert gateway.target_image_url == ""
    assert "正文" in gateway.editor_html
    cover_index = gateway.editor_html.index("data:image/jpeg;base64,Y292ZXI=")
    inline_index = gateway.editor_html.index("data:image/png;base64,aW5saW5l")
    assert cover_index < inline_index
    assert "data-wechat-cover-candidate=\"true\"" in gateway.editor_html
    assert "inline.png" not in gateway.editor_html


class CoverPickerFromBodyGateway(ChromeCDPBrowserGateway):
    def __init__(self):
        super().__init__()
        self.uploads = []
        self.selected_from_body = False

    def set_file_input(self, session, selectors, file_path, label, role):
        self.uploads.append((tuple(selectors), file_path, label, role))
        return {"remote_url": f"https://mmbiz.qpic.cn/{role}.jpg"}

    def _focus_editor_for_upload(self, session):
        return None

    def _set_cover_from_article_image_picker(self, session, cover_path, target_image_url=""):
        self.selected_from_body = True
        self.target_image_url = target_image_url
        return {"remote_url": "https://mmbiz.qpic.cn/cover-from-body.jpg"}


def test_cover_upload_uses_wechat_body_image_picker_as_primary_flow(tmp_path: Path):
    payload = article_payload(tmp_path)
    gateway = CoverPickerFromBodyGateway()

    gateway.upload_article_assets(browser_session(tmp_path), payload)

    assert gateway.uploads == []
    assert gateway.selected_from_body is True
    assert gateway.target_image_url == ""


def test_cover_upload_allows_cover_only_article_by_using_cover_candidate(tmp_path: Path):
    payload = article_payload(tmp_path)
    payload["blocks"] = [
        {"type": "body", "content": "只有正文，没有正文配图", "image_path": "", "image_paths": []}
    ]
    gateway = CoverPickerFromBodyGateway()

    gateway.upload_article_assets(browser_session(tmp_path), payload)

    assert gateway.uploads == []
    assert gateway.selected_from_body is True
    assert gateway.target_image_url == ""


class StepwiseCoverPickerGateway(ChromeCDPBrowserGateway):
    def __init__(
        self,
        fail_marker="",
        cover_applied_after_next=True,
        delayed_entry_attempts=0,
        edit_cover_dialog_open=False,
        next_button_missing_when_edit_cover_open=False,
    ):
        super().__init__()
        self.fail_marker = fail_marker
        self.cover_applied_after_next = cover_applied_after_next
        self.delayed_entry_attempts = delayed_entry_attempts
        self.next_button_missing_when_edit_cover_open = next_button_missing_when_edit_cover_open
        self.entry_attempts = 0
        self.complete_clicked = False
        self.edit_cover_dialog_open = edit_cover_dialog_open
        self.edit_cover_confirm_clicked = False
        self.card_selected = False
        self.actions = []
        self.traces = []

    def _trace(self, event, **fields):
        self.traces.append((event, fields))

    def _trigger_upload_entry(self, session, role):
        self.actions.append(f"trigger:{role}")
        return True

    def evaluate(self, session, expression):
        markers = [
            "codex-cover-picker:from-article-entry",
            "codex-cover-picker:snapshot",
            "codex-cover-picker:select-image",
            "codex-cover-picker:selection-state",
            "codex-cover-picker:click-next",
            "codex-cover-picker:click-complete",
            "codex-cover-picker:edit-cover-dialog-state",
            "codex-cover-picker:click-edit-cover-confirm",
            "codex-cover-picker:article-editor-return-state",
            "codex-cover-picker:cover-applied",
        ]
        marker = next((item for item in markers if item in expression), "legacy")
        self.actions.append(marker)
        if self.fail_marker and marker == self.fail_marker:
            raise WeChatPublishingError("微信浏览器 CDP 会话失败：timed out")
        if marker == "codex-cover-picker:from-article-entry":
            self.entry_attempts += 1
            if self.entry_attempts <= self.delayed_entry_attempts:
                return {
                    "ok": False,
                    "reason": "from_article_entry_not_found",
                    "entryCandidates": [],
                }
            return {
                "ok": True,
                "clicked": True,
                "entryCandidates": [{"text": "从正文选择"}],
            }
        if marker == "codex-cover-picker:snapshot":
            return {
                "ok": True,
                "dialogFound": True,
                "imageCount": 2,
                "selectedIndex": 0,
                "candidates": [
                    {"index": 0, "src": "https://mmbiz.qpic.cn/cover.jpg"},
                    {"index": 1, "src": "https://mmbiz.qpic.cn/inline.jpg"},
                ],
                "dialogText": "选择图片 请从正文插入的图片和视频封面中选择封面",
            }
        if marker == "codex-cover-picker:selection-state":
            return {
                "ok": self.card_selected,
                "reason": "" if self.card_selected else "candidate_not_selected",
                "selectedIndex": 0,
                "selectedIndices": [0] if self.card_selected else [],
                "candidateSelected": self.card_selected,
                "nextButtons": [{"disabled": not self.card_selected}],
            }
        if marker == "codex-cover-picker:select-image":
            return {
                "ok": True,
                "selectedIndex": 0,
                "selectedBefore": False,
                "clickableRect": {"x": 100, "y": 100, "width": 120, "height": 120},
            }
        if marker == "codex-cover-picker:click-complete":
            self.complete_clicked = True
            return {"ok": True}
        if marker == "codex-cover-picker:click-next":
            if self.next_button_missing_when_edit_cover_open and self.edit_cover_dialog_open:
                return {
                    "ok": False,
                    "reason": "button_not_found",
                    "availableButtons": [{"text": "确认", "disabled": False}],
                }
            return {"ok": True}
        if marker == "codex-cover-picker:edit-cover-dialog-state":
            return {
                "ok": True,
                "dialogOpen": self.edit_cover_dialog_open,
                "dialogText": "编辑封面 确认" if self.edit_cover_dialog_open else "",
            }
        if marker == "codex-cover-picker:click-edit-cover-confirm":
            self.edit_cover_confirm_clicked = True
            self.complete_clicked = True
            self.edit_cover_dialog_open = False
            return {"ok": True, "text": "确认"}
        if marker == "codex-cover-picker:article-editor-return-state":
            return {
                "ok": True,
                "url": "https://mp.weixin.qq.com/cgi-bin/appmsg",
                "editorReady": True,
                "editCoverDialogOpen": False,
                "saveDraftButtonVisible": True,
                "publishButtonVisible": True,
                "visibleButtonTexts": ["保存为草稿", "发表"],
            }
        if marker == "codex-cover-picker:cover-applied":
            return {
                "ok": True,
                "coverApplied": (
                    (self.cover_applied_after_next and not self.edit_cover_dialog_open)
                    or self.complete_clicked
                ),
            }
        if marker == "legacy":
            raise WeChatPublishingError("unexpected legacy cover picker expression")
        return {"ok": True}

    def _dispatch_wechat_mouse_click(self, session, rect):
        self.actions.append("cdp:mouse-click")
        self.card_selected = True


class EditCoverUnverifiedGateway(StepwiseCoverPickerGateway):
    def __init__(self):
        super().__init__(edit_cover_dialog_open=True)

    def _wait_for_cover_picker_cover_applied(self, session, timeout=5):
        self.actions.append("codex-cover-picker:cover-applied")
        self._trace("upload.cover_picker.cover_applied_unverified", last_result={})
        return False


def test_cover_picker_from_article_image_runs_short_observable_steps(tmp_path: Path):
    gateway = StepwiseCoverPickerGateway()

    gateway._set_cover_from_article_image_picker(
        browser_session(tmp_path),
        str(tmp_path / "cover.jpg"),
    )

    assert gateway.actions == [
        "trigger:cover",
        "codex-cover-picker:from-article-entry",
        "codex-cover-picker:snapshot",
        "codex-cover-picker:select-image",
        "cdp:mouse-click",
        "codex-cover-picker:selection-state",
        "codex-cover-picker:selection-state",
        "codex-cover-picker:edit-cover-dialog-state",
        "codex-cover-picker:click-next",
        "codex-cover-picker:edit-cover-dialog-state",
        "codex-cover-picker:cover-applied",
    ]
    trace_events = [event for event, _ in gateway.traces]
    assert "upload.cover_picker.candidates" in trace_events
    assert "upload.cover_picker.cover_applied_after_next" in trace_events
    assert "upload.cover_picker_from_article_image.completed" in trace_events


def test_cover_picker_waits_for_delayed_from_article_menu_entry(tmp_path: Path):
    gateway = StepwiseCoverPickerGateway(delayed_entry_attempts=1)

    gateway._set_cover_from_article_image_picker(
        browser_session(tmp_path),
        str(tmp_path / "cover.jpg"),
    )

    assert gateway.entry_attempts == 2
    trace_events = [event for event, _ in gateway.traces]
    assert "upload.cover_picker.from_article_entry_wait" in trace_events


def test_cover_picker_waits_for_stable_image_selection_before_next(tmp_path: Path):
    gateway = StepwiseCoverPickerGateway()

    gateway._set_cover_from_article_image_picker(
        browser_session(tmp_path),
        str(tmp_path / "cover.jpg"),
    )

    next_click_index = gateway.actions.index("codex-cover-picker:click-next")
    selection_checks = gateway.actions[:next_click_index].count(
        "codex-cover-picker:selection-state"
    )

    assert selection_checks >= 2


def test_cover_picker_waits_three_seconds_after_image_selection_before_next(
    tmp_path: Path,
    monkeypatch,
):
    gateway = StepwiseCoverPickerGateway()

    def record_sleep(seconds):
        gateway.actions.append(f"sleep:{seconds}")

    monkeypatch.setattr(browser_gateway_module.time, "sleep", record_sleep)

    gateway._set_cover_from_article_image_picker(
        browser_session(tmp_path),
        str(tmp_path / "cover.jpg"),
    )

    next_click_index = gateway.actions.index("codex-cover-picker:click-next")
    assert "sleep:3.0" in gateway.actions[:next_click_index]
    wait_index = gateway.actions.index("sleep:3.0")
    assert gateway.actions[:wait_index].count("codex-cover-picker:selection-state") >= 2


def test_cover_picker_clicks_complete_when_next_does_not_apply_cover(tmp_path: Path):
    gateway = StepwiseCoverPickerGateway(cover_applied_after_next=False)

    gateway._set_cover_from_article_image_picker(
        browser_session(tmp_path),
        str(tmp_path / "cover.jpg"),
    )

    assert gateway.actions[:9] == [
        "trigger:cover",
        "codex-cover-picker:from-article-entry",
        "codex-cover-picker:snapshot",
        "codex-cover-picker:select-image",
        "cdp:mouse-click",
        "codex-cover-picker:selection-state",
        "codex-cover-picker:selection-state",
        "codex-cover-picker:edit-cover-dialog-state",
        "codex-cover-picker:click-next",
    ]
    assert "codex-cover-picker:click-complete" in gateway.actions
    first_cover_check = gateway.actions.index("codex-cover-picker:cover-applied")
    complete_click = gateway.actions.index("codex-cover-picker:click-complete")
    assert first_cover_check < complete_click
    assert gateway.actions[-1] == "codex-cover-picker:cover-applied"


def test_cover_picker_confirms_the_edit_cover_dialog_before_verifying_cover(tmp_path: Path):
    gateway = StepwiseCoverPickerGateway(edit_cover_dialog_open=True)

    gateway._set_cover_from_article_image_picker(
        browser_session(tmp_path),
        str(tmp_path / "cover.jpg"),
    )

    assert gateway.edit_cover_confirm_clicked is True
    assert "codex-cover-picker:click-edit-cover-confirm" in gateway.actions
    trace_events = [event for event, _ in gateway.traces]
    assert "upload.cover_picker.edit_cover_confirm_dispatched" in trace_events


def test_cover_picker_traces_article_editor_return_after_edit_cover_confirm(tmp_path: Path):
    gateway = StepwiseCoverPickerGateway(edit_cover_dialog_open=True)

    gateway._set_cover_from_article_image_picker(
        browser_session(tmp_path),
        str(tmp_path / "cover.jpg"),
    )

    trace_events = [event for event, _ in gateway.traces]
    assert (
        trace_events.index("upload.cover_picker.edit_cover_dialog_closed")
        < trace_events.index("upload.cover_picker.returned_to_editor_after_edit_cover_confirm")
        < trace_events.index("upload.cover_picker.cover_applied_after_edit_cover_confirm")
    )
    return_events = [
        fields
        for event, fields in gateway.traces
        if event == "upload.cover_picker.returned_to_editor_after_edit_cover_confirm"
    ]
    assert return_events[-1]["result"]["editorReady"] is True
    assert return_events[-1]["result"]["editCoverDialogOpen"] is False
    assert return_events[-1]["result"]["publishButtonVisible"] is True


def test_cover_picker_does_not_click_complete_after_edit_cover_confirm_when_cover_unverified(
    tmp_path: Path,
):
    gateway = EditCoverUnverifiedGateway()

    gateway._set_cover_from_article_image_picker(
        browser_session(tmp_path),
        str(tmp_path / "cover.jpg"),
    )

    assert gateway.edit_cover_confirm_clicked is True
    assert "codex-cover-picker:click-complete" not in gateway.actions


def test_cover_picker_skips_next_when_edit_cover_dialog_is_already_open(
    tmp_path: Path,
    monkeypatch,
):
    gateway = StepwiseCoverPickerGateway(
        edit_cover_dialog_open=True,
        next_button_missing_when_edit_cover_open=True,
    )

    monkeypatch.setattr(browser_gateway_module.time, "sleep", lambda seconds: None)

    gateway._set_cover_from_article_image_picker(
        browser_session(tmp_path),
        str(tmp_path / "cover.jpg"),
    )

    assert "codex-cover-picker:click-next" not in gateway.actions
    assert gateway.edit_cover_confirm_clicked is True
    trace_events = [event for event, _ in gateway.traces]
    assert "upload.cover_picker.next_already_on_edit_cover" in trace_events


def test_cover_picker_waits_two_seconds_before_edit_cover_confirm(
    tmp_path: Path,
    monkeypatch,
):
    gateway = StepwiseCoverPickerGateway(edit_cover_dialog_open=True)

    def record_sleep(seconds):
        gateway.actions.append(f"sleep:{seconds}")

    monkeypatch.setattr(browser_gateway_module.time, "sleep", record_sleep)

    gateway._set_cover_from_article_image_picker(
        browser_session(tmp_path),
        str(tmp_path / "cover.jpg"),
    )

    confirm_index = gateway.actions.index("codex-cover-picker:click-edit-cover-confirm")
    assert "sleep:2.0" in gateway.actions[:confirm_index]
    trace_events = [event for event, _ in gateway.traces]
    assert "upload.cover_picker.wait_before_edit_cover_confirm" in trace_events


def test_edit_cover_confirm_expression_is_scoped_to_the_wechat_edit_cover_dialog():
    expression = ChromeCDPBrowserGateway._edit_cover_confirm_expression()

    assert ".weui-desktop-dialog__wrp" in expression
    assert "编辑封面" in expression
    assert "weui-desktop-btn_primary" in expression
    assert "buttonText === '确认'" in expression


def test_cover_picker_traces_failed_step_when_cdp_times_out(tmp_path: Path):
    gateway = StepwiseCoverPickerGateway(
        fail_marker="codex-cover-picker:snapshot"
    )

    with pytest.raises(WeChatPublishingError, match="wait_image_candidates"):
        gateway._set_cover_from_article_image_picker(
            browser_session(tmp_path),
            str(tmp_path / "cover.jpg"),
        )

    failure_events = [
        fields
        for event, fields in gateway.traces
        if event == "upload.cover_picker_from_article_image.failed"
    ]
    assert failure_events
    assert failure_events[-1]["stage"] == "wait_image_candidates"
    assert "timed out" in failure_events[-1]["error"]


def test_cover_picker_candidates_ignore_dialog_container_nodes():
    source = ChromeCDPBrowserGateway._cover_picker_common_source()

    assert "'.appmsg_conent_img_container'," not in source
    assert "'.appmsg_content_img_container'," not in source
    assert "candidate.matches('img')" in source
    assert "candidate.querySelector?.('img, [style*=\"background-image\"]')" not in source


def test_cover_picker_uses_wechat_card_selected_state_for_confirmation():
    source = ChromeCDPBrowserGateway._cover_picker_common_source()
    expression = ChromeCDPBrowserGateway._cover_picker_select_image_expression(0)

    assert "li.appmsg_conent_img_item" in source
    assert "classList.contains('selected')" in source
    assert "clickableRect" in expression


def test_cover_picker_button_expression_reports_visible_button_diagnostics():
    expression = ChromeCDPBrowserGateway._cover_picker_click_button_expression(
        ("下一步",),
        "codex-cover-picker:click-next",
    )

    assert "availableButtons" in expression
    assert "disabled" in expression
    assert "ariaDisabled" in expression
    assert "buttonClass" in expression


def test_cover_picker_from_article_entry_does_not_click_page_containers():
    expression = ChromeCDPBrowserGateway._cover_picker_from_article_entry_expression()

    assert "querySelectorAll('button, a, [role=\"button\"], li, label, span, div')" not in expression
    assert "text.length <= 24" in expression
    assert "text === '从正文选择'" in expression


def test_cover_picker_from_article_entry_returns_rect_and_diagnostics():
    expression = ChromeCDPBrowserGateway._cover_picker_from_article_entry_expression()

    assert "entryCandidates" in expression
    assert "menuRootCount" in expression
    assert "visibleMenuTexts" in expression
    assert "rect:" in expression
    assert "clickableRect" in expression
    assert "source: 'precise-menu-item'" in expression


def test_cover_picker_from_article_entry_prefers_wechat_cover_menu_selector():
    expression = ChromeCDPBrowserGateway._cover_picker_from_article_entry_expression()

    assert ".pop-opr__group-select-cover .js_selectCoverFromContent" in expression
    assert "directWechatEntries" in expression
    assert "source: 'wechat-cover-menu'" in expression


def test_cover_upload_trigger_reports_the_selected_target():
    expression = ChromeCDPBrowserGateway._upload_trigger_expression("cover")

    assert "selectedSelector" in expression
    assert "selectedText" in expression
    assert "selectedClassName" in expression
    assert "triggerCandidates" in expression


class RemoteImageGateway(ChromeCDPBrowserGateway):
    def __init__(self, image_states):
        super().__init__(upload_timeout=0)
        self.image_states = list(image_states)

    def _remote_image_state(self, session, role):
        return self.image_states.pop(0)


def test_upload_wait_requires_new_remote_wechat_image(tmp_path: Path):
    gateway = RemoteImageGateway(
        [{"urls": ["https://mmbiz.qpic.cn/new-image.jpg"], "loading": False}]
    )

    evidence = gateway._wait_for_uploaded_image(
        browser_session(tmp_path), "cover", {"https://mmbiz.qpic.cn/old-image.jpg"}
    )

    assert evidence["remote_url"] == "https://mmbiz.qpic.cn/new-image.jpg"


def test_upload_wait_rejects_missing_remote_image(tmp_path: Path):
    gateway = RemoteImageGateway([{"urls": [], "loading": False}])

    with pytest.raises(WeChatPublishingError, match="未确认上传完成"):
        gateway._wait_for_uploaded_image(browser_session(tmp_path), "inline", set())


class AmbiguousDomGateway(ChromeCDPBrowserGateway):
    def evaluate(self, session, expression):
        return {"ok": False, "reason": "ambiguous", "count": 2}


def test_dom_actions_reject_ambiguous_elements(tmp_path: Path):
    gateway = AmbiguousDomGateway()

    with pytest.raises(WeChatPublishingError, match="不唯一"):
        gateway._click_first(browser_session(tmp_path), ("button.send",), "发表按钮")

    with pytest.raises(WeChatPublishingError, match="不唯一"):
        gateway._set_value(browser_session(tmp_path), ("#title",), "标题", "标题")


class FakeProcess:
    def __init__(self):
        self.running = True
        self.wait_calls = 0
        self.terminate_calls = 0
        self.kill_calls = 0
        self.stderr = None

    def poll(self):
        return None if self.running else 0

    def wait(self, timeout=None):
        self.wait_calls += 1
        self.running = False
        return 0

    def terminate(self):
        self.terminate_calls += 1
        self.running = False

    def kill(self):
        self.kill_calls += 1
        self.running = False


class ExitedProcess(FakeProcess):
    def __init__(self, stderr_text: str):
        super().__init__()
        self.running = False
        self.stderr = Mock()
        self.stderr.read.return_value = stderr_text


class ClosingGateway(ChromeCDPBrowserGateway):
    def _cdp_call(self, session, method, params=None):
        raise OSError("closed")


def test_browser_close_waits_for_child_exit_after_cdp_failure(tmp_path: Path):
    process = FakeProcess()
    session = browser_session(tmp_path)
    session.process = process

    ClosingGateway().close(session)

    assert process.terminate_calls == 1
    assert process.wait_calls == 1


def test_close_session_targets_closes_matching_tabs_and_current_target(tmp_path: Path):
    class TargetClosingGateway(ChromeCDPBrowserGateway):
        def __init__(self):
            super().__init__()
            self.closed_target_ids = []

        def _targets(self, port):
            return [
                {
                    "id": "current-page",
                    "type": "page",
                    "url": "https://creator.xiaohongshu.com/publish/publish?from=homepage",
                },
                {
                    "id": "matching-success",
                    "type": "page",
                    "url": "https://creator.xiaohongshu.com/publish/success?note=1",
                },
                {
                    "id": "other-page",
                    "type": "page",
                    "url": "https://creator.xiaohongshu.com/new/home",
                },
            ]

        def _cdp_call(self, session, method, params=None):
            assert method == "Target.closeTarget"
            self.closed_target_ids.append((params or {})["targetId"])
            return {}

    session = browser_session(tmp_path)
    session.target_id = "current-page"
    gateway = TargetClosingGateway()

    gateway.close_session_targets(
        session,
        hostnames=("creator.xiaohongshu.com",),
        paths=("/publish/publish", "/publish/success"),
    )

    assert gateway.closed_target_ids == ["matching-success", "current-page"]


def test_close_session_targets_can_keep_current_target(tmp_path: Path):
    class TargetClosingGateway(ChromeCDPBrowserGateway):
        def __init__(self):
            super().__init__()
            self.closed_target_ids = []

        def _targets(self, port):
            return [
                {
                    "id": "current-page",
                    "type": "page",
                    "url": "https://creator.xiaohongshu.com/publish/publish?from=homepage",
                },
                {
                    "id": "old-page",
                    "type": "page",
                    "url": "https://creator.xiaohongshu.com/publish/publish?from=homepage",
                },
            ]

        def _cdp_call(self, session, method, params=None):
            assert method == "Target.closeTarget"
            self.closed_target_ids.append((params or {})["targetId"])
            return {}

    session = browser_session(tmp_path)
    session.target_id = "current-page"
    gateway = TargetClosingGateway()

    gateway.close_session_targets(
        session,
        hostnames=("creator.xiaohongshu.com",),
        paths=("/publish/publish",),
        include_current=False,
    )

    assert gateway.closed_target_ids == ["old-page"]


def test_wait_for_target_includes_chrome_stderr_when_process_exits():
    process = ExitedProcess("profile is locked")

    with pytest.raises(WeChatPublishingError, match="profile is locked"):
        ChromeCDPBrowserGateway(startup_timeout=0.01)._wait_for_target(9222, process)


def test_open_profile_cleans_process_when_navigation_fails(monkeypatch, tmp_path: Path):
    profile_path = tmp_path / "profile"
    profile_path.mkdir()
    chrome_path = tmp_path / "Chrome"
    chrome_path.write_bytes(b"binary")
    process = FakeProcess()

    class NavigationFailureGateway(ClosingGateway):
        def _free_port(self):
            return 9222

        def _wait_for_target(self, port, child):
            return {
                "id": "page-1",
                "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/page-1",
                "url": "https://mp.weixin.qq.com/",
            }

        def navigate(self, session, url):
            raise WeChatPublishingError("navigation failed")

    monkeypatch.setattr(
        "app.publishing.browser_gateway.subprocess.Popen", lambda *args, **kwargs: process
    )

    with pytest.raises(WeChatPublishingError, match="navigation failed"):
        NavigationFailureGateway(chrome_path=chrome_path).open_profile(
            profile_path, "https://mp.weixin.qq.com/"
        )

    assert process.terminate_calls == 1
    assert process.wait_calls == 1


class ScreenshotGateway(ChromeCDPBrowserGateway):
    def _cdp_call(self, session, method, params=None):
        assert method == "Page.captureScreenshot"
        return {"data": "cG5n"}


def test_screenshots_are_collision_safe_and_private(tmp_path: Path):
    gateway = ScreenshotGateway()
    session = browser_session(tmp_path)

    first = Path(gateway.screenshot(session))
    second = Path(gateway.screenshot(session))
    try:
        assert first != second
        assert first.stat().st_mode & 0o777 == 0o600
        assert second.stat().st_mode & 0o777 == 0o600
    finally:
        first.unlink(missing_ok=True)
        second.unlink(missing_ok=True)


class PublishMonitorGateway(ChromeCDPBrowserGateway):
    def _cdp_call(self, session, method, params=None):
        if method == "Runtime.evaluate":
            expression = str((params or {}).get("expression") or "")
            if "codex-publish-monitor:install" in expression:
                return {"result": {"value": {"ok": True, "installed": True}}}
            if "codex-publish-monitor:snapshot" in expression:
                return {
                    "result": {
                        "value": {
                            "ok": True,
                            "url": "https://mp.weixin.qq.com/cgi-bin/appmsg?token=secret-token",
                            "title": "公众号",
                            "readyState": "complete",
                            "buttons": [
                                {
                                    "text": "保存为草稿",
                                    "disabled": False,
                                    "rect": {"x": 10, "y": 20, "width": 80, "height": 32},
                                }
                            ],
                            "cover": {
                                "pickerOpen": True,
                                "selectedCount": 0,
                                "candidateCount": 2,
                            },
                            "editor": {"imageCount": 2, "textLength": 120},
                            "trace": {
                                "requests": [
                                    {
                                        "kind": "fetch",
                                        "url": "https://mp.weixin.qq.com/cgi-bin/save?token=secret-token",
                                        "status": 500,
                                    }
                                ],
                                "console": [{"level": "error", "message": "boom"}],
                                "errors": [{"message": "bad"}],
                            },
                        }
                    }
                }
            return {"result": {"value": {"ok": True}}}
        if method == "Page.captureScreenshot":
            return {"data": "cG5n"}
        raise AssertionError(method)


def test_publish_monitor_captures_page_state_and_private_screenshot(tmp_path: Path):
    gateway = PublishMonitorGateway()
    session = browser_session(tmp_path)
    trace_path = tmp_path / "publish-job-1.jsonl"
    gateway.set_trace_context(1, str(trace_path))

    gateway.install_page_monitor(session, "编辑封面")
    gateway.capture_debug_state(session, "编辑封面确认后")
    gateway.clear_trace_context()

    events = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(event["event"] == "monitor.installed" for event in events)
    snapshot = next(event for event in events if event["event"] == "monitor.snapshot")
    assert snapshot["label"] == "编辑封面确认后"
    assert snapshot["state"]["url"] == "https://mp.weixin.qq.com/cgi-bin/appmsg"
    assert (
        snapshot["state"]["trace"]["requests"][0]["url"]
        == "https://mp.weixin.qq.com/cgi-bin/save"
    )

    screenshot = next(event for event in events if event["event"] == "monitor.screenshot")
    screenshot_path = Path(screenshot["path"])
    assert screenshot_path.is_file()
    assert screenshot_path.stat().st_mode & 0o777 == 0o600
    assert screenshot_path.read_bytes() == b"png"
    assert "secret-token" not in trace_path.read_text(encoding="utf-8")
    assert "cG5n" not in trace_path.read_text(encoding="utf-8")


class FakeTimer:
    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.callback()


class PendingGateway(ChromeCDPBrowserGateway):
    def __init__(self, timers):
        super().__init__(pending_timeout=60, timer_factory=lambda delay, callback: timers.append(FakeTimer(delay, callback)) or timers[-1])
        self.closed = []

    def close(self, session):
        self.closed.append(session)

    def _current_url(self, session):
        return session.url


def test_pending_confirmation_uses_opaque_handle_and_expires_with_cleanup(tmp_path: Path):
    timers = []
    gateway = PendingGateway(timers)
    session = browser_session(tmp_path, "https://mp.weixin.qq.com/cgi-bin/masssendpage?token=secret")
    baseline = {"baseline_id": "baseline-1", "receipt_ids": [], "action": "publish"}

    handle = gateway.retain_confirmation(session, "4", "浏览器发布测试", baseline)

    assert len(handle) >= 32
    assert all(value not in handle for value in ("9222", "page-1", "profile"))
    assert timers[0].started is True
    timers[0].fire()
    assert gateway.closed == [session]


def test_pending_confirmation_expiry_notifies_persisted_job_owner(tmp_path: Path):
    timers = []
    gateway = PendingGateway(timers)
    session = browser_session(tmp_path, "https://mp.weixin.qq.com/cgi-bin/masssendpage")
    callback = Mock()
    handle = gateway.retain_confirmation(
        session, "4", "浏览器发布测试", {"baseline_id": "baseline-1"}
    )

    gateway.bind_confirmation_expiry(handle, callback)
    timers[0].fire()

    callback.assert_called_once_with()
    assert gateway.closed == [session]


def test_pending_confirmation_resume_is_bound_to_account_profile_and_host(tmp_path: Path):
    timers = []
    gateway = PendingGateway(timers)
    session = browser_session(tmp_path, "https://mp.weixin.qq.com/cgi-bin/masssendpage")
    baseline = {"baseline_id": "baseline-1", "receipt_ids": [], "action": "publish"}
    handle = gateway.retain_confirmation(session, "4", "浏览器发布测试", baseline)

    with pytest.raises(WeChatPublishingError, match="账号不匹配"):
        gateway.resume_profile(tmp_path.resolve(), handle, "5")

    session.url = "https://example.com/"
    with pytest.raises(WeChatPublishingError, match="页面不是 mp.weixin.qq.com"):
        gateway.resume_profile(tmp_path.resolve(), handle, "4")


def test_pending_confirmation_can_be_cancelled_and_closes_browser(tmp_path: Path):
    timers = []
    gateway = PendingGateway(timers)
    session = browser_session(tmp_path)
    handle = gateway.retain_confirmation(
        session, "4", "浏览器发布测试", {"baseline_id": "baseline-1"}
    )

    gateway.cancel_pending(handle, "4")

    assert timers[0].cancelled is True
    assert gateway.closed == [session]
