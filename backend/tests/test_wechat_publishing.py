from pathlib import Path

import pytest

from app.publishing.browser_gateway import BrowserSession, ChromeCDPBrowserGateway
from app.publishing.models import PublishResult
from app.publishing.wechat import WeChatBrowserPublisher, WeChatPublishingError


class FakeWeChatGateway:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes) if isinstance(outcomes, list) else [outcomes]
        self.actions = []
        self.closed_sessions = []
        self.filled_payload = None

    def open_profile(self, profile_path, url):
        self.actions.append(("open_profile", profile_path, url))
        return {"session_id": "session-1", "url": url}

    def resume_profile(self, profile_path, job_context):
        self.actions.append(("resume_profile", profile_path, job_context))
        return {"session_id": "session-1", "url": job_context["platform_url"]}

    def require_authenticated(self, session):
        self.actions.append("require_authenticated")

    def open_article_editor(self, session):
        self.actions.append("open_article_editor")

    def fill_article(self, session, payload):
        self.actions.append("fill_article")
        self.filled_payload = payload

    def click_save_draft(self, session):
        self.actions.append("click_save_draft")

    def click_publish(self, session):
        self.actions.append("click_publish")

    def verify_draft(self, session, title):
        self.actions.append("verify_draft")
        return self.outcomes.pop(0)

    def verify_publish_or_confirmation(self, session, title):
        self.actions.append("verify_publish_or_confirmation")
        return self.outcomes.pop(0)

    def session_context(self, session):
        return {
            "session_id": session["session_id"],
            "platform_url": session["url"],
        }

    def close(self, session):
        self.closed_sessions.append(session)


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


def browser_account(tmp_path: Path):
    profile_path = tmp_path / "profile"
    profile_path.mkdir()
    return {
        "id": 4,
        "platform": "微信公众号",
        "auth_status": "已授权",
        "profile_path": str(profile_path),
    }


def draft_outcome():
    return {
        "status": "platform_draft",
        "draft_id": "draft-42",
        "platform_url": "https://mp.weixin.qq.com/cgi-bin/appmsg?action=list",
        "response": {
            "selector": "[data-draft-id]",
            "attribute": "data-draft-id",
            "value": "draft-42",
        },
    }


def published_outcome():
    return {
        "status": "published",
        "publish_id": "publish-88",
        "platform_url": "https://mp.weixin.qq.com/cgi-bin/masssendpage",
        "response": {
            "selector": "[data-publish-id]",
            "attribute": "data-publish-id",
            "value": "publish-88",
        },
    }


def test_wechat_browser_publisher_creates_verified_platform_draft(tmp_path: Path):
    gateway = FakeWeChatGateway(draft_outcome())
    result = WeChatBrowserPublisher(gateway=gateway).publish(
        article_payload(tmp_path), browser_account(tmp_path), action="draft"
    )

    assert isinstance(result, PublishResult)
    assert result.status == "platform_draft"
    assert result.platform_draft_id == "draft-42"
    assert result.platform_url.startswith("https://mp.weixin.qq.com/")
    assert result.response["value"] == "draft-42"
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


def test_wechat_browser_publisher_returns_verified_publish_receipt(tmp_path: Path):
    gateway = FakeWeChatGateway(published_outcome())
    result = WeChatBrowserPublisher(gateway=gateway).publish(
        article_payload(tmp_path), browser_account(tmp_path), action="publish"
    )

    assert result.status == "published"
    assert result.platform_publish_id == "publish-88"
    assert result.platform_url.startswith("https://mp.weixin.qq.com/")
    assert result.response["value"] == "publish-88"
    assert gateway.actions[-1] == "verify_publish_or_confirmation"
    assert len(gateway.closed_sessions) == 1


def test_wechat_browser_publisher_pauses_for_operator_confirmation(tmp_path: Path):
    gateway = FakeWeChatGateway(
        {
            "status": "awaiting_confirmation",
            "platform_url": "https://mp.weixin.qq.com/cgi-bin/masssendpage",
            "response": {"selector": ".js_qrcode", "screenshot": "/tmp/wechat-confirm.png"},
        }
    )
    result = WeChatBrowserPublisher(gateway=gateway).publish(
        article_payload(tmp_path), browser_account(tmp_path), action="publish"
    )

    assert result.status == "awaiting_confirmation"
    assert result.requires_confirmation is True
    assert result.response["screenshot"] == "/tmp/wechat-confirm.png"
    assert result.response["job_context"]["session_id"] == "session-1"
    assert gateway.closed_sessions == []


def test_wechat_browser_publisher_resumes_preserved_confirmation_session(tmp_path: Path):
    gateway = FakeWeChatGateway(published_outcome())
    account = browser_account(tmp_path)
    result = WeChatBrowserPublisher(gateway=gateway).resume(
        account,
        {
            "session_id": "session-1",
            "platform_url": "https://mp.weixin.qq.com/cgi-bin/masssendpage",
            "title": "浏览器发布测试",
        },
    )

    assert result.status == "published"
    assert result.platform_publish_id == "publish-88"
    assert gateway.actions[0][0] == "resume_profile"
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
                "response": {"notice": "saved"},
            },
            "草稿 ID",
        ),
        (
            {
                "status": "published",
                "publish_id": "publish-88",
                "platform_url": "https://example.com/receipt",
                "response": {"value": "publish-88"},
            },
            "微信页面 URL",
        ),
        (
            {
                "status": "published",
                "publish_id": "publish-88",
                "platform_url": "https://mp.weixin.qq.com/",
                "response": {},
            },
            "页面证据",
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


def test_browser_gateway_rejects_profile_with_only_prelogin_cookies(monkeypatch, tmp_path: Path):
    class ExpiredCookieGateway(ChromeCDPBrowserGateway):
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

    ticks = iter((100.0, 101.0, 113.0))
    monkeypatch.setattr("app.publishing.browser_gateway.time.time", lambda: next(ticks))
    monkeypatch.setattr("app.publishing.browser_gateway.time.sleep", lambda _: None)
    session = BrowserSession(
        session_id="expired-session",
        profile_path=tmp_path,
        port=9222,
        target_id="page-1",
        websocket_url="ws://127.0.0.1/devtools/page/page-1",
    )

    with pytest.raises(WeChatPublishingError, match="登录态失效，请重新扫码"):
        ExpiredCookieGateway().require_authenticated(session)
