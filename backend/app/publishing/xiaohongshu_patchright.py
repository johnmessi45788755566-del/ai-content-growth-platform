import hashlib
import json
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Mapping

from .models import PublishAction, PublishResult


XHS_IMAGE_PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?from=homepage&target=image"
XHS_CREATOR_HOST = "creator.xiaohongshu.com"
XHS_PUBLISH_PATH = "/publish/publish"
XHS_SUCCESS_PATH = "/publish/success"
XHS_LOGIN_COOKIE_MARKERS = {
    "access-token-creator.xiaohongshu.com",
    "customer-sso-sid",
    "galaxy.creator.beaker.session.id",
    "galaxy_creator_session_id",
}
XHS_MAX_NOTE_LENGTH = 1000
XHS_UPLOAD_INPUT_SELECTORS = (
    'input[type="file"][accept*="image"]',
    "div[class^='upload-content'] input[class='upload-input']",
    "input.upload-input",
    'input[type="file"]',
)
XHS_UPLOAD_ENTRY_SELECTORS = (
    'button:has-text("上传图片")',
    ".image-upload-buttons button.upload-button",
    "button.upload-button",
)
XHS_PUBLISH_BUTTON_SELECTOR = 'button:has-text("发布")'
XHS_DRAFT_BUTTON_SELECTOR = 'button:has-text("暂存离开")'
XHS_PUBLISH_HOST_SELECTOR = 'xhs-publish-btn[submit-text="发布"]'
XHS_DRAFT_HOST_SELECTOR = 'xhs-publish-btn[save-text="暂存离开"]'


class XiaoHongShuPublishingError(RuntimeError):
    pass


def _start_patchright() -> Any:
    try:
        from patchright.sync_api import sync_playwright
    except ModuleNotFoundError:
        from playwright.sync_api import sync_playwright
    return sync_playwright().start()


