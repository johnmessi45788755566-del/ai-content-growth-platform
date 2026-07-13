import time
from pathlib import Path

import pytest

from app.publishing.browser_gateway import (
    COVER_FILE_SELECTORS,
    INLINE_FILE_SELECTORS,
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

    def open_article_editor(self, session):
        self.actions.append("open_article_editor")

    def fill_article(self, session, payload):
        self.actions.append("fill_article")
        self.filled_payload = payload

    def capture_baseline(self, session, title, action):
        self.actions.append("capture_baseline")
        return {
            "baseline_id": "baseline-1",
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
        "platform_url": "https://mp.weixin.qq.com/cgi-bin/appmsg?action=list&token=secret-token",
        "baseline_id": "baseline-1",
        "receipt": {"id": "draft-42", "title": "浏览器发布测试", "source": "dom"},
        "post_action_signal": {"kind": "success_message", "observed_after_action": True},
    }


def published_outcome():
    return {
        "status": "published",
        "publish_id": "publish-88",
        "platform_url": "https://mp.weixin.qq.com/cgi-bin/masssendpage?token=secret-token",
        "baseline_id": "baseline-1",
        "receipt": {"id": "publish-88", "title": "浏览器发布测试", "source": "dom"},
        "post_action_signal": {"kind": "success_message", "observed_after_action": True},
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
        "corroboration": "success_message",
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


def test_wechat_browser_publisher_pauses_for_operator_confirmation(tmp_path: Path):
    gateway = FakeWeChatGateway(
        {
            "status": "awaiting_confirmation",
            "platform_url": "https://mp.weixin.qq.com/cgi-bin/masssendpage?token=secret-token",
            "baseline_id": "baseline-1",
            "confirmation": {"kind": "operator_qr", "screenshot_available": True},
        }
    )
    result = WeChatBrowserPublisher(gateway=gateway).publish(
        article_payload(tmp_path), browser_account(tmp_path), action="publish"
    )

    assert result.status == "awaiting_confirmation"
    assert result.requires_confirmation is True
    assert result.platform_url == "https://mp.weixin.qq.com/cgi-bin/masssendpage"
    assert result.response == {
        "source": "wechat_platform",
        "event": "operator_confirmation_required",
        "confirmation": "operator_qr",
        "job_context": {"resume_handle": "opaque-resume-handle"},
    }
    public_text = str(result.response)
    for forbidden in ("debug_port", "target_id", "profile_path", "session_id", "secret-token"):
        assert forbidden not in public_text
    assert gateway.closed_sessions == []


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
                "post_action_signal": {"kind": "success_message", "observed_after_action": True},
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
                "post_action_signal": {"kind": "success_message", "observed_after_action": True},
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


def browser_session(tmp_path: Path, url="https://mp.weixin.qq.com/"):
    return BrowserSession(
        session_id="session-1",
        profile_path=tmp_path.resolve(),
        port=9222,
        target_id="page-1",
        websocket_url="ws://127.0.0.1/devtools/page/page-1",
        url=url,
    )


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
            {"kind": "success_message", "observed_after_baseline": True}
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
            {"kind": "success_message", "observed_after_baseline": True}
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
                    {"kind": "success_message", "observed_after_baseline": True}
                ],
            },
        ]
    )
    session = browser_session(tmp_path)
    baseline = gateway.capture_baseline(session, "浏览器发布测试", "draft")

    with pytest.raises(WeChatPublishingError, match="未返回可验证的草稿结果"):
        gateway.verify_draft(session, "浏览器发布测试", baseline)


def test_cover_and_inline_upload_selectors_are_disjoint_and_not_generic():
    assert set(COVER_FILE_SELECTORS).isdisjoint(INLINE_FILE_SELECTORS)
    assert "input[type='file'][accept*='image']" not in COVER_FILE_SELECTORS
    assert "input[type='file'][accept*='image']" not in INLINE_FILE_SELECTORS


def test_file_upload_locator_requires_unique_visible_image_file_input():
    expression = ChromeCDPBrowserGateway._file_input_expression(COVER_FILE_SELECTORS)

    assert "matches.length > 1" in expression
    assert "node.type !== 'file'" in expression
    assert "visibleControl(node)" in expression
    assert "accept.includes('image')" in expression


class UploadRecordingGateway(ChromeCDPBrowserGateway):
    def __init__(self):
        super().__init__()
        self.uploads = []

    def _set_value(self, *args, **kwargs):
        return None

    def _set_editor_html(self, *args, **kwargs):
        return None

    def set_file_input(self, session, selectors, file_path, label, role):
        self.uploads.append((tuple(selectors), file_path, label, role))
        return {"remote_url": f"https://mmbiz.qpic.cn/{role}.jpg"}


def test_fill_article_uses_distinct_verified_cover_and_inline_upload_roles(tmp_path: Path):
    gateway = UploadRecordingGateway()
    payload = article_payload(tmp_path)

    gateway.fill_article(browser_session(tmp_path), payload)

    assert [upload[3] for upload in gateway.uploads] == ["cover", "inline"]
    assert gateway.uploads[0][0] == COVER_FILE_SELECTORS
    assert gateway.uploads[1][0] == INLINE_FILE_SELECTORS


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
