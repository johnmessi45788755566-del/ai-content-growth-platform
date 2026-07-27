from pathlib import Path

import pytest

from app.publishing.models import PublishResult
from app.publishing.router import PlatformPublisherRouter
from app.publishing.xiaohongshu_patchright import (
    XHS_IMAGE_PUBLISH_URL,
    XiaoHongShuPatchrightPublisher,
    XiaoHongShuPublishingError,
)


class FakeLocator:
    def __init__(self, page, selector, count=1):
        self.page = page
        self.selector = selector
        self.count_value = count

    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    def count(self):
        return self.count_value

    def wait_for(self, **kwargs):
        self.page.actions.append(("wait_for", self.selector, kwargs))

    def set_input_files(self, files):
        self.page.actions.append(("set_input_files", self.selector, list(files)))
        self.page.files = list(files)

    def fill(self, value):
        self.page.actions.append(("fill", self.selector, value))
        if "填写标题" in self.selector:
            self.page.title = value

    def click(self):
        self.page.actions.append(("locator_click", self.selector))
        if "发布" in self.selector:
            self.page.url = "https://creator.xiaohongshu.com/publish/success?note=1"
        elif "暂存离开" in self.selector:
            self.page.url = "https://creator.xiaohongshu.com/new/home"


class FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def insert_text(self, text):
        self.page.actions.append(("keyboard_insert_text", text))
        self.page.note = text

    def type(self, text):
        self.insert_text(text)


class FakePage:
    def __init__(self):
        self.url = "about:blank"
        self.actions = []
        self.keyboard = FakeKeyboard(self)
        self.title = ""
        self.note = ""
        self.files = []

    def goto(self, url, **kwargs):
        self.actions.append(("goto", url, kwargs))
        self.url = XHS_IMAGE_PUBLISH_URL

    def wait_for_url(self, pattern, **kwargs):
        self.actions.append(("wait_for_url", pattern, kwargs))

    def wait_for_timeout(self, timeout):
        self.actions.append(("wait_for_timeout", timeout))

    def wait_for_function(self, expression, **kwargs):
        self.actions.append(("wait_for_function", expression, kwargs))

    def locator(self, selector):
        self.actions.append(("locator", selector))
        return FakeLocator(self, selector)

    def evaluate(self, expression):
        self.actions.append(("evaluate", expression))
        if "location.href" in expression:
            return self.url
        if "button:has-text" in expression:
            return {
                "publishCount": 1,
                "draftCount": 1,
                "buttons": ["暂存离开", "发布"],
            }
        return True

    def screenshot(self, **kwargs):
        self.actions.append(("screenshot", kwargs))

    def close(self):
        self.actions.append(("page_close",))


class FakeContext:
    def __init__(self, cookie_value="valid"):
        self.page = FakePage()
        self.pages = []
        self.closed = False
        self.cookie_value = cookie_value

    def new_page(self):
        self.pages.append(self.page)
        return self.page

    def cookies(self):
        return [{"name": "access-token-creator.xiaohongshu.com", "value": self.cookie_value}]

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, context):
        self.context = context
        self.launch_calls = []

    def launch_persistent_context(self, user_data_dir, **kwargs):
        self.launch_calls.append((user_data_dir, kwargs))
        return self.context


class FakePlaywright:
    def __init__(self, context=None):
        self.context = context or FakeContext()
        self.chromium = FakeChromium(self.context)
        self.stopped = False

    def stop(self):
        self.stopped = True


def fake_account(profile_path: Path):
    return {
        "platform": "小红书",
        "auth_status": "已授权",
        "profile_path": str(profile_path),
        "name": "找房子的派大星",
    }


def fake_payload(image_path: Path, note="小红书正文"):
    return {
        "title": "小红书图文发布测试",
        "note": note,
        "tags": [],
        "image_paths": [str(image_path)],
    }


def make_publisher(playwright):
    return XiaoHongShuPatchrightPublisher(playwright_factory=lambda: playwright)


def test_patchright_publisher_clicks_publish_with_locator_and_never_cdp_mouse(tmp_path: Path):
    profile = tmp_path / "profile"
    profile.mkdir()
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"fake")
    playwright = FakePlaywright()
    progress = []

    result = make_publisher(playwright).publish_with_progress(
        fake_payload(image),
        fake_account(profile),
        "publish",
        progress.append,
    )

    actions = playwright.context.page.actions
    assert result.status == "published"
    assert ("locator_click", 'button:has-text("发布")') in actions
    assert not any("dispatchMouseEvent" in str(action) or "cdp_mouse" in str(action) for action in actions)
    assert progress == ["uploading_assets", "filling_content", "publishing"]
    assert playwright.context.closed is True
    assert playwright.stopped is True


def test_patchright_publisher_clicks_draft_with_locator_and_never_publish_button(tmp_path: Path):
    profile = tmp_path / "profile"
    profile.mkdir()
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"fake")
    playwright = FakePlaywright()

    result = make_publisher(playwright).publish(
        fake_payload(image),
        fake_account(profile),
        "draft",
    )

    actions = playwright.context.page.actions
    assert result.status == "platform_draft"
    assert result.platform_url == "https://creator.xiaohongshu.com/new/home"
    assert ("locator_click", 'button:has-text("暂存离开")') in actions
    assert ("locator_click", 'button:has-text("发布")') not in actions
    assert not any("dispatchMouseEvent" in str(action) or "cdp_mouse" in str(action) for action in actions)


def test_patchright_publisher_uploads_images_with_set_input_files(tmp_path: Path):
    profile = tmp_path / "profile"
    profile.mkdir()
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"fake")
    playwright = FakePlaywright()

    make_publisher(playwright).publish(fake_payload(image), fake_account(profile), "publish")

    assert playwright.context.page.files == [str(image.resolve())]
    assert any(action[0] == "set_input_files" for action in playwright.context.page.actions)


def test_patchright_publisher_rejects_over_limit_note_before_opening_browser(tmp_path: Path):
    profile = tmp_path / "profile"
    profile.mkdir()
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"fake")
    playwright = FakePlaywright()

    with pytest.raises(XiaoHongShuPublishingError, match="超过 1000"):
        make_publisher(playwright).publish(
            fake_payload(image, note="字" * 1001),
            fake_account(profile),
            "publish",
        )

    assert playwright.context.page.actions == []


def test_patchright_publisher_verifies_login_with_publish_page_preflight(tmp_path: Path):
    profile = tmp_path / "profile"
    profile.mkdir()
    playwright = FakePlaywright()

    result = make_publisher(playwright).verify_login(fake_account(profile))

    actions = playwright.context.page.actions
    assert result["verified"] is True
    assert result["platform_url"].startswith("https://creator.xiaohongshu.com/publish/publish")
    assert ("goto", XHS_IMAGE_PUBLISH_URL, {"wait_until": "domcontentloaded", "timeout": 60000}) in actions
    assert playwright.context.closed is True
    assert playwright.stopped is True


def test_patchright_publisher_accepts_login_cookie_marker_even_when_cookie_value_is_empty(tmp_path: Path):
    profile = tmp_path / "profile"
    profile.mkdir()
    playwright = FakePlaywright(FakeContext(cookie_value=""))

    result = make_publisher(playwright).verify_login(fake_account(profile))

    assert result["verified"] is True
    assert result["platform_url"].startswith("https://creator.xiaohongshu.com/publish/publish")


def test_default_router_uses_patchright_xiaohongshu_publisher():
    router = PlatformPublisherRouter()

    assert isinstance(router.publishers["小红书"], XiaoHongShuPatchrightPublisher)
