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

    def click(self, **kwargs):
        if kwargs:
            self.page.actions.append(("locator_click", self.selector, kwargs))
        else:
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


class UploadEntryLocator(FakeLocator):
    def count(self):
        if "input" in self.selector and (
            "file" in self.selector or "upload-input" in self.selector
        ):
            return 1 if self.page.upload_input_available else 0
        if "上传图片" in self.selector or "upload-button" in self.selector:
            return 1
        return super().count()

    def wait_for(self, **kwargs):
        if (
            "input" in self.selector
            and ("file" in self.selector or "upload-input" in self.selector)
            and not self.page.upload_input_available
        ):
            raise TimeoutError("file input is not attached before upload entry click")
        super().wait_for(**kwargs)

    def click(self):
        super().click()
        if "上传图片" in self.selector or "upload-button" in self.selector:
            self.page.upload_input_available = True


class UploadEntryPage(FakePage):
    def __init__(self):
        super().__init__()
        self.upload_input_available = False

    def locator(self, selector):
        self.actions.append(("locator", selector))
        return UploadEntryLocator(self, selector)


class ClosedShadowPublishLocator(FakeLocator):
    def count(self):
        if self.selector.startswith("button:has-text"):
            return 0
        if self.selector.startswith("xhs-publish-btn"):
            return 1
        return super().count()

    def bounding_box(self):
        if self.selector.startswith("xhs-publish-btn"):
            return {"x": 600, "y": 650, "width": 260, "height": 44}
        return None

    def click(self, **kwargs):
        super().click(**kwargs)
        if not self.selector.startswith("xhs-publish-btn"):
            return
        position = kwargs.get("position") or {}
        if float(position.get("x") or 0) > 130:
            self.page.url = "https://creator.xiaohongshu.com/publish/success?note=1"
        else:
            self.page.url = "https://creator.xiaohongshu.com/new/home"


class ClosedShadowPublishPage(FakePage):
    def locator(self, selector):
        self.actions.append(("locator", selector))
        return ClosedShadowPublishLocator(self, selector)


class FakeContext:
    def __init__(self, cookie_value="valid", page=None):
        self.page = page or FakePage()
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
        self.new_context_calls = []

    def launch_persistent_context(self, user_data_dir, **kwargs):
        self.launch_calls.append((user_data_dir, kwargs))
        return self.context

    def launch(self, **kwargs):
        self.launch_calls.append(("launch", kwargs))
        return self

    def new_context(self, **kwargs):
        self.new_context_calls.append(kwargs)
        return self.context


class FakePlaywright:
    def __init__(self, context=None):
        self.context = context or FakeContext()
        self.chromium = FakeChromium(self.context)
        self.stopped = False

    def stop(self):
        self.stopped = True


def fake_account(profile_path: Path, storage_state_path: Path | None = None):
    account = {
        "platform": "小红书",
        "auth_status": "已授权",
        "profile_path": str(profile_path),
        "name": "找房子的派大星",
    }
    if storage_state_path is not None:
        account["storage_state_path"] = str(storage_state_path)
    return account


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


def test_patchright_publisher_clicks_closed_shadow_publish_host_when_button_is_hidden(tmp_path: Path):
    profile = tmp_path / "profile"
    profile.mkdir()
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"fake")
    page = ClosedShadowPublishPage()
    playwright = FakePlaywright(FakeContext(page=page))

    result = make_publisher(playwright).publish(fake_payload(image), fake_account(profile), "publish")

    assert result.status == "published"
    assert any(
        action[0] == "locator_click"
        and action[1] == 'xhs-publish-btn[submit-text="发布"]'
        and action[2]["position"]["x"] > 130
        for action in page.actions
        if len(action) == 3
    )


def test_patchright_publisher_clicks_closed_shadow_draft_host_when_button_is_hidden(tmp_path: Path):
    profile = tmp_path / "profile"
    profile.mkdir()
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"fake")
    page = ClosedShadowPublishPage()
    playwright = FakePlaywright(FakeContext(page=page))

    result = make_publisher(playwright).publish(fake_payload(image), fake_account(profile), "draft")

    assert result.status == "platform_draft"
    assert any(
        action[0] == "locator_click"
        and action[1] == 'xhs-publish-btn[save-text="暂存离开"]'
        and action[2]["position"]["x"] < 130
        for action in page.actions
        if len(action) == 3
    )


def test_patchright_publisher_prefers_storage_state_context_over_persistent_profile(tmp_path: Path):
    profile = tmp_path / "profile"
    profile.mkdir()
    storage_state = tmp_path / "xhs-state.json"
    storage_state.write_text("{}", encoding="utf-8")
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"fake")
    playwright = FakePlaywright()

    result = make_publisher(playwright).publish(
        fake_payload(image),
        fake_account(profile, storage_state),
        "draft",
    )

    assert result.status == "platform_draft"
    assert playwright.chromium.new_context_calls[0]["storage_state"] == str(storage_state)
    assert not any(call[0] == str(profile) for call in playwright.chromium.launch_calls)


def test_patchright_publisher_uploads_images_with_set_input_files(tmp_path: Path):
    profile = tmp_path / "profile"
    profile.mkdir()
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"fake")
    playwright = FakePlaywright()

    make_publisher(playwright).publish(fake_payload(image), fake_account(profile), "publish")

    assert playwright.context.page.files == [str(image.resolve())]
    assert any(action[0] == "set_input_files" for action in playwright.context.page.actions)


def test_patchright_publisher_waits_three_seconds_and_traces_render_state_before_upload(tmp_path: Path):
    profile = tmp_path / "profile"
    profile.mkdir()
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"fake")
    playwright = FakePlaywright()
    trace_path = tmp_path / "publish-job.jsonl"
    publisher = make_publisher(playwright)
    publisher.set_trace_context(88, str(trace_path))

    publisher.publish(fake_payload(image), fake_account(profile), "publish")

    actions = playwright.context.page.actions
    wait_index = actions.index(("wait_for_timeout", 3000))
    first_upload_lookup = next(
        index
        for index, action in enumerate(actions)
        if action == ("locator", 'input[type="file"][accept*="image"]')
    )
    assert wait_index < first_upload_lookup
    events = [line for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert any('"event": "xhs.render.wait_start"' in line for line in events)
    assert any('"event": "xhs.render.ready"' in line for line in events)


def test_patchright_publisher_clicks_upload_image_entry_before_setting_files(tmp_path: Path):
    profile = tmp_path / "profile"
    profile.mkdir()
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"fake")
    page = UploadEntryPage()
    playwright = FakePlaywright(FakeContext(page=page))

    result = make_publisher(playwright).publish(fake_payload(image), fake_account(profile), "publish")

    actions = page.actions
    click_index = actions.index(("locator_click", 'button:has-text("上传图片")'))
    set_files_index = next(
        index for index, action in enumerate(actions) if action[0] == "set_input_files"
    )
    assert result.status == "published"
    assert click_index < set_files_index
    assert page.files == [str(image.resolve())]


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