class XiaoHongShuPatchrightPublisher:
    def __init__(self, playwright_factory: Callable[[], Any] | None = None):
        self.playwright_factory = playwright_factory or _start_patchright
        self._trace_job_id = 0
        self._trace_path: Path | None = None

    def set_trace_context(self, job_id: int, trace_path: str) -> None:
        self._trace_job_id = int(job_id)
        self._trace_path = Path(trace_path)
        self._trace_path.parent.mkdir(parents=True, exist_ok=True)
        self._trace("trace.started")

    def clear_trace_context(self) -> None:
        self._trace("trace.finished")
        self._trace_job_id = 0
        self._trace_path = None

    def publish(
        self,
        payload: dict[str, Any],
        account: Mapping[str, Any],
        action: PublishAction,
    ) -> PublishResult:
        return self._publish(payload, account, action)

    def publish_with_progress(
        self,
        payload: dict[str, Any],
        account: Mapping[str, Any],
        action: PublishAction,
        report_progress: Callable[[str], None],
    ) -> PublishResult:
        return self._publish(payload, account, action, report_progress)

    def resume(self, account: Mapping[str, Any], job_context: Mapping[str, Any]) -> PublishResult:
        raise XiaoHongShuPublishingError("小红书发布不支持人工确认恢复")

    def cancel(self, account: Mapping[str, Any], job_context: Mapping[str, Any]) -> None:
        return None

    def verify_login(self, account: Mapping[str, Any]) -> dict[str, Any]:
        profile_path = self._profile_path(account)
        storage_state_path = self._storage_state_path(account)
        playwright = None
        context = None
        page = None
        try:
            playwright = self.playwright_factory()
            self._trace("xhs.patchright.auth_preflight_launch")
            context = self._launch_context(playwright, profile_path, storage_state_path)
            self._close_previous_publish_pages(context)
            page = context.new_page()
            page.goto(XHS_IMAGE_PUBLISH_URL, wait_until="domcontentloaded", timeout=60000)
            with _suppress_exceptions():
                page.wait_for_url("**/publish/publish**", timeout=15000)
            self._trace("xhs.patchright.auth_preflight_url", url=self._current_url(page))
            self._require_authenticated(context, page)
            parsed = urllib.parse.urlparse(self._current_url(page))
            if parsed.hostname != XHS_CREATOR_HOST or parsed.path != XHS_PUBLISH_PATH:
                raise XiaoHongShuPublishingError("小红书发布页登录态未通过，请重新扫码登录")
            return {"verified": True, "platform_url": self._current_url(page)}
        except XiaoHongShuPublishingError:
            if page is not None:
                self._capture_failure_snapshot(page, "xiaohongshu_auth_preflight_error")
            raise
        except Exception as exc:
            if page is not None:
                self._capture_failure_snapshot(page, "xiaohongshu_auth_preflight_unexpected_error")
            raise XiaoHongShuPublishingError(f"小红书发布页登录态未通过，请重新扫码登录：{exc}") from exc
        finally:
            if context is not None:
                self._close_publish_pages(context)
                with _suppress_exceptions():
                    context.close()
                browser = getattr(context, "_xhs_browser", None)
                if browser is not None:
                    with _suppress_exceptions():
                        browser.close()
            if playwright is not None:
                with _suppress_exceptions():
                    playwright.stop()

    def _publish(
        self,
        payload: dict[str, Any],
        account: Mapping[str, Any],
        action: PublishAction,
        report_progress: Callable[[str], None] | None = None,
    ) -> PublishResult:
        self._validate_action(action)
        self._validate_payload(payload)
        profile_path = self._profile_path(account)
        storage_state_path = self._storage_state_path(account)
        playwright = None
        context = None
        page = None
        try:
            playwright = self.playwright_factory()
            self._trace("xhs.patchright.launch")
            context = self._launch_context(playwright, profile_path, storage_state_path)
            self._close_previous_publish_pages(context)
            page = context.new_page()
            page.goto(XHS_IMAGE_PUBLISH_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_url("**/publish/publish**", timeout=60000)
            self._trace("xhs.patchright.goto_publish", url=self._current_url(page))
            self._require_authenticated(context, page)
            self._wait_for_publish_render(page)
            if report_progress:
                report_progress("uploading_assets")
            self._upload_images(page, payload["image_paths"])
            if report_progress:
                report_progress("filling_content")
            self._fill_content(page, payload)
            if report_progress:
                report_progress("publishing")
            if action == "draft":
                result = self._save_draft_note(page, payload)
            else:
                result = self._publish_note(page, payload)
            self._export_storage_state(context, storage_state_path)
            return result
        except XiaoHongShuPublishingError:
            if page is not None:
                self._capture_failure_snapshot(page, "xiaohongshu_publish_error")
            raise
        except Exception as exc:
            if page is not None:
                self._capture_failure_snapshot(page, "xiaohongshu_unexpected_error")
            raise XiaoHongShuPublishingError(f"小红书 Patchright 发布失败：{exc}") from exc
        finally:
            if context is not None:
                self._close_publish_pages(context)
                with _suppress_exceptions():
                    context.close()
                browser = getattr(context, "_xhs_browser", None)
                if browser is not None:
                    with _suppress_exceptions():
                        browser.close()
            if playwright is not None:
                with _suppress_exceptions():
                    playwright.stop()

    def _launch_context(
        self,
        playwright: Any,
        profile_path: Path,
        storage_state_path: Path | None = None,
    ) -> Any:
        launcher = getattr(playwright, "chromium", None)
        if launcher is None:
            raise XiaoHongShuPublishingError("Patchright 未提供 chromium 浏览器")
        if storage_state_path is not None:
            browser = launcher.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                storage_state=str(storage_state_path),
                viewport={"width": 1200, "height": 707},
            )
            with _suppress_exceptions():
                setattr(context, "_xhs_browser", browser)
            return context
        return launcher.launch_persistent_context(
            user_data_dir=str(profile_path),
            headless=False,
            viewport={"width": 1200, "height": 707},
            args=["--disable-blink-features=AutomationControlled"],
        )

    def _require_authenticated(self, context: Any, page: Any) -> None:
        cookie_names = {
            str(cookie.get("name") or "")
            for cookie in (context.cookies() or [])
            if str(cookie.get("name") or "").strip()
        }
        current_url = self._current_url(page)
        parsed = urllib.parse.urlparse(current_url)
        if parsed.hostname and parsed.hostname != XHS_CREATOR_HOST:
            raise XiaoHongShuPublishingError("小红书当前页面不是创作者中心，已终止发布")
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path.startswith("/login") or query.get("redirectReason") == ["401"]:
            raise XiaoHongShuPublishingError("小红书登录态失效：发布页跳转到登录页，请重新扫码登录")
        if not (cookie_names & XHS_LOGIN_COOKIE_MARKERS):
            raise XiaoHongShuPublishingError("小红书登录态失效：缺少登录 cookie，请重新扫码登录")

    def _export_storage_state(
        self, context: Any, storage_state_path: Path | None
    ) -> None:
        if storage_state_path is None:
            return
        storage_state = getattr(context, "storage_state", None)
        if callable(storage_state):
            with _suppress_exceptions():
                storage_state(path=str(storage_state_path))

    def _wait_for_publish_render(self, page: Any) -> None:
        wait_ms = 3000
        started_at = time.monotonic()
        self._trace("xhs.render.wait_start", wait_ms=wait_ms)
        page.wait_for_timeout(wait_ms)
        state = self._publish_render_state(page)
        elapsed_ms = max(0, int((time.monotonic() - started_at) * 1000))
        event = "xhs.render.ready" if self._publish_render_ready(state) else "xhs.render.timeout"
        self._trace(event, wait_ms=wait_ms, elapsed_ms=elapsed_ms, **state)

    def _publish_render_state(self, page: Any) -> dict[str, Any]:
        state = {
            "url": self._current_url(page),
            "ready_state": "",
            "upload_input_count": self._count_selectors(page, XHS_UPLOAD_INPUT_SELECTORS),
            "upload_entry_count": self._count_selectors(page, XHS_UPLOAD_ENTRY_SELECTORS),
            "title_input_count": self._locator_count(page, 'input[placeholder*="填写标题"]'),
            "publish_button_count": self._locator_count(page, XHS_PUBLISH_BUTTON_SELECTOR)
            + self._locator_count(page, XHS_PUBLISH_HOST_SELECTOR),
            "draft_button_count": self._locator_count(page, XHS_DRAFT_BUTTON_SELECTOR)
            + self._locator_count(page, XHS_DRAFT_HOST_SELECTOR),
            "visible_button_texts": [],
        }
        with _suppress_exceptions():
            evaluated = page.evaluate(
                """() => ({
                    readyState: document.readyState,
                    buttons: Array.from(document.querySelectorAll('button'))
                        .filter((button) => {
                            const rect = button.getBoundingClientRect();
                            return rect.width > 0 && rect.height > 0;
                        })
                        .map((button) => (button.innerText || button.textContent || '').trim())
                        .filter(Boolean)
                        .slice(0, 12)
                })"""
            )
            if isinstance(evaluated, dict):
                state["ready_state"] = str(evaluated.get("readyState") or "")
                buttons = evaluated.get("buttons") or []
                if isinstance(buttons, list):
                    state["visible_button_texts"] = [str(text)[:30] for text in buttons]
        return state

    @staticmethod
    def _publish_render_ready(state: Mapping[str, Any]) -> bool:
        return bool(
            int(state.get("upload_input_count") or 0) > 0
            or int(state.get("upload_entry_count") or 0) > 0
        )

    def _count_selectors(self, page: Any, selectors: tuple[str, ...]) -> int:
        return sum(self._locator_count(page, selector) for selector in selectors)

    @staticmethod
    def _locator_count(page: Any, selector: str) -> int:
        try:
            return int(page.locator(selector).first.count())
        except Exception:
            return 0

    def _upload_images(self, page: Any, image_paths: list[str]) -> None:
        paths = [str(Path(path).expanduser().resolve()) for path in image_paths]
        files_selected = False
        if not self._has_existing_locator(page, XHS_UPLOAD_INPUT_SELECTORS):
            files_selected = self._open_upload_entry(page, paths)
        if not files_selected:
            upload_input = self._first_existing_locator(page, XHS_UPLOAD_INPUT_SELECTORS)
            upload_input.wait_for(state="attached", timeout=30000)
            self._trace("xhs.upload.locator_ready", selector="input[type=file]", file_count=len(paths))
            upload_input.set_input_files(paths)
            self._trace("xhs.upload.set_input_files_done", file_count=len(paths))
        title_input = page.locator('input[placeholder*="填写标题"]').first
        title_input.wait_for(state="visible", timeout=90000)

    def _open_upload_entry(self, page: Any, paths: list[str]) -> bool:
        for selector in XHS_UPLOAD_ENTRY_SELECTORS:
            locator = page.locator(selector).first
            try:
                count = locator.count()
            except Exception:
                count = 0
            self._trace("xhs.upload.entry_candidate", selector=selector, count=count)
            if count <= 0:
                continue
            locator.wait_for(state="visible", timeout=10000)
            expect_file_chooser = getattr(page, "expect_file_chooser", None)
            if callable(expect_file_chooser):
                try:
                    with expect_file_chooser(timeout=5000) as chooser_info:
                        locator.click()
                    chooser_info.value.set_files(paths)
                    self._trace(
                        "xhs.upload.file_chooser_set_files_done",
                        selector=selector,
                        file_count=len(paths),
                    )
                    return True
                except Exception as exc:
                    self._trace("xhs.upload.file_chooser_not_used", selector=selector, error=str(exc))
            else:
                locator.click()
            self._trace("xhs.upload.entry_clicked", selector=selector)
            return False
        raise XiaoHongShuPublishingError("小红书页面未找到图片上传入口")

    def _fill_content(self, page: Any, payload: Mapping[str, Any]) -> None:
        title = str(payload.get("title") or "").strip()[:20]
        note = str(payload.get("note") or "").strip()
        self._trace(
            "xhs.content.length_check",
            title_length=len(title),
            note_length=len(note),
            max_note_length=XHS_MAX_NOTE_LENGTH,
        )
        title_input = page.locator('input[placeholder*="填写标题"]').first
        title_input.fill(title)
        editor = self._first_existing_locator(
            page,
            (
                'p[data-placeholder*="输入正文描述"]',
                '[contenteditable="true"][data-placeholder*="输入正文描述"]',
                '[contenteditable="true"]',
            ),
        )
        editor.click()
        keyboard = getattr(page, "keyboard", None)
        if keyboard is None:
            raise XiaoHongShuPublishingError("小红书页面键盘输入接口不可用")
        insert_text = getattr(keyboard, "insert_text", None) or getattr(keyboard, "type", None)
        if not callable(insert_text):
            raise XiaoHongShuPublishingError("小红书页面键盘输入接口不可用")
        insert_text(note)

    def _publish_note(self, page: Any, payload: Mapping[str, Any]) -> PublishResult:
        page.wait_for_timeout(2000)
        self._click_publish_action(page, "publish")
        state = self._trace_publish_after_click_state(page, attempt=1, elapsed_ms=1000, wait_ms=1000)
        if self._should_retry_publish_click(state):
            state = self._trace_publish_after_click_state(page, attempt=1, elapsed_ms=3000, wait_ms=2000)
            if self._should_retry_publish_click(state):
                self._trace("xhs.publish.retry_click", reason="publish_page_still_idle_after_click")
                self._click_publish_action(page, "publish")
                self._trace_publish_after_click_state(page, attempt=2, elapsed_ms=1000, wait_ms=1000)
        page.wait_for_url("**/publish/success**", timeout=60000)
        success_url = self._current_url(page)
        parsed = urllib.parse.urlparse(success_url)
        if parsed.hostname != XHS_CREATOR_HOST or parsed.path != XHS_SUCCESS_PATH:
            raise XiaoHongShuPublishingError("小红书发布后未进入成功页")
        self._trace("xhs.publish.success_url", url=success_url)
        return self._publish_result(success_url, payload)

    def _save_draft_note(self, page: Any, payload: Mapping[str, Any]) -> PublishResult:
        page.wait_for_timeout(2000)
        self._click_publish_action(page, "draft")
        page.wait_for_function(
            "() => !location.pathname.includes('/publish/publish')",
            timeout=60000,
        )
        draft_url = self._current_url(page)
        parsed = urllib.parse.urlparse(draft_url)
        if parsed.hostname == XHS_CREATOR_HOST and parsed.path == XHS_PUBLISH_PATH:
            raise XiaoHongShuPublishingError("小红书暂存离开后未确认草稿保存")
        self._trace("xhs.draft.saved_confirmed", url=draft_url)
        return self._draft_result(draft_url, payload)

    def _click_publish_action(self, page: Any, action: str) -> None:
        if action == "publish":
            text = "发布"
            trace_prefix = "xhs.publish"
            button_selector = XHS_PUBLISH_BUTTON_SELECTOR
            host_selector = XHS_PUBLISH_HOST_SELECTOR
            host_x_ratio = 0.75
        elif action == "draft":
            text = "暂存离开"
            trace_prefix = "xhs.draft"
            button_selector = XHS_DRAFT_BUTTON_SELECTOR
            host_selector = XHS_DRAFT_HOST_SELECTOR
            host_x_ratio = 0.25
        else:
            raise XiaoHongShuPublishingError(f"小红书发布动作不支持：{action}")

        if self._click_publish_host(page, host_selector, host_x_ratio, trace_prefix, text):
            return
        if self._click_plain_publish_button(page, button_selector, trace_prefix, text):
            return
        if self._click_publish_host(page, host_selector, host_x_ratio, trace_prefix, text):
            return
        raise XiaoHongShuPublishingError(f"小红书页面未找到{text}按钮")

    def _click_plain_publish_button(
        self, page: Any, selector: str, trace_prefix: str, text: str
    ) -> bool:
        if self._locator_count(page, selector) <= 0:
            return False
        button = page.locator(selector).last
        try:
            button.wait_for(state="visible", timeout=2000)
        except Exception as exc:
            self._trace(
                f"{trace_prefix}.locator_unavailable",
                button_text=text,
                selector=selector,
                error=str(exc),
            )
            return False
        self._trace(f"{trace_prefix}.locator_found", button_text=text, selector=selector)
        button.click()
        self._trace(f"{trace_prefix}.locator_clicked", button_text=text, selector=selector)
        return True

    def _click_publish_host(
        self,
        page: Any,
        selector: str,
        x_ratio: float,
        trace_prefix: str,
        text: str,
    ) -> bool:
        if self._locator_count(page, selector) <= 0:
            return False
        host = page.locator(selector).last
        host.wait_for(state="visible", timeout=30000)
        state = self._publish_host_state(host)
        self._trace(
            f"{trace_prefix}.host_state",
            button_text=text,
            selector=selector,
            **state,
        )
        if text == "发布":
            if state.get("submit_disabled") == "true" or state.get("submit_loading") == "true":
                raise XiaoHongShuPublishingError("小红书发布按钮不可用")
        elif state.get("save_disabled") == "true":
            raise XiaoHongShuPublishingError("小红书暂存按钮不可用")
        position = self._publish_host_click_position(host, x_ratio)
        self._trace(f"{trace_prefix}.host_found", button_text=text, selector=selector)
        host.click(position=position)
        self._trace(
            f"{trace_prefix}.host_clicked",
            button_text=text,
            selector=selector,
            position_x=int(position["x"]),
            position_y=int(position["y"]),
        )
        return True

    def _trace_publish_after_click_state(
        self,
        page: Any,
        *,
        attempt: int,
        elapsed_ms: int,
        wait_ms: int,
    ) -> dict[str, Any]:
        page.wait_for_timeout(wait_ms)
        state: dict[str, Any] = {"ok": True, "url": self._current_url(page)}
        with _suppress_exceptions():
            evaluated = page.evaluate(self._monitor_expression())
            if isinstance(evaluated, Mapping):
                state.update(evaluated)
        self._trace(
            "xhs.publish.after_click_state",
            attempt=attempt,
            elapsed_ms=elapsed_ms,
            state=state,
        )
        return state

    @staticmethod
    def _should_retry_publish_click(state: Mapping[str, Any]) -> bool:
        parsed = urllib.parse.urlparse(str(state.get("url") or ""))
        if parsed.hostname != XHS_CREATOR_HOST or parsed.path != XHS_PUBLISH_PATH:
            return False
        controls = state.get("publishControls")
        if not isinstance(controls, list) or not controls:
            return False
        control = next(
            (
                item for item in controls
                if isinstance(item, Mapping) and str(item.get("submitText") or "") == "发布"
            ),
            controls[0],
        )
        if not isinstance(control, Mapping):
            return False
        if str(control.get("submitDisabled") or "").lower() == "true":
            return False
        if str(control.get("submitLoading") or "").lower() == "true":
            return False
        return True

    @staticmethod
    def _publish_host_state(locator: Any) -> dict[str, Any]:
        state = {
            "submit_disabled": "",
            "submit_loading": "",
            "save_disabled": "",
            "submit_text": "",
            "save_text": "",
            "rect": {},
        }
        with _suppress_exceptions():
            getter = getattr(locator, "get_attribute", None)
            if callable(getter):
                state["submit_disabled"] = str(getter("submit-disabled") or "")
                state["submit_loading"] = str(getter("submit-loading") or "")
                state["save_disabled"] = str(getter("save-disabled") or "")
                state["submit_text"] = str(getter("submit-text") or "")
                state["save_text"] = str(getter("save-text") or "")
        bounding_box = getattr(locator, "bounding_box", None)
        if callable(bounding_box):
            with _suppress_exceptions():
                box = bounding_box()
                if isinstance(box, Mapping):
                    state["rect"] = {
                        "x": int(float(box.get("x") or 0)),
                        "y": int(float(box.get("y") or 0)),
                        "width": int(float(box.get("width") or 0)),
                        "height": int(float(box.get("height") or 0)),
                    }
        return state

    @staticmethod
    def _publish_host_click_position(locator: Any, x_ratio: float) -> dict[str, float]:
        width = 240.0
        height = 40.0
        bounding_box = getattr(locator, "bounding_box", None)
        if callable(bounding_box):
            with _suppress_exceptions():
                box = bounding_box()
                if isinstance(box, Mapping):
                    width = float(box.get("width") or width)
                    height = float(box.get("height") or height)
        direction = 1.0 if x_ratio >= 0.5 else -1.0
        x = (width * 0.5) + (direction * 72.0)
        return {
            "x": max(1.0, min(width - 1.0, x)),
            "y": max(1.0, height * 0.5),
        }

    @staticmethod
    def _first_existing_locator(page: Any, selectors: tuple[str, ...]) -> Any:
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if locator.count() > 0:
                    return locator
            except Exception:
                continue
        raise XiaoHongShuPublishingError("小红书页面未找到目标控件")

    @staticmethod
    def _has_existing_locator(page: Any, selectors: tuple[str, ...]) -> bool:
        for selector in selectors:
            try:
                if page.locator(selector).first.count() > 0:
                    return True
            except Exception:
                continue
        return False


    def _close_previous_publish_pages(self, context: Any) -> None:
        for page in list(getattr(context, "pages", []) or []):
            if self._is_publish_page(page):
                with _suppress_exceptions():
                    page.close()
                    self._trace("tab.close_target", reason="previous_publish_page")

    def _close_publish_pages(self, context: Any) -> None:
        for page in list(getattr(context, "pages", []) or []):
            if self._is_publish_page(page):
                with _suppress_exceptions():
                    page.close()
                    self._trace("tab.close_target", reason="publish_page_cleanup")

    @staticmethod
    def _is_publish_page(page: Any) -> bool:
        parsed = urllib.parse.urlparse(str(getattr(page, "url", "") or ""))
        return parsed.hostname == XHS_CREATOR_HOST and parsed.path in {XHS_PUBLISH_PATH, XHS_SUCCESS_PATH}

    @staticmethod
    def _current_url(page: Any) -> str:
        return str(getattr(page, "url", "") or page.evaluate("location.href") or "")

    def _capture_failure_snapshot(self, page: Any, label: str) -> None:
        self._trace("monitor.installed", label=label, result={"ok": True, "installed": True})
        state = {"ok": True, "url": self._current_url(page)}
        with _suppress_exceptions():
            state.update(page.evaluate(self._monitor_expression()) or {})
        self._trace("monitor.snapshot", label=label, state=state)
        self._write_trace_json_artifact(label, state)
        path = self._trace_artifact_path(label, ".png")
        if path is None:
            return
        with _suppress_exceptions():
            page.screenshot(path=str(path), full_page=True)
            self._trace("monitor.screenshot", label=label, path=str(path))

    @staticmethod
    def _monitor_expression() -> str:
        return """
        (() => {
          const visible = (node) => !!node && getComputedStyle(node).display !== 'none'
            && getComputedStyle(node).visibility !== 'hidden' && node.getClientRects().length > 0;
          const textOf = (node) => (node.innerText || node.textContent || '').trim();
          const buttons = [...document.querySelectorAll('button, a, [role="button"]')]
            .filter(visible).slice(0, 80).map((node) => textOf(node).slice(0, 80));
          const publishHosts = [...document.querySelectorAll('xhs-publish-btn[submit-text="发布"]')].filter(visible);
          const draftHosts = [...document.querySelectorAll('xhs-publish-btn[save-text="暂存离开"]')].filter(visible);
          const publishControls = [...document.querySelectorAll('xhs-publish-btn')]
            .filter(visible).slice(0, 20).map((node) => {
              const rect = node.getBoundingClientRect();
              return {
                submitText: node.getAttribute('submit-text') || '',
                saveText: node.getAttribute('save-text') || '',
                submitDisabled: node.getAttribute('submit-disabled') || '',
                submitLoading: node.getAttribute('submit-loading') || '',
                saveDisabled: node.getAttribute('save-disabled') || '',
                rect: {
                  x: Math.round(rect.x),
                  y: Math.round(rect.y),
                  width: Math.round(rect.width),
                  height: Math.round(rect.height),
                },
              };
            });
          const editor = document.querySelector('p[data-placeholder*="输入正文描述"], [contenteditable="true"]');
          const noteText = editor ? textOf(editor) : '';
          return {
            publishButtonCount: [...document.querySelectorAll('button')].filter((node) => textOf(node).includes('发布')).length + publishHosts.length,
            draftButtonCount: [...document.querySelectorAll('button')].filter((node) => textOf(node).includes('暂存离开')).length + draftHosts.length,
            visibleButtons: buttons.concat(publishHosts.map(() => '发布')).concat(draftHosts.map(() => '暂存离开')),
            publishControls,
            noteLength: noteText.length,
            noteOverLimit: noteText.length > 1000,
          };
        })()
        """

    def _trace(self, event: str, **fields: Any) -> None:
        if not self._trace_path:
            return
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "event": event,
            "job_id": self._trace_job_id,
            **self._safe_trace_payload(fields),
        }
        with self._trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _trace_artifact_path(self, label: str, suffix: str) -> Path | None:
        if not self._trace_path:
            return None
        directory = self._trace_path.with_suffix("")
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        safe_label = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label)[:80]
        return directory / f"{timestamp}-{safe_label}{suffix}"

    def _write_trace_json_artifact(self, label: str, state: Any) -> None:
        path = self._trace_artifact_path(label, "-state.json")
        if path is None:
            return
        with _suppress_exceptions():
            path.write_text(json.dumps(self._safe_trace_payload(state), ensure_ascii=False, indent=2), encoding="utf-8")
            self._trace("monitor.snapshot_file", label=label, path=str(path))

    @classmethod
    def _safe_trace_payload(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): cls._safe_trace_payload(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._safe_trace_payload(item) for item in value]
        if isinstance(value, Path):
            return value.name
        return value

    @staticmethod
    def _validate_action(action: str) -> None:
        if action not in {"draft", "publish"}:
            raise XiaoHongShuPublishingError("小红书发布 action 必须是 draft 或 publish")

    @staticmethod
    def _validate_payload(payload: Mapping[str, Any]) -> None:
        if not str(payload.get("title") or "").strip():
            raise XiaoHongShuPublishingError("小红书标题不能为空")
        note = str(payload.get("note") or "").strip()
        if len(note) > XHS_MAX_NOTE_LENGTH:
            raise XiaoHongShuPublishingError("小红书正文超过 1000 字，请缩短后再发布")
        image_paths = payload.get("image_paths")
        if not isinstance(image_paths, list) or not image_paths:
            raise XiaoHongShuPublishingError("小红书图文发布至少需要一张图片")
        for image_path in image_paths:
            path = Path(str(image_path or "")).expanduser()
            if not path.is_file():
                raise XiaoHongShuPublishingError(f"小红书图文图片不存在：{path}")

    @staticmethod
    def _profile_path(account: Mapping[str, Any]) -> Path:
        if account.get("platform") != "小红书":
            raise XiaoHongShuPublishingError("所选账号不是小红书账号")
        if account.get("auth_status") != "已授权":
            raise XiaoHongShuPublishingError("小红书账号未授权，请重新扫码登录")
        raw_path = str(account.get("profile_path") or "").strip()
        path = Path(raw_path).expanduser() if raw_path else Path()
        if not raw_path or not path.is_dir():
            raise XiaoHongShuPublishingError(f"账号浏览器 Profile 不存在：{raw_path or '未配置'}")
        return path

    @staticmethod
    def _storage_state_path(account: Mapping[str, Any]) -> Path | None:
        raw_path = str(account.get("storage_state_path") or "").strip()
        if not raw_path:
            return None
        path = Path(raw_path).expanduser()
        return path if path.is_file() else None

    @staticmethod
    def _publish_result(success_url: str, payload: Mapping[str, Any]) -> PublishResult:
        title = str(payload.get("title") or "").strip()
        receipt_id = "xhs-" + hashlib.sha256(f"{success_url}\n{title}".encode("utf-8")).hexdigest()[:16]
        return PublishResult(
            status="published",
            platform_publish_id=receipt_id,
            platform_url=success_url,
            response={
                "source": "xiaohongshu_platform",
                "event": "note_published",
                "receipt_id": receipt_id,
                "title": title,
                "corroboration": "publish_success_url",
                "delivery_status": "published",
            },
        )

    @staticmethod
    def _draft_result(draft_url: str, payload: Mapping[str, Any]) -> PublishResult:
        title = str(payload.get("title") or "").strip()
        receipt_id = "xhs-draft-" + hashlib.sha256(f"{draft_url}\n{title}".encode("utf-8")).hexdigest()[:16]
        return PublishResult(
            status="platform_draft",
            platform_draft_id=receipt_id,
            platform_url=draft_url,
            response={
                "source": "xiaohongshu_platform",
                "event": "draft_created",
                "receipt_id": receipt_id,
                "title": title,
                "corroboration": "draft_saved",
                "delivery_status": "platform_draft",
            },
        )


class _suppress_exceptions:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        return True


__all__ = [
    "XHS_IMAGE_PUBLISH_URL",
    "XiaoHongShuPatchrightPublisher",
    "XiaoHongShuPublishingError",
]
