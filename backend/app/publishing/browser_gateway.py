import base64
import html
import json
import mimetypes
import os
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import websocket


WECHAT_HOME_URL = "https://mp.weixin.qq.com/"

TITLE_SELECTORS = ("#title", "textarea[name='title']", ".js_title", "[data-placeholder*='标题']")
AUTHOR_SELECTORS = ("#author", "input[name='author']", ".js_author")
DIGEST_SELECTORS = ("#js_description", "textarea[name='digest']", ".js_desc")
EDITOR_SELECTORS = (".ProseMirror[contenteditable='true']", "#ueditor_0", "[contenteditable='true'].js_editor")
COVER_FILE_SELECTORS = (
    ".js_cover_area input[type='file']",
    ".js_cover_upload input[type='file']",
    ".cover-upload input[type='file']",
)
INLINE_FILE_SELECTORS = (
    ".js_editor_insertimage input[type='file']",
    ".edui-for-insertimage input[type='file']",
    ".js_image_upload input[type='file']",
    ".editor-image-upload input[type='file']",
)
GENERIC_IMAGE_FILE_SELECTORS = (
    "input[type='file'][accept*='image']",
    "input[type='file'][accept*='.jpg']",
    "input[type='file'][accept*='.jpeg']",
    "input[type='file'][accept*='.png']",
)
SAVE_DRAFT_TEXTS = ("保存为草稿", "保存草稿")
COVER_UPLOAD_TEXTS = ("拖拽或选择封面", "选择封面", "上传封面", "添加封面")
INLINE_UPLOAD_TEXTS = ("图片", "本地上传", "上传图片", "选择图片")
NEW_ARTICLE_SELECTORS = (
    "a[href*='appmsg_edit']",
    ".new-creation__menu-item a[href*='appmsg']",
    ".weui-desktop-panel__bd a[href*='appmsg']",
)
SAVE_DRAFT_SELECTORS = ("#js_submit", ".js_submit", "button[data-action='save']", "button[aria-label*='保存']")
PUBLISH_SELECTORS = (
    "#js_send",
    ".js_send",
    "button[data-action='publish']",
    "button[aria-label*='发表']",
)
PUBLISH_CONFIRM_SELECTORS = (
    ".weui-desktop-dialog__ft .weui-desktop-btn_primary",
    ".weui-desktop-dialog button[type='submit']",
    ".js_confirm_send",
)
STATE_TRAVERSAL_MAX_DEPTH = 7
STATE_TRAVERSAL_MAX_ENTRIES = 500


class WeChatPublishingError(RuntimeError):
    pass


def normalize_wechat_title(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


@dataclass
class BrowserSession:
    session_id: str
    profile_path: Path
    port: int
    target_id: str
    websocket_url: str
    process: subprocess.Popen[Any] | None = None
    url: str = WECHAT_HOME_URL
    confirmation_screenshot: str = ""


@dataclass
class PendingConfirmation:
    session: BrowserSession
    account_key: str
    profile_path: Path
    title: str
    baseline: dict[str, Any]
    timer: Any
    expiry_callback: Any | None = None


class ChromeCDPBrowserGateway:
    def __init__(
        self,
        chrome_path: str | Path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        startup_timeout: float = 20,
        result_timeout: float = 45,
        auth_timeout: float = 12,
        upload_timeout: float = 30,
        pending_timeout: float = 300,
        timer_factory: Any = threading.Timer,
    ):
        self.chrome_path = Path(chrome_path)
        self.startup_timeout = startup_timeout
        self.result_timeout = result_timeout
        self.auth_timeout = auth_timeout
        self.upload_timeout = upload_timeout
        self.pending_timeout = pending_timeout
        self.timer_factory = timer_factory
        self._sessions: dict[str, BrowserSession] = {}
        self._pending: dict[str, PendingConfirmation] = {}
        self._lock = threading.Lock()
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

    def _trace(self, event: str, **fields: Any) -> None:
        if not self._trace_path:
            return
        payload = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "event": event,
            "job_id": self._trace_job_id,
            **self._safe_trace_payload(fields),
        }
        try:
            with self._trace_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _safe_trace_payload(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): self._safe_trace_payload(item)
                for key, item in value.items()
                if str(key).lower()
                not in {"authorization", "cookie", "cookies", "token", "websocket_url"}
            }
        if isinstance(value, (list, tuple)):
            return [self._safe_trace_payload(item) for item in value]
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, str):
            parsed = urllib.parse.urlparse(value)
            if parsed.scheme in {"http", "https", "ws", "wss"} and parsed.hostname:
                if parsed.scheme in {"ws", "wss"}:
                    return "[已隐藏浏览器调试地址]"
                return urllib.parse.urlunparse(
                    (parsed.scheme, parsed.netloc, parsed.path, "", "", "")
                )
            return value
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return str(value)

    def open_profile(self, profile_path: Path, url: str) -> BrowserSession:
        profile_path = profile_path.expanduser().resolve()
        if not profile_path.is_dir():
            raise WeChatPublishingError(f"浏览器 Profile 不存在：{profile_path}")
        if not self.chrome_path.is_file():
            raise WeChatPublishingError("未找到 Google Chrome，无法打开微信公众号后台")
        port = self._free_port()
        process = None
        session = None
        try:
            process = subprocess.Popen(
                [
                    str(self.chrome_path),
                    f"--user-data-dir={profile_path}",
                    f"--remote-debugging-port={port}",
                    "--remote-debugging-address=127.0.0.1",
                    "--remote-allow-origins=*",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-sync",
                    "--new-window",
                    url,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            target = self._wait_for_target(port, process)
            session = BrowserSession(
                session_id=secrets.token_urlsafe(24),
                profile_path=profile_path,
                port=port,
                target_id=str(target["id"]),
                websocket_url=str(target["webSocketDebuggerUrl"]),
                process=process,
                url=str(target.get("url") or url),
            )
            self._sessions[session.session_id] = session
            self.navigate(session, url)
            return session
        except Exception:
            if session is not None:
                self.close(session)
            elif process is not None:
                self._stop_process(process, graceful=False)
            raise

    def retain_confirmation(
        self,
        session: BrowserSession,
        account_key: str,
        title: str,
        baseline: Mapping[str, Any],
    ) -> str:
        self._require_session_host(session)
        handle = secrets.token_urlsafe(32)
        timer = self.timer_factory(
            self.pending_timeout, lambda: self._expire_pending(handle)
        )
        if hasattr(timer, "daemon"):
            timer.daemon = True
        pending = PendingConfirmation(
            session=session,
            account_key=account_key,
            profile_path=session.profile_path.resolve(),
            title=title,
            baseline=dict(baseline),
            timer=timer,
        )
        with self._lock:
            self._pending[handle] = pending
        timer.start()
        return handle

    def resume_profile(
        self, profile_path: Path, resume_handle: str, account_key: str
    ) -> tuple[BrowserSession, str, dict[str, Any]]:
        with self._lock:
            pending = self._pending.get(resume_handle)
        if not pending:
            raise WeChatPublishingError("扫码确认恢复句柄不存在或已过期")
        if pending.account_key != account_key:
            raise WeChatPublishingError("扫码确认恢复句柄与账号不匹配")
        if pending.profile_path != profile_path.expanduser().resolve():
            raise WeChatPublishingError("扫码确认恢复句柄与浏览器 Profile 不匹配")
        self._require_session_host(pending.session)
        with self._lock:
            current = self._pending.pop(resume_handle, None)
        if current is not pending:
            raise WeChatPublishingError("扫码确认恢复句柄已被使用")
        pending.timer.cancel()
        return pending.session, pending.title, dict(pending.baseline)

    def cancel_pending(self, resume_handle: str, account_key: str) -> None:
        with self._lock:
            pending = self._pending.get(resume_handle)
            if not pending:
                raise WeChatPublishingError("扫码确认恢复句柄不存在或已过期")
            if pending.account_key != account_key:
                raise WeChatPublishingError("扫码确认恢复句柄与账号不匹配")
            self._pending.pop(resume_handle, None)
        pending.timer.cancel()
        self.close(pending.session)

    def bind_confirmation_expiry(self, resume_handle: str, callback: Any) -> None:
        with self._lock:
            pending = self._pending.get(resume_handle)
            if pending:
                pending.expiry_callback = callback
        if not pending:
            callback()

    def _expire_pending(self, resume_handle: str) -> None:
        with self._lock:
            pending = self._pending.pop(resume_handle, None)
        if pending:
            self.close(pending.session)
            if pending.expiry_callback:
                pending.expiry_callback()

    def navigate(self, session: BrowserSession, url: str) -> None:
        self._cdp_call(session, "Page.navigate", {"url": url})
        self._wait_for_document(session)

    def evaluate(self, session: BrowserSession, expression: str) -> Any:
        payload = self._cdp_call(
            session,
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        exception = payload.get("exceptionDetails") or {}
        if exception:
            detail = exception.get("exception") or {}
            raise WeChatPublishingError(
                str(detail.get("description") or exception.get("text") or "微信页面脚本执行失败")
            )
        result = payload.get("result") or {}
        if result.get("subtype") == "error":
            raise WeChatPublishingError(str(result.get("description") or "微信页面脚本执行失败"))
        return result.get("value")

    def set_file_input(
        self,
        session: BrowserSession,
        selector_candidates: Sequence[str],
        file_path: str | Path,
        label: str = "图片",
        role: str = "inline",
    ) -> dict[str, Any]:
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise WeChatPublishingError(f"{label}文件不存在：{path}")
        if role not in {"cover", "inline"}:
            raise WeChatPublishingError("图片上传角色必须是 cover 或 inline")
        before = set(self._remote_image_state(session, role).get("urls") or [])
        attempted_selectors = tuple(
            dict.fromkeys(
                selector_candidates
                if role == "cover"
                else (*selector_candidates, *GENERIC_IMAGE_FILE_SELECTORS)
            )
        )
        trigger_status = "未触发上传入口"
        self._trace(
            "upload.start",
            role=role,
            label=label,
            file={"name": path.name, "size": path.stat().st_size},
            page_url=self._safe_page_url(session.url),
            selectors=list(attempted_selectors),
            before_count=len(before),
        )
        if self._trace_path:
            self._trace(
                "upload.input_diagnostics.before_trigger",
                role=role,
                diagnostics=self._file_input_diagnostics(session, attempted_selectors),
            )
            if role == "cover":
                self._trace(
                    "upload.cover_area_diagnostics.before_trigger",
                    role=role,
                    diagnostics=self._upload_area_diagnostics(session, role),
                )
        if role == "cover":
            chooser_uploaded = self._set_cover_file_via_file_chooser(session, path)
            self._trace("upload.cover_file_chooser", role=role, uploaded=chooser_uploaded)
            if chooser_uploaded:
                uploaded = self._wait_for_uploaded_image(session, role, before)
                self._trace("upload.completed", role=role, remote_url=uploaded.get("remote_url", ""))
                return uploaded
        elif role == "inline":
            chooser_uploaded = self._set_inline_file_via_file_chooser(
                session, path, attempted_selectors, label
            )
            self._trace("upload.inline_file_chooser", role=role, uploaded=chooser_uploaded)
            if chooser_uploaded:
                uploaded = self._wait_for_uploaded_image(session, role, before)
                self._trace("upload.completed", role=role, remote_url=uploaded.get("remote_url", ""))
                return uploaded
        if role == "cover":
            triggered = self._trigger_upload_entry(session, role)
            trigger_status = "已触发上传入口" if triggered else "未触发上传入口"
            self._trace("upload.trigger", role=role, triggered=triggered)
            if self._trace_path:
                self._trace(
                    "upload.input_diagnostics.after_trigger",
                    role=role,
                    diagnostics=self._file_input_diagnostics(session, attempted_selectors),
                )
                self._trace(
                    "upload.cover_area_diagnostics.after_trigger",
                    role=role,
                    diagnostics=self._upload_area_diagnostics(session, role),
                )
        reference = self._resolve_file_input_reference(
            session,
            selector_candidates,
            require_visible_control=True,
            role=role,
            label=label,
        )
        self._trace(
            "upload.input_evaluated",
            role=role,
            found=bool(reference.get("object_id") or reference.get("backend_node_id")),
            uses_backend_node_id=bool(reference.get("backend_node_id")),
        )
        if not (reference.get("object_id") or reference.get("backend_node_id")):
            triggered = self._trigger_upload_entry(session, role)
            trigger_status = "已触发上传入口" if triggered else "未触发上传入口"
            self._trace("upload.trigger", role=role, triggered=triggered)
            if self._trace_path:
                self._trace(
                    "upload.input_diagnostics.after_trigger",
                    role=role,
                    diagnostics=self._file_input_diagnostics(session, attempted_selectors),
                )
                if role == "cover":
                    self._trace(
                        "upload.cover_area_diagnostics.after_retry_trigger",
                        role=role,
                        diagnostics=self._upload_area_diagnostics(session, role),
                    )
            reference = self._resolve_file_input_reference(
                session,
                attempted_selectors,
                require_visible_control=True,
                role=role,
                label=label,
            )
            self._trace(
                "upload.input_evaluated_after_trigger",
                role=role,
                found=bool(reference.get("object_id") or reference.get("backend_node_id")),
                uses_backend_node_id=bool(reference.get("backend_node_id")),
            )
            if not (reference.get("object_id") or reference.get("backend_node_id")) and triggered:
                reference = self._resolve_file_input_reference(
                    session,
                    attempted_selectors,
                    require_visible_control=False,
                    role=role,
                    label=label,
                )
                trigger_status = f"{trigger_status}，已允许隐藏上传控件"
                self._trace(
                    "upload.hidden_input_evaluated",
                    role=role,
                    found=bool(reference.get("object_id") or reference.get("backend_node_id")),
                    uses_backend_node_id=bool(reference.get("backend_node_id")),
                )
        if not (reference.get("object_id") or reference.get("backend_node_id")):
            self._trace("upload.input_not_found", role=role, trigger_status=trigger_status)
            raise WeChatPublishingError(
                f"微信编辑器未找到{label}上传控件（{trigger_status}），已尝试选择器：{', '.join(attempted_selectors)}"
            )
        try:
            self._set_file_input_files_with_retry(
                session,
                reference,
                path,
                attempted_selectors,
                label,
                role,
            )
            uploaded = self._wait_for_uploaded_image(session, role, before)
            self._trace("upload.completed", role=role, remote_url=uploaded.get("remote_url", ""))
            return uploaded
        except Exception as exc:
            if self._trace_path and role == "cover":
                self._trace(
                    "upload.cover_area_diagnostics.on_failure",
                    role=role,
                    diagnostics=self._upload_area_diagnostics(session, role),
                )
            self._trace("upload.failed", role=role, label=label, error=str(exc))
            raise

    def _set_file_input_files_with_retry(
        self,
        session: BrowserSession,
        reference: Mapping[str, Any],
        path: Path,
        selectors: Sequence[str],
        label: str,
        role: str,
    ) -> None:
        current_reference = dict(reference)
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                params = self._file_upload_params(session, current_reference, path)
                self._trace(
                    "upload.set_files_attempt",
                    role=role,
                    label=label,
                    attempt=attempt + 1,
                    uses_backend_node_id=bool(params.get("backendNodeId")),
                    uses_node_id=bool(params.get("nodeId")),
                    has_object_id=bool(params.get("objectId")),
                )
                self._cdp_call(
                    session,
                    "DOM.setFileInputFiles",
                    params,
                )
                self._trace("upload.set_files_succeeded", role=role, attempt=attempt + 1)
                return
            except Exception as exc:
                self._trace(
                    "upload.set_files_failed",
                    role=role,
                    label=label,
                    attempt=attempt + 1,
                    stale=self._is_stale_file_input_error(exc),
                    error=str(exc),
                )
                if not self._is_stale_file_input_error(exc) or attempt == 2:
                    raise
                last_error = exc
                time.sleep(0.2)
                triggered = self._trigger_upload_entry(session, role)
                self._trace(
                    "upload.input_stale_retry_prepare",
                    role=role,
                    label=label,
                    attempt=attempt + 2,
                    triggered=triggered,
                )
                current_reference = self._resolve_file_input_reference(
                    session,
                    selectors,
                    require_visible_control=True,
                    role=role,
                    label=label,
                )
                if not (
                    current_reference.get("object_id")
                    or current_reference.get("backend_node_id")
                    or current_reference.get("node_id")
                ):
                    current_reference = self._resolve_file_input_reference(
                        session,
                        selectors,
                        require_visible_control=False,
                        role=role,
                        label=label,
                    )
                if not (
                    current_reference.get("object_id")
                    or current_reference.get("backend_node_id")
                    or current_reference.get("node_id")
                ):
                    self._trace("upload.input_reresolve_failed", role=role, label=label)
                    raise WeChatPublishingError(
                        f"微信编辑器{label}上传控件已刷新，但重新定位失败"
                    ) from last_error
                self._trace(
                    "upload.input_reresolved",
                    role=role,
                    label=label,
                    uses_backend_node_id=bool(current_reference.get("backend_node_id")),
                    uses_node_id=bool(current_reference.get("node_id")),
                    has_object_id=bool(current_reference.get("object_id")),
                )

    def _set_cover_file_via_file_chooser(self, session: BrowserSession, path: Path) -> bool:
        connection = None
        call_id = 0
        chooser_backend_node_id = 0

        def send(method: str, params: Mapping[str, Any] | None = None) -> int:
            nonlocal call_id
            call_id += 1
            connection.send(
                json.dumps({"id": call_id, "method": method, "params": dict(params or {})})
            )
            return call_id

        def recv_until(response_id: int, timeout: float = 5) -> dict[str, Any]:
            nonlocal chooser_backend_node_id
            deadline = time.time() + timeout
            while time.time() < deadline:
                payload = json.loads(connection.recv())
                if payload.get("method") == "Page.fileChooserOpened":
                    params = payload.get("params") or {}
                    chooser_backend_node_id = int(params.get("backendNodeId") or 0)
                    self._trace(
                        "upload.cover_file_chooser_opened",
                        backend_node_id_found=bool(chooser_backend_node_id),
                    )
                    continue
                if payload.get("id") != response_id:
                    continue
                if payload.get("error"):
                    message = payload["error"].get("message") or "CDP 调用失败"
                    raise WeChatPublishingError(f"{payload.get('method') or 'CDP'}：{message}")
                return payload.get("result") or {}
            raise WeChatPublishingError("等待微信封面文件选择器超时")

        def wait_for_file_chooser(timeout: float = 2) -> None:
            nonlocal chooser_backend_node_id
            deadline = time.time() + timeout
            while not chooser_backend_node_id and time.time() < deadline:
                payload = json.loads(connection.recv())
                if payload.get("method") == "Page.fileChooserOpened":
                    params = payload.get("params") or {}
                    chooser_backend_node_id = int(params.get("backendNodeId") or 0)
                    self._trace(
                        "upload.cover_file_chooser_opened",
                        backend_node_id_found=bool(chooser_backend_node_id),
                    )

        try:
            connection = websocket.create_connection(session.websocket_url, timeout=5)
            recv_until(
                send("Page.setInterceptFileChooserDialog", {"enabled": True}),
            )
            target_result = recv_until(
                send(
                    "Runtime.evaluate",
                    {
                        "expression": self._cover_upload_target_expression(),
                        "returnByValue": True,
                        "awaitPromise": True,
                    },
                )
            )
            value = ((target_result.get("result") or {}).get("value") or {})
            if not isinstance(value, Mapping) or not value.get("ok"):
                self._trace("upload.cover_file_chooser_target_not_found", result=value)
                return False
            rect = value.get("rect") or {}
            width = float(rect.get("width") or 0)
            height = float(rect.get("height") or 0)
            if width <= 0 or height <= 0:
                self._trace("upload.cover_file_chooser_target_invalid", rect=rect)
                return False
            x = round(float(rect.get("x") or 0) + width / 2)
            y = round(float(rect.get("y") or 0) + height / 2)
            for params in (
                {"type": "mouseMoved", "x": x, "y": y, "button": "none"},
                {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
                {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
            ):
                recv_until(send("Input.dispatchMouseEvent", params))
            if not chooser_backend_node_id:
                wait_for_file_chooser()
            if not chooser_backend_node_id:
                self._trace("upload.cover_file_chooser_not_opened")
                return False
            recv_until(
                send(
                    "DOM.setFileInputFiles",
                    {"files": [str(path)], "backendNodeId": chooser_backend_node_id},
                )
            )
            self._trace(
                "upload.cover_file_chooser_set_files",
                backend_node_id_found=True,
            )
            return True
        except Exception as exc:
            self._trace("upload.cover_file_chooser_failed", error=str(exc))
            return False
        finally:
            if connection is not None:
                try:
                    recv_until(
                        send("Page.setInterceptFileChooserDialog", {"enabled": False}),
                        timeout=2,
                    )
                except Exception:
                    pass
                connection.close()

    def _set_inline_file_via_file_chooser(
        self,
        session: BrowserSession,
        path: Path,
        selectors: Sequence[str],
        label: str,
    ) -> bool:
        connection = None
        call_id = 0
        chooser_backend_node_id = 0

        def send(method: str, params: Mapping[str, Any] | None = None) -> int:
            nonlocal call_id
            call_id += 1
            connection.send(
                json.dumps({"id": call_id, "method": method, "params": dict(params or {})})
            )
            return call_id

        def recv_until(response_id: int, timeout: float = 5) -> dict[str, Any]:
            nonlocal chooser_backend_node_id
            deadline = time.time() + timeout
            while time.time() < deadline:
                payload = json.loads(connection.recv())
                if payload.get("method") == "Page.fileChooserOpened":
                    params = payload.get("params") or {}
                    chooser_backend_node_id = int(params.get("backendNodeId") or 0)
                    self._trace(
                        "upload.inline_file_chooser_opened",
                        backend_node_id_found=bool(chooser_backend_node_id),
                    )
                    continue
                if payload.get("id") != response_id:
                    continue
                if payload.get("error"):
                    message = payload["error"].get("message") or "CDP 调用失败"
                    raise WeChatPublishingError(f"{payload.get('method') or 'CDP'}：{message}")
                return payload.get("result") or {}
            raise WeChatPublishingError("等待微信正文图片文件选择器超时")

        def wait_for_file_chooser(timeout: float = 1) -> None:
            nonlocal chooser_backend_node_id
            deadline = time.time() + timeout
            while not chooser_backend_node_id and time.time() < deadline:
                try:
                    payload = json.loads(connection.recv())
                except Exception as exc:
                    timeout_error = getattr(websocket, "WebSocketTimeoutException", None)
                    if (timeout_error and isinstance(exc, timeout_error)) or "timed out" in str(exc).lower():
                        self._trace(
                            "upload.inline_file_chooser_wait_timeout",
                            label=label,
                            timeout=timeout,
                        )
                        return
                    raise
                if payload.get("method") == "Page.fileChooserOpened":
                    params = payload.get("params") or {}
                    chooser_backend_node_id = int(params.get("backendNodeId") or 0)
                    self._trace(
                        "upload.inline_file_chooser_opened",
                        backend_node_id_found=bool(chooser_backend_node_id),
                    )

        def value_from_runtime(result: Mapping[str, Any]) -> Mapping[str, Any]:
            runtime_result = result.get("result") if isinstance(result, Mapping) else {}
            value = runtime_result.get("value") if isinstance(runtime_result, Mapping) else {}
            return value if isinstance(value, Mapping) else {}

        def click_rect(rect: Mapping[str, Any]) -> None:
            width = float(rect.get("width") or 0)
            height = float(rect.get("height") or 0)
            if width <= 0 or height <= 0:
                raise WeChatPublishingError("微信正文图片上传入口尺寸无效")
            x = round(float(rect.get("x") or 0) + width / 2)
            y = round(float(rect.get("y") or 0) + height / 2)
            for params in (
                {"type": "mouseMoved", "x": x, "y": y, "button": "none"},
                {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
                {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
            ):
                recv_until(send("Input.dispatchMouseEvent", params))

        try:
            connection = websocket.create_connection(session.websocket_url, timeout=5)
            recv_until(send("Page.setInterceptFileChooserDialog", {"enabled": True}))
            trigger_result = recv_until(
                send(
                    "Runtime.evaluate",
                        {
                            "expression": self._upload_trigger_expression("inline"),
                            "returnByValue": True,
                            "awaitPromise": True,
                            "userGesture": True,
                        },
                    )
                )
            trigger_value = value_from_runtime(trigger_result)
            self._trace(
                "upload.inline_file_chooser_trigger",
                triggered=bool(trigger_value.get("ok")),
            )
            if not chooser_backend_node_id:
                wait_for_file_chooser(timeout=0.5)
            if not chooser_backend_node_id:
                target_result = recv_until(
                    send(
                        "Runtime.evaluate",
                        {
                            "expression": self._file_input_click_target_expression(selectors),
                            "returnByValue": True,
                            "awaitPromise": True,
                            "userGesture": True,
                        },
                    )
                )
                target_value = value_from_runtime(target_result)
                self._trace(
                    "upload.inline_file_chooser_target",
                    label=label,
                    result=dict(target_value),
                )
                if not target_value.get("ok"):
                    self._trace(
                        "upload.inline_file_chooser_target_not_found",
                        result=dict(target_value),
                    )
                    return False
                rect = target_value.get("rect") or {}
                if not isinstance(rect, Mapping):
                    self._trace(
                        "upload.inline_file_chooser_target_invalid",
                        result=dict(target_value),
                    )
                    return False
                self._trace(
                    "upload.inline_file_chooser_click_target",
                    label=label,
                    rect=dict(rect),
                    clicked=str(target_value.get("clicked") or ""),
                )
                if not chooser_backend_node_id:
                    wait_for_file_chooser(timeout=0.25)
                if not chooser_backend_node_id:
                    click_rect(rect)
            if not chooser_backend_node_id:
                wait_for_file_chooser(timeout=2)
            if not chooser_backend_node_id:
                self._trace("upload.inline_file_chooser_not_opened", label=label)
                return False
            recv_until(
                send(
                    "DOM.setFileInputFiles",
                    {"files": [str(path)], "backendNodeId": chooser_backend_node_id},
                )
            )
            self._trace(
                "upload.inline_file_chooser_set_files",
                backend_node_id_found=True,
            )
            return True
        except Exception as exc:
            self._trace("upload.inline_file_chooser_failed", label=label, error=str(exc))
            return False
        finally:
            if connection is not None:
                try:
                    recv_until(
                        send("Page.setInterceptFileChooserDialog", {"enabled": False}),
                        timeout=2,
                    )
                except Exception:
                    pass
                connection.close()

    def _resolve_file_input_reference(
        self,
        session: BrowserSession,
        selectors: Sequence[str],
        require_visible_control: bool = True,
        role: str = "inline",
        label: str = "图片",
    ) -> dict[str, Any]:
        marker = f"codex-upload-{secrets.token_hex(12)}"
        payload = self._cdp_call(
            session,
            "Runtime.evaluate",
            {
                "expression": self._file_input_expression(
                    selectors,
                    require_visible_control=require_visible_control,
                    marker=marker,
                ),
                "returnByValue": False,
            },
        )
        exception = payload.get("exceptionDetails") or {}
        if exception:
            detail = exception.get("exception") or {}
            message = str(detail.get("description") or exception.get("text") or "上传控件无效")
            self._trace("upload.input_evaluate_failed", role=role, label=label, error=message)
            raise WeChatPublishingError(f"微信编辑器{label}上传控件无效：{message}")
        remote = payload.get("result") or {}
        object_id = str(remote.get("objectId") or "")
        backend_node_id = self._backend_node_id_for_marker(session, marker)
        self._trace(
            "upload.backend_node_resolved",
            role=role,
            label=label,
            found=bool(backend_node_id),
        )
        return {"object_id": object_id, "backend_node_id": backend_node_id}

    def _resolve_file_input_object_id(
        self,
        session: BrowserSession,
        selectors: Sequence[str],
        require_visible_control: bool = True,
    ) -> str:
        return str(
            self._resolve_file_input_reference(
                session,
                selectors,
                require_visible_control=require_visible_control,
            ).get("object_id")
            or ""
        )

    def _file_input_diagnostics(
        self, session: BrowserSession, selectors: Sequence[str]
    ) -> dict[str, Any]:
        expression = f"""
        (() => {{
          const selectors = {json.dumps(list(selectors))};
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const records = [];
          selectors.forEach((selector, priority) => {{
            document.querySelectorAll(selector).forEach((node) => {{
              if (!(node instanceof HTMLInputElement) || node.type !== 'file') return;
              const rect = node.getBoundingClientRect();
              const host = node.closest('label, [role=button], .weui-desktop-upload, .upload-box, [class*=upload]');
              const hostRect = host?.getBoundingClientRect?.();
              records.push({{
                selector,
                priority,
                accept: node.accept || '',
                disabled: !!node.disabled,
                visible: visible(node),
                hostVisible: visible(host),
                rect: {{width: Math.round(rect.width), height: Math.round(rect.height)}},
                hostRect: hostRect ? {{width: Math.round(hostRect.width), height: Math.round(hostRect.height)}} : null,
              }});
            }});
          }});
          return {{count: records.length, records: records.slice(0, 20)}};
        }})()
        """
        try:
            value = self.evaluate(session, expression)
        except Exception as exc:
            return {"error": str(exc)}
        return dict(value) if isinstance(value, Mapping) else {"value": value}

    def _upload_area_diagnostics(
        self, session: BrowserSession, role: str
    ) -> dict[str, Any]:
        texts = COVER_UPLOAD_TEXTS if role == "cover" else INLINE_UPLOAD_TEXTS
        expression = f"""
        (() => {{
          const texts = {json.dumps(list(texts), ensure_ascii=False)};
          const keywords = {json.dumps(["封面", "拖拽", "选择", "上传", "图片"], ensure_ascii=False)};
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const rectOf = (node) => {{
            const rect = node?.getBoundingClientRect?.();
            if (!rect) return null;
            return {{
              x: Math.round(rect.x),
              y: Math.round(rect.y),
              width: Math.round(rect.width),
              height: Math.round(rect.height),
            }};
          }};
          const short = (value) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, 180);
          const domPath = (node) => {{
            const parts = [];
            let current = node;
            while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 8) {{
              let part = current.tagName.toLowerCase();
              if (current.id) part += `#${{current.id}}`;
              const className = short(current.className).split(' ').filter(Boolean).slice(0, 4).join('.');
              if (className) part += `.${{className}}`;
              parts.unshift(part);
              current = current.parentElement;
            }}
            return parts.join(' > ');
          }};
          const fileInputs = [...document.querySelectorAll('input[type="file"]')].map((node, index) => {{
            const host = node.closest('label, button, a, [role=button], .weui-desktop-upload, .upload-box, [class*=upload], [class*=cover], [class*=dialog], [class*=modal]');
            return {{
              index,
              accept: node.accept || '',
              name: node.name || '',
              id: node.id || '',
              className: short(node.className),
              disabled: !!node.disabled,
              visible: visible(node),
              rect: rectOf(node),
              path: domPath(node),
              hostVisible: visible(host),
              hostText: short(host?.innerText || host?.textContent),
              hostClassName: short(host?.className),
              hostRect: rectOf(host),
              hostPath: host ? domPath(host) : '',
            }};
          }});
          const keywordNodes = [...document.querySelectorAll('body *')]
            .filter((node) => visible(node))
            .map((node) => {{
              const text = short(node.innerText || node.textContent || node.getAttribute('aria-label') || node.getAttribute('title'));
              return {{node, text}};
            }})
            .filter((item) => item.text && keywords.some((keyword) => item.text.includes(keyword)))
            .slice(0, 40)
            .map((item) => ({{
              text: item.text,
              tag: item.node.tagName.toLowerCase(),
              id: item.node.id || '',
              className: short(item.node.className),
              rect: rectOf(item.node),
              path: domPath(item.node),
              clickablePath: domPath(item.node.closest('label, button, a, [role=button]') || item.node),
            }}));
          const dialogs = [...document.querySelectorAll('[role="dialog"], .weui-desktop-dialog, [class*=dialog], [class*=modal], [class*=popover]')]
            .filter((node) => visible(node))
            .slice(0, 20)
            .map((node) => ({{
              text: short(node.innerText || node.textContent),
              className: short(node.className),
              rect: rectOf(node),
              path: domPath(node),
              fileInputCount: node.querySelectorAll('input[type="file"]').length,
            }}));
          const iframes = [...document.querySelectorAll('iframe')]
            .slice(0, 20)
            .map((node, index) => ({{
              index,
              src: node.src || '',
              title: node.title || '',
              name: node.name || '',
              id: node.id || '',
              className: short(node.className),
              visible: visible(node),
              rect: rectOf(node),
              path: domPath(node),
            }}));
          return {{
            url: location.href,
            title: document.title,
            viewport: {{width: window.innerWidth, height: window.innerHeight, scrollY: Math.round(window.scrollY)}},
            expectedTexts: texts,
            fileInputs,
            keywordNodes,
            dialogs,
            iframes,
          }};
        }})()
        """
        try:
            value = self.evaluate(session, expression)
        except Exception as exc:
            return {"error": str(exc)}
        return dict(value) if isinstance(value, Mapping) else {"value": value}

    @staticmethod
    def _safe_page_url(value: str) -> str:
        parsed = urllib.parse.urlparse(str(value or ""))
        if parsed.scheme and parsed.hostname:
            return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        return str(value or "")

    @staticmethod
    def _is_stale_file_input_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "could not find object with given id",
                "no node with given id",
                "cannot find context with specified id",
            )
        )

    def _file_upload_params(
        self, session: BrowserSession, reference: Mapping[str, Any], path: Path
    ) -> dict[str, Any]:
        object_id = str(reference.get("object_id") or "")
        backend_node_id = int(reference.get("backend_node_id") or 0)
        node_id = int(reference.get("node_id") or 0)
        if not backend_node_id and not node_id and object_id:
            stable_reference = self._stable_node_reference_for_object(session, object_id)
            backend_node_id = int(stable_reference.get("backend_node_id") or 0)
            node_id = int(stable_reference.get("node_id") or 0)
        params: dict[str, Any] = {"files": [str(path)]}
        if backend_node_id:
            params["backendNodeId"] = backend_node_id
        elif node_id:
            params["nodeId"] = node_id
        elif object_id:
            params["objectId"] = object_id
        else:
            raise WeChatPublishingError("微信编辑器上传控件引用无效")
        return params

    def _stable_node_reference_for_object(
        self, session: BrowserSession, object_id: str
    ) -> dict[str, int]:
        try:
            payload = self._cdp_call(session, "DOM.describeNode", {"objectId": object_id})
            node = payload.get("node") if isinstance(payload, Mapping) else None
            if isinstance(node, Mapping):
                backend_node_id = int(node.get("backendNodeId") or 0)
                node_id = int(node.get("nodeId") or 0)
                if backend_node_id or node_id:
                    return {
                        "backend_node_id": backend_node_id,
                        "node_id": node_id,
                    }
        except Exception as exc:
            self._trace(
                "upload.object_describe_failed",
                error=str(exc),
            )
        try:
            requested = self._cdp_call(session, "DOM.requestNode", {"objectId": object_id})
            node_id = int(requested.get("nodeId") or 0)
            if not node_id:
                return {}
            described = self._cdp_call(session, "DOM.describeNode", {"nodeId": node_id})
            node = described.get("node") if isinstance(described, Mapping) else None
            backend_node_id = (
                int(node.get("backendNodeId") or 0) if isinstance(node, Mapping) else 0
            )
            self._trace(
                "upload.object_requested_as_node",
                found=bool(node_id),
                backend_node_id_found=bool(backend_node_id),
            )
            return {"backend_node_id": backend_node_id, "node_id": node_id}
        except Exception as exc:
            self._trace(
                "upload.object_request_node_failed",
                error=str(exc),
            )
            return {}

    def _backend_node_id_for_marker(self, session: BrowserSession, marker: str) -> int:
        try:
            document = self._cdp_call(
                session,
                "DOM.getDocument",
                {"depth": 1, "pierce": True},
            )
            root = document.get("root") if isinstance(document, Mapping) else None
            root_node_id = int(root.get("nodeId") or 0) if isinstance(root, Mapping) else 0
            if not root_node_id:
                return 0
            result = self._cdp_call(
                session,
                "DOM.querySelector",
                {
                    "nodeId": root_node_id,
                    "selector": f'input[type="file"][data-codex-upload-marker="{marker}"]',
                },
            )
            node_id = int(result.get("nodeId") or 0)
            if not node_id:
                return 0
            described = self._cdp_call(session, "DOM.describeNode", {"nodeId": node_id})
        except Exception:
            return 0
        node = described.get("node") if isinstance(described, Mapping) else None
        if not isinstance(node, Mapping):
            return 0
        try:
            return int(node.get("backendNodeId") or 0)
        except (TypeError, ValueError):
            return 0

    def _backend_node_id_for_object(self, session: BrowserSession, object_id: str) -> int:
        try:
            payload = self._cdp_call(session, "DOM.describeNode", {"objectId": object_id})
        except Exception:
            return 0
        node = payload.get("node") if isinstance(payload, Mapping) else None
        if not isinstance(node, Mapping):
            return 0
        try:
            return int(node.get("backendNodeId") or 0)
        except (TypeError, ValueError):
            return 0

    def screenshot(self, session: BrowserSession, destination: str | Path | None = None) -> str:
        if destination:
            path = Path(destination)
        else:
            descriptor, raw_path = tempfile.mkstemp(prefix="wechat-confirm-", suffix=".png")
            os.close(descriptor)
            path = Path(raw_path)
        result = self._cdp_call(session, "Page.captureScreenshot", {"format": "png", "fromSurface": True})
        data = str(result.get("data") or "")
        if not data:
            raise WeChatPublishingError("微信确认页面截图失败")
        path.write_bytes(base64.b64decode(data))
        path.chmod(0o600)
        return str(path)

    def close(self, session: BrowserSession) -> None:
        self._sessions.pop(session.session_id, None)
        with self._lock:
            handles = [
                handle
                for handle, pending in self._pending.items()
                if pending.session is session
            ]
            pending_items = [self._pending.pop(handle) for handle in handles]
        for pending in pending_items:
            pending.timer.cancel()
        graceful = False
        try:
            self._cdp_call(session, "Browser.close")
            graceful = True
        except Exception:
            pass
        if session.process:
            self._stop_process(session.process, graceful=graceful)
        if session.confirmation_screenshot:
            try:
                Path(session.confirmation_screenshot).unlink(missing_ok=True)
            except OSError:
                pass

    def require_authenticated(self, session: BrowserSession) -> None:
        deadline = time.time() + self.auth_timeout
        last_url = ""
        while True:
            last_url = self._current_url(session)
            parsed = urllib.parse.urlparse(last_url)
            if parsed.scheme != "https" or parsed.hostname != "mp.weixin.qq.com":
                raise WeChatPublishingError("微信公众号当前页面不是 mp.weixin.qq.com，已终止发布")
            token = urllib.parse.parse_qs(parsed.query).get("token", [""])[0].strip()
            if (
                parsed.path.startswith("/cgi-bin/")
                and token
                and self._dashboard_token_verified(session)
            ):
                return
            cookies = self._cdp_call(session, "Network.getAllCookies").get("cookies") or []
            valid_names = {
                str(cookie.get("name") or "")
                for cookie in cookies
                if self._valid_auth_cookie(cookie)
            }
            if {"slave_sid", "bizuin"}.issubset(valid_names):
                return
            if time.time() >= deadline:
                break
            time.sleep(0.5)
        raise WeChatPublishingError(f"微信公众号登录态失效，请重新扫码（当前页面：{last_url or '未知'}）")

    def require_account_identity(
        self, session: BrowserSession, account: Mapping[str, Any]
    ) -> None:
        cookies = self._cdp_call(session, "Network.getAllCookies").get("cookies") or []
        live_external_id = next(
            (
                str(cookie.get("value") or "").strip()
                for cookie in cookies
                if cookie.get("name") == "bizuin" and self._valid_auth_cookie(cookie)
            ),
            "",
        )
        expected_external_id = str(account.get("external_id") or "").strip()
        if not expected_external_id or not live_external_id:
            raise WeChatPublishingError(
                "无法验证当前登录公众号稳定身份，请重新授权所选账号"
            )
        if expected_external_id != live_external_id:
            raise WeChatPublishingError("当前登录公众号与所选账号不一致，已终止发布")

    @staticmethod
    def _valid_auth_cookie(cookie: Mapping[str, Any]) -> bool:
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "").strip()
        domain = str(cookie.get("domain") or "").lstrip(".").lower()
        try:
            expires = float(cookie.get("expires") or 0)
        except (TypeError, ValueError):
            return False
        not_expired = expires <= 0 or expires > time.time()
        return (
            name in {"slave_sid", "bizuin"}
            and bool(value)
            and domain == "mp.weixin.qq.com"
            and not_expired
        )

    def _dashboard_token_verified(self, session: BrowserSession) -> bool:
        expression = """
        (() => {
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const dashboardSelectors = ['.weui-desktop-layout__main', '.weui-desktop-menu', '.weui-desktop-account', '.weui-desktop-account__nickname'];
          const loginSelectors = ['.login__type__container__scan', '.login_qrcode', '[class*=login] [class*=qrcode]'];
          const dashboard = dashboardSelectors.some((selector) => [...document.querySelectorAll(selector)].some(visible));
          const login = loginSelectors.some((selector) => [...document.querySelectorAll(selector)].some(visible));
          return dashboard && !login;
        })()
        """
        return self.evaluate(session, expression) is True

    def open_article_editor(self, session: BrowserSession) -> None:
        current_url = self._current_url(session)
        token = urllib.parse.parse_qs(urllib.parse.urlparse(current_url).query).get("token", [""])[0]
        if token:
            editor_url = (
                "https://mp.weixin.qq.com/cgi-bin/appmsg?"
                + urllib.parse.urlencode(
                    {
                        "t": "media/appmsg_edit_v2",
                        "action": "edit",
                        "type": "10",
                        "isMul": "1",
                        "lang": "zh_CN",
                        "token": token,
                    }
                )
            )
            self.navigate(session, editor_url)
            return
        self._click_first(session, NEW_ARTICLE_SELECTORS, "新建图文入口")
        self._wait_for_document(session)

    def fill_article(self, session: BrowserSession, payload: Mapping[str, Any]) -> None:
        self.fill_article_content(session, payload)
        self.upload_article_assets(session, payload)

    def fill_article_content(
        self, session: BrowserSession, payload: Mapping[str, Any]
    ) -> None:
        self._set_value(session, TITLE_SELECTORS, str(payload.get("title") or ""), "标题")
        author = str(payload.get("author") or "").strip()
        if author:
            self._set_value(session, AUTHOR_SELECTORS, author, "作者", required=False)
        digest = str(payload.get("digest") or "").strip()
        if digest:
            self._set_value(session, DIGEST_SELECTORS, digest, "摘要", required=False)

        self._set_editor_html(session, self._article_body_html(payload))

    def upload_article_assets(
        self, session: BrowserSession, payload: Mapping[str, Any]
    ) -> None:
        cover_path = str(payload.get("cover_path") or "").strip()
        if cover_path:
            article_image_paths = self._article_image_paths(
                payload, include_cover_candidate=True
            )
            self._trace(
                "upload.cover_from_article_image_picker_primary",
                article_image_count=len(article_image_paths),
                cover_candidate_in_body=bool(self._cover_candidate_path(payload)),
            )
            if not article_image_paths:
                raise WeChatPublishingError(
                    "微信发布需要封面，但正文没有可用于从正文选择封面的图片；请在排版中插入一张图片，或先同步到平台草稿箱"
                )
            self._set_cover_from_article_image_picker(
                session,
                cover_path,
                "",
            )

    @staticmethod
    def _is_cover_upload_input_missing_error(exc: Exception) -> bool:
        message = str(exc)
        return "封面图上传控件" in message and "未找到" in message

    @classmethod
    def _article_image_paths(
        cls, payload: Mapping[str, Any], include_cover_candidate: bool = False
    ) -> list[str]:
        image_paths: list[str] = []
        cover_candidate_path = cls._cover_candidate_path(payload)
        if include_cover_candidate and cover_candidate_path:
            image_paths.append(cover_candidate_path)
        for block in payload.get("blocks") or []:
            paths = block.get("image_paths")
            if isinstance(paths, list):
                for path in paths:
                    image_path = str(path or "").strip()
                    if image_path and image_path not in image_paths:
                        image_paths.append(image_path)
            fallback_path = str(block.get("image_path") or "").strip()
            if fallback_path and fallback_path not in image_paths:
                image_paths.append(fallback_path)
        return image_paths

    def _article_body_html(self, payload: Mapping[str, Any]) -> str:
        sections = []
        cover_candidate_path = self._cover_candidate_path(payload)
        if cover_candidate_path:
            sections.append(
                self._inline_image_html(
                    cover_candidate_path,
                    extra_attributes='data-wechat-cover-candidate="true"',
                )
            )
        for block in payload.get("blocks") or []:
            content = str(block.get("content") or "").strip()
            block_type = str(block.get("type") or "body")
            if content:
                sections.append(self._block_content_html(block_type, content))
            image_paths = block.get("image_paths")
            if isinstance(image_paths, list):
                paths = [str(path).strip() for path in image_paths if str(path or "").strip()]
            else:
                paths = []
            fallback_path = str(block.get("image_path") or "").strip()
            if fallback_path and fallback_path not in paths:
                paths.append(fallback_path)
            for image_path in paths:
                sections.append(self._inline_image_html(image_path))
        return "".join(sections)

    @staticmethod
    def _cover_candidate_path(payload: Mapping[str, Any]) -> str:
        return str(
            payload.get("cover_candidate_path")
            or payload.get("cover_path")
            or ""
        ).strip()

    @staticmethod
    def _block_content_html(block_type: str, content: str) -> str:
        escaped = html.escape(content).replace(chr(10), "<br>")
        if block_type == "heading":
            return f"<h2>{escaped}</h2>"
        if block_type == "intro":
            return f"<blockquote>{escaped}</blockquote>"
        return f"<p>{escaped}</p>"

    @staticmethod
    def _inline_image_html(image_path: str, extra_attributes: str = "") -> str:
        attributes = f" {extra_attributes.strip()}" if extra_attributes.strip() else ""
        if image_path.startswith("data:image/"):
            return (
                "<p>"
                f"<img src=\"{html.escape(image_path, quote=True)}\" "
                "style=\"max-width:100%;height:auto;display:block;margin:16px auto;\""
                f"{attributes} />"
                "</p>"
            )
        if image_path.startswith("http://") or image_path.startswith("https://"):
            return (
                "<p>"
                f"<img src=\"{html.escape(image_path, quote=True)}\" "
                "style=\"max-width:100%;height:auto;display:block;margin:16px auto;\""
                f"{attributes} />"
                "</p>"
            )
        path = Path(image_path).expanduser()
        if not path.is_file():
            raise WeChatPublishingError(f"正文配图文件不存在：{path}")
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
        if not mime_type.startswith("image/"):
            mime_type = "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return (
            "<p>"
            f"<img src=\"data:{mime_type};base64,{encoded}\" "
            "style=\"max-width:100%;height:auto;display:block;margin:16px auto;\""
            f"{attributes} />"
            "</p>"
        )

    def click_save_draft(self, session: BrowserSession) -> None:
        if not self._click_first(session, SAVE_DRAFT_SELECTORS, "保存草稿按钮", required=False):
            self._click_by_text(session, SAVE_DRAFT_TEXTS, "保存草稿按钮")

    def click_publish(self, session: BrowserSession) -> None:
        self._click_first(session, PUBLISH_SELECTORS, "发表按钮")
        time.sleep(0.8)
        self._click_first(session, PUBLISH_CONFIRM_SELECTORS, "发表确认按钮", required=False)

    def capture_baseline(
        self, session: BrowserSession, title: str, action: str
    ) -> dict[str, Any]:
        baseline_id = secrets.token_urlsafe(24)
        self._install_operation_marker(session, baseline_id)
        observation = self.evaluate(
            session,
            self._observation_expression(
                title,
                "draft" if action == "draft" else "publish",
                include_hidden=True,
            ),
        )
        if not isinstance(observation, Mapping):
            raise WeChatPublishingError("微信页面未返回操作前基线")
        self._require_wechat_observation_url(str(observation.get("url") or ""))
        if observation.get("baseline_complete") is not True:
            raise WeChatPublishingError("微信操作前基线采集不完整，已中止发布，请刷新页面后重试")
        receipts = []
        seen_ids = set()
        for receipt in observation.get("receipts") or []:
            if not isinstance(receipt, Mapping):
                continue
            receipt_id = str(receipt.get("id") or "").strip()
            if not receipt_id or receipt_id in seen_ids:
                continue
            seen_ids.add(receipt_id)
            receipts.append(
                {
                    "id": receipt_id,
                    "title": normalize_wechat_title(receipt.get("title")),
                    "source": str(receipt.get("source") or ""),
                }
            )
        return {
            "baseline_id": baseline_id,
            "baseline_complete": True,
            "receipt_ids": [str(receipt["id"]) for receipt in receipts],
            "receipts": receipts,
            "signal_kinds": [
                str(signal.get("kind") or "")
                for signal in observation.get("signals") or []
                if isinstance(signal, Mapping)
            ],
            "url": str(observation.get("url") or ""),
            "title": title,
            "action": action,
        }

    def verify_draft(
        self,
        session: BrowserSession,
        title: str,
        baseline: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._wait_for_receipt(session, title, baseline, kind="draft")

    def verify_publish_or_confirmation(
        self,
        session: BrowserSession,
        title: str,
        baseline: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._wait_for_receipt(session, title, baseline, kind="publish")

    def _wait_for_receipt(
        self,
        session: BrowserSession,
        title: str,
        baseline: Mapping[str, Any],
        kind: str,
    ) -> dict[str, Any]:
        expected_action = "draft" if kind == "draft" else "publish"
        if baseline.get("action") != expected_action or baseline.get("title") != title:
            raise WeChatPublishingError("微信操作基线与当前发布动作不匹配")
        deadline = time.time() + self.result_timeout
        while True:
            observation = self.evaluate(
                session, self._observation_expression(title, kind, include_hidden=False)
            )
            if not isinstance(observation, dict):
                observation = {}
            platform_url = self._require_wechat_observation_url(
                str(observation.get("url") or "")
            )
            same_baseline = str(observation.get("baseline_id") or "") == str(
                baseline.get("baseline_id") or ""
            )
            if (
                kind == "publish"
                and same_baseline
                and observation.get("confirmation")
                and observation.get("confirmation_after_baseline")
            ):
                screenshot = self.screenshot(session)
                session.confirmation_screenshot = screenshot
                return {
                    "status": "awaiting_confirmation",
                    "platform_url": platform_url,
                    "baseline_id": baseline.get("baseline_id"),
                    "confirmation": {
                        "kind": "operator_qr",
                        "screenshot_available": bool(screenshot),
                    },
                }
            receipt = self._new_receipt(observation, baseline, title) if same_baseline else None
            signal = (
                self._post_action_signal(observation, baseline, expected_action)
                if same_baseline
                else None
            )
            if receipt and signal:
                platform_id = str(receipt["id"])
                return {
                    "status": "platform_draft" if kind == "draft" else "published",
                    "draft_id" if kind == "draft" else "publish_id": platform_id,
                    "platform_url": platform_url,
                    "baseline_id": baseline.get("baseline_id"),
                    "receipt": {
                        "id": platform_id,
                        "title": receipt.get("title"),
                        "source": receipt.get("source"),
                    },
                    "post_action_signal": {
                        "kind": signal.get("kind"),
                        "observed_after_action": True,
                    },
                }
            if time.time() >= deadline:
                break
            time.sleep(0.5)
        label = "草稿" if kind == "draft" else "发布"
        raise WeChatPublishingError(f"微信页面未返回可验证的{label}结果，请检查浏览器页面后重试")

    def _install_operation_marker(
        self, session: BrowserSession, baseline_id: str
    ) -> None:
        expression = f"""
        (() => {{
          window.__codexWechatOperation?.observer?.disconnect();
          const changed = new WeakSet();
          const markAncestors = (node) => {{
            const element = node instanceof Element ? node : node?.parentElement;
            if (!element) return;
            let current = element;
            for (let depth = 0; current && depth < 6; depth += 1, current = current.parentElement) changed.add(current);
          }};
          const markAddedTree = (node) => {{
            const element = node instanceof Element ? node : node?.parentElement;
            if (!element) return;
            markAncestors(element);
            for (const child of element.querySelectorAll('*')) changed.add(child);
          }};
          const observer = new MutationObserver((records) => {{
            for (const record of records) {{
              if (record.type === 'childList') {{
                for (const node of record.addedNodes) markAddedTree(node);
              }} else {{
                markAncestors(record.target);
              }}
            }}
          }});
          observer.observe(document.documentElement, {{subtree: true, childList: true, attributes: true, characterData: true}});
          window.__codexWechatOperation = {{id: {json.dumps(baseline_id)}, changed, observer}};
          return true;
        }})()
        """
        if self.evaluate(session, expression) is not True:
            raise WeChatPublishingError("微信页面无法建立本次发布操作基线")

    @staticmethod
    def _baseline_state_collector_source() -> str:
        return """
        (stateRoots, normalizeTitle, maxDepth, maxEntries) => {
          const receipts = [];
          let baselineComplete = true;
          const baselineIncompleteReasons = [];
          const markBaselineIncomplete = (root, kind, details = {}) => {
            baselineComplete = false;
            baselineIncompleteReasons.push({root, kind, ...details});
          };
          const collectStateRecords = (value, rootName) => {
            const seen = new WeakSet();
            let entries = 0;
            const idKeys = ['draft_id', 'draftId', 'media_id', 'mediaId', 'appmsgid', 'appMsgId', 'publish_id', 'publishId', 'msgid', 'msgId'];
            const titleKeys = ['title', 'appmsg_title', 'appMsgTitle', 'name'];
            const visit = (current, depth) => {
              if (!current || typeof current !== 'object' || seen.has(current)) return;
              if (depth > maxDepth) {
                markBaselineIncomplete(rootName, 'depth_budget_exceeded', {depth});
                return;
              }
              seen.add(current);
              const values = new Map();
              const read = (key) => {
                if (values.has(key)) return values.get(key);
                try {
                  const result = current[key];
                  values.set(key, result);
                  return result;
                } catch (_error) {
                  markBaselineIncomplete(rootName, 'state_access_failed');
                  return undefined;
                }
              };
              const idKey = idKeys.find((key) => ['string', 'number'].includes(typeof read(key)));
              if (idKey) {
                const titleKey = titleKeys.find((key) => typeof read(key) === 'string');
                receipts.push({
                  id: String(read(idKey)).trim(),
                  title: normalizeTitle(titleKey ? read(titleKey) : ''),
                  source: 'platform_state',
                  observed_after_baseline: false,
                  visible: false
                });
              }
              try {
                for (const key in current) {
                  if (!Object.prototype.hasOwnProperty.call(current, key)) continue;
                  if (entries >= maxEntries) {
                    markBaselineIncomplete(rootName, 'entry_budget_exceeded', {entries: entries + 1});
                    return;
                  }
                  entries += 1;
                  visit(read(key), depth + 1);
                }
              } catch (_error) {
                markBaselineIncomplete(rootName, 'state_access_failed');
              }
            };
            visit(value, 0);
          };
          for (const [rootName, root] of stateRoots) collectStateRecords(root, rootName);
          return {
            receipts,
            baseline_complete: baselineComplete,
            baseline_incomplete_reasons: baselineIncompleteReasons
          };
        }
        """.strip()

    @staticmethod
    def _observation_expression(
        title: str, kind: str, include_hidden: bool = False
    ) -> str:
        del title
        draft_attributes = ["data-draft-id", "data-media-id", "data-appmsgid", "data-id"]
        publish_attributes = ["data-publish-id", "data-msg-id", "data-id"]
        draft_queries = ["draft_id", "media_id", "appmsgid"]
        publish_queries = ["publish_id", "msgid"]
        draft_selectors = ["[data-draft-id]", "[data-media-id]", ".js_draft_item[data-id]", ".appmsg[data-appmsgid]"]
        publish_selectors = ["[data-publish-id]", "[data-msg-id]", ".publish_success[data-id]", ".js_publish_success[data-id]"]
        if include_hidden:
            id_attributes = list(dict.fromkeys(draft_attributes + publish_attributes))
            query_names = list(dict.fromkeys(draft_queries + publish_queries))
            success_selectors = list(dict.fromkeys(draft_selectors + publish_selectors))
        elif kind == "draft":
            id_attributes, query_names, success_selectors = (
                draft_attributes,
                draft_queries,
                draft_selectors,
            )
        else:
            id_attributes, query_names, success_selectors = (
                publish_attributes,
                publish_queries,
                publish_selectors,
            )
        signal_kind = "draft_saved" if kind == "draft" else "publish_success"
        success_words = (
            ["保存成功", "草稿已保存", "已保存至草稿"]
            if kind == "draft"
            else ["发表成功", "发送成功", "群发成功", "发布成功"]
        )
        state_collector = ChromeCDPBrowserGateway._baseline_state_collector_source()
        return f"""
        (() => {{
          const selectors = {json.dumps(success_selectors)};
          const attributes = {json.dumps(id_attributes)};
          const queryNames = {json.dumps(query_names)};
          const includeHidden = {json.dumps(include_hidden)};
          const confirmationSelectors = ['.js_qrcode', '.weui-desktop-qrcheck', '[class*=qrcode]', '[class*=qr_code]'];
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const normalizeTitle = (value) => (value || '').normalize('NFKC').replace(/\\s+/gu, ' ').trim();
          const operation = window.__codexWechatOperation;
          const changed = (node) => !!operation?.changed?.has(node);
          const bodyText = document.body?.innerText || '';
          const confirmationText = ['管理员扫码', '微信扫码确认', '运营者扫码', '管理员确认'].find((text) => bodyText.includes(text));
          let confirmation = false;
          if (confirmationText) {{
            confirmation = confirmationSelectors.some((selector) => [...document.querySelectorAll(selector)].some(visible));
          }}
          const confirmationAfterBaseline = confirmationSelectors.some((selector) => [...document.querySelectorAll(selector)].some((node) => visible(node) && changed(node)));
          const receipts = [];
          for (const selector of selectors) {{
            for (const node of document.querySelectorAll(selector)) {{
              if (!(includeHidden || visible(node))) continue;
              const container = node.closest('li, tr, article, .weui-desktop-panel, .appmsg, .publish_success') || node;
              const titleSelectors = ['[data-title]', '.appmsg_title', '.weui-desktop-mass-appmsg__title', '.js_title', '[class*=title]'];
              const titleNode = titleSelectors.flatMap((candidate) => [...container.querySelectorAll(candidate)]).find((candidate) => includeHidden || visible(candidate));
              const rawTitle = node.getAttribute('data-title') || container.getAttribute?.('data-title') || titleNode?.getAttribute('data-title') || titleNode?.textContent || '';
              const receiptTitle = normalizeTitle(rawTitle);
              let platformId = '';
              for (const attribute of attributes) {{
                const value = node.getAttribute(attribute) || container.getAttribute?.(attribute);
                if (value) {{ platformId = value; break; }}
              }}
              const link = node.closest('a[href]') || node.querySelector?.('a[href]') || container.querySelector?.('a[href]');
              if (!platformId && link?.href) {{
                const parsed = new URL(link.href, location.href);
                for (const queryName of queryNames) {{
                  const value = parsed.searchParams.get(queryName);
                  if (value) {{ platformId = value; break; }}
                }}
              }}
              if (platformId) receipts.push({{id: platformId, title: receiptTitle, source: 'dom', observed_after_baseline: changed(node) || changed(container) || changed(link), visible: visible(node)}});
            }}
          }}
          const stateRoots = [
            ['wx.cgiData', window.wx?.cgiData],
            ['cgiData', window.cgiData],
            ['__INITIAL_STATE__', window.__INITIAL_STATE__],
            ['__INITIAL_DATA__', window.__INITIAL_DATA__],
            ['__NEXT_DATA__', window.__NEXT_DATA__]
          ];
          const stateCollection = includeHidden
            ? ({state_collector})(stateRoots, normalizeTitle, {STATE_TRAVERSAL_MAX_DEPTH}, {STATE_TRAVERSAL_MAX_ENTRIES})
            : {{receipts: [], baseline_complete: true, baseline_incomplete_reasons: []}};
          receipts.push(...stateCollection.receipts);
          const successSelectors = ['.weui-desktop-toast', '.weui-desktop-tips', '[role=alert]', '.js_success', '.publish_success'];
          const successWords = {json.dumps(success_words, ensure_ascii=False)};
          const successMessage = successSelectors.some((selector) => [...document.querySelectorAll(selector)].some((node) => visible(node) && changed(node) && successWords.some((word) => (node.innerText || '').includes(word))));
          const receiptMap = new Map();
          for (const receipt of receipts) {{
            if (!receipt.id) continue;
            const existing = receiptMap.get(receipt.id);
            if (!existing || (!existing.title && receipt.title)) receiptMap.set(receipt.id, receipt);
          }}
          return {{
            url: location.href,
            baseline_id: operation?.id || '',
            confirmation,
            confirmation_after_baseline: confirmationAfterBaseline,
            baseline_complete: stateCollection.baseline_complete,
            baseline_incomplete_reasons: stateCollection.baseline_incomplete_reasons,
            receipts: [...receiptMap.values()],
            signals: successMessage ? [{{kind: {json.dumps(signal_kind)}, observed_after_baseline: true}}] : []
          }};
        }})()
        """

    @staticmethod
    def _new_receipt(
        observation: Mapping[str, Any], baseline: Mapping[str, Any], title: str
    ) -> Mapping[str, Any] | None:
        old_ids = {str(value) for value in baseline.get("receipt_ids") or []}
        for receipt in observation.get("receipts") or []:
            if not isinstance(receipt, Mapping):
                continue
            receipt_id = str(receipt.get("id") or "").strip()
            receipt_title = normalize_wechat_title(receipt.get("title"))
            if (
                receipt_id
                and receipt_id not in old_ids
                and receipt_title == normalize_wechat_title(title)
                and receipt.get("source") in {"dom", "api"}
                and receipt.get("observed_after_baseline") is True
            ):
                return receipt
        return None

    @staticmethod
    def _post_action_signal(
        observation: Mapping[str, Any], baseline: Mapping[str, Any], action: str
    ) -> Mapping[str, Any] | None:
        old_kinds = {str(value) for value in baseline.get("signal_kinds") or []}
        expected_kinds = {
            "draft": {"draft_saved", "draft_api_response"},
            "publish": {"publish_success", "publish_api_response"},
        }.get(action, set())
        for signal in observation.get("signals") or []:
            if not isinstance(signal, Mapping):
                continue
            kind = str(signal.get("kind") or "")
            if (
                kind in expected_kinds
                and kind not in old_kinds
                and signal.get("observed_after_baseline") is True
            ):
                return signal
        return None

    @staticmethod
    def _require_wechat_observation_url(value: str) -> str:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme != "https" or parsed.hostname != "mp.weixin.qq.com":
            raise WeChatPublishingError("微信平台观察页面不是 mp.weixin.qq.com")
        return value

    def _remote_image_state(
        self, session: BrowserSession, role: str
    ) -> dict[str, Any]:
        root_selectors = (
            [".js_cover_area", ".js_cover_upload", ".cover-upload"]
            if role == "cover"
            else [".ProseMirror", "#ueditor_0", ".js_editor"]
        )
        expression = f"""
        (() => {{
          const roots = {json.dumps(root_selectors)}.flatMap((selector) => [...document.querySelectorAll(selector)]);
          const urls = [];
          let loading = false;
          for (const root of roots) {{
            loading ||= !!root.querySelector('[class*=loading], .weui-loading, [aria-busy=true]');
            for (const image of root.querySelectorAll('img[src]')) {{
              try {{
                const parsed = new URL(image.src, location.href);
                if (parsed.protocol === 'https:' && (parsed.hostname.endsWith('.qpic.cn') || parsed.hostname.endsWith('.qlogo.cn'))) urls.push(parsed.href);
              }} catch (_) {{}}
            }}
          }}
          return {{urls: [...new Set(urls)], loading}};
        }})()
        """
        result = self.evaluate(session, expression)
        return dict(result) if isinstance(result, Mapping) else {"urls": [], "loading": True}

    def _wait_for_uploaded_image(
        self, session: BrowserSession, role: str, before: set[str]
    ) -> dict[str, Any]:
        deadline = time.time() + self.upload_timeout
        while True:
            state = self._remote_image_state(session, role)
            urls = {
                str(value)
                for value in state.get("urls") or []
                if str(value).startswith("https://")
            }
            new_urls = sorted(urls - before)
            if new_urls and not state.get("loading"):
                return {
                    "role": role,
                    "remote_url": new_urls[0],
                    "source": "wechat_dom",
                }
            if time.time() >= deadline:
                break
            time.sleep(0.25)
        label = "封面图" if role == "cover" else "正文配图"
        raise WeChatPublishingError(f"微信页面未确认上传完成（{label}），已终止保存")

    @staticmethod
    def _file_input_expression(
        selectors: Sequence[str],
        require_visible_control: bool = True,
        marker: str = "",
    ) -> str:
        return f"""
        (() => {{
          const selectors = {json.dumps(list(selectors))};
          const requireVisibleControl = {json.dumps(require_visible_control)};
          const marker = {json.dumps(marker)};
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const uploadHost = (node) => node.closest('label, [role=button], .weui-desktop-upload, .upload-box, [class*=upload], li, button, a');
          const hostVisible = (node) => visible(uploadHost(node));
          const hostArea = (node) => {{
            const rect = uploadHost(node)?.getBoundingClientRect?.();
            return rect ? Math.max(0, rect.width) * Math.max(0, rect.height) : 0;
          }};
          const visibleControl = (node) => visible(node) || hostVisible(node);
          const candidateRecords = [];
          selectors.forEach((selector, priority) => {{
            [...document.querySelectorAll(selector)].forEach((node) => {{
              candidateRecords.push({{node, priority, selector}});
            }});
          }});
          const bestByNode = new Map();
          candidateRecords.forEach((record) => {{
            const existing = bestByNode.get(record.node);
            if (!existing || record.priority < existing.priority) bestByNode.set(record.node, record);
          }});
          const matches = [...bestByNode.values()].filter((record) => {{
            const node = record.node;
            if (!(node instanceof HTMLInputElement) || node.type !== 'file' || node.disabled) return false;
            const accept = (node.accept || '').toLowerCase();
            return (accept.includes('image') || accept.includes('.jpg') || accept.includes('.jpeg') || accept.includes('.png')) && (!requireVisibleControl || visibleControl(node));
          }});
          const scoredMatches = matches.map((record) => ({{
            ...record,
            hostVisible: hostVisible(record.node) ? 1 : 0,
            hostArea: hostArea(record.node),
            nodeVisible: visible(record.node) ? 1 : 0,
          }}));
          scoredMatches.sort((left, right) =>
            left.priority - right.priority ||
            right.hostVisible - left.hostVisible ||
            right.hostArea - left.hostArea ||
            right.nodeVisible - left.nodeVisible
          );
          if (scoredMatches.length >= 1) {{
            const selected = scoredMatches[0];
            const runnerUp = scoredMatches[1];
            if (
              runnerUp &&
              selected.priority === runnerUp.priority &&
              selected.hostVisible === runnerUp.hostVisible &&
              selected.hostArea === runnerUp.hostArea &&
              selected.nodeVisible === runnerUp.nodeVisible
            ) throw new Error(`ambiguous file input (${{scoredMatches.length}} unique nodes)`);
            if (marker) selected.node.setAttribute('data-codex-upload-marker', marker);
            return selected.node;
          }}
          return null;
        }})()
        """

    @staticmethod
    def _file_input_click_target_expression(selectors: Sequence[str]) -> str:
        return f"""
        (() => {{
          const selectors = {json.dumps(list(selectors))};
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const uploadHost = (node) => node.closest('label, [role=button], .weui-desktop-upload, .upload-box, [class*=upload], li, button, a');
          const hostArea = (node) => {{
            const rect = uploadHost(node)?.getBoundingClientRect?.();
            return rect ? Math.max(0, rect.width) * Math.max(0, rect.height) : 0;
          }};
          const candidateRecords = [];
          selectors.forEach((selector, priority) => {{
            [...document.querySelectorAll(selector)].forEach((node) => {{
              candidateRecords.push({{node, priority, selector}});
            }});
          }});
          const bestByNode = new Map();
          candidateRecords.forEach((record) => {{
            const existing = bestByNode.get(record.node);
            if (!existing || record.priority < existing.priority) bestByNode.set(record.node, record);
          }});
          const matches = [...bestByNode.values()].filter((record) => {{
            const node = record.node;
            if (!(node instanceof HTMLInputElement) || node.type !== 'file' || node.disabled) return false;
            const accept = (node.accept || '').toLowerCase();
            const host = uploadHost(node);
            return (accept.includes('image') || accept.includes('.jpg') || accept.includes('.jpeg') || accept.includes('.png')) && visible(host);
          }});
          const scoredMatches = matches.map((record) => ({{
            ...record,
            host: uploadHost(record.node),
            hostArea: hostArea(record.node),
          }}));
          scoredMatches.sort((left, right) => left.priority - right.priority || right.hostArea - left.hostArea);
          if (!scoredMatches.length) return {{ok: false, reason: 'not_found'}};
          const selected = scoredMatches[0];
          const runnerUp = scoredMatches[1];
          if (runnerUp && selected.priority === runnerUp.priority && selected.hostArea === runnerUp.hostArea) {{
            return {{ok: false, reason: 'ambiguous', count: scoredMatches.length}};
          }}
          const host = selected.host;
          host.scrollIntoView({{block: 'center'}});
          let clicked = '';
          try {{
            selected.node.click();
            clicked = 'input';
          }} catch (error) {{}}
          if (!clicked) {{
            try {{
              host.click();
              clicked = 'host';
            }} catch (error) {{}}
          }}
          const rect = host.getBoundingClientRect();
          return {{
            ok: true,
            selector: selected.selector,
            clicked,
            text: String(host.innerText || host.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
            hostClassName: host.className || '',
            rect: {{
              x: Math.round(rect.x),
              y: Math.round(rect.y),
              width: Math.round(rect.width),
              height: Math.round(rect.height),
            }},
          }};
        }})()
        """

    @staticmethod
    def _cover_upload_target_expression() -> str:
        return f"""
        (() => {{
          const selectors = {json.dumps([
              "#js_cover_area .js_cover_btn_area",
              "#js_cover_area",
              "#js_cover_description_area .js_cover_btn_area",
              "#js_cover_description_area [class*='cover']",
          ])};
          const texts = {json.dumps(list(COVER_UPLOAD_TEXTS), ensure_ascii=False)};
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const short = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
          const nodes = [...new Set(selectors.flatMap((selector) => [...document.querySelectorAll(selector)]))];
          const matches = nodes.filter((node) => {{
            if (!visible(node) || node.disabled || node.getAttribute('aria-disabled') === 'true') return false;
            const text = short(node.innerText || node.textContent || node.getAttribute('aria-label') || node.getAttribute('title'));
            return texts.some((value) => text.includes(value));
          }}).map((node) => {{
            const rect = node.getBoundingClientRect();
            return {{
              node,
              area: Math.max(0, rect.width) * Math.max(0, rect.height),
              top: rect.top,
              left: rect.left,
              text: short(node.innerText || node.textContent || ''),
            }};
          }});
          matches.sort((left, right) => left.area - right.area || left.top - right.top || left.left - right.left);
          if (!matches.length) return {{ok: false, reason: 'not_found'}};
          const node = matches[0].node;
          node.scrollIntoView({{block: 'center'}});
          const rect = node.getBoundingClientRect();
          return {{
            ok: true,
            text: matches[0].text,
            rect: {{
              x: Math.round(rect.x),
              y: Math.round(rect.y),
              width: Math.round(rect.width),
              height: Math.round(rect.height),
            }},
          }};
        }})()
        """

    def _close_cover_image_picker_if_open(self, session: BrowserSession) -> None:
        result = self.evaluate(session, self._close_cover_image_picker_expression())
        if isinstance(result, Mapping) and result.get("closed"):
            self._trace("upload.cover_picker_closed")
            time.sleep(0.25)

    @staticmethod
    def _close_cover_image_picker_expression() -> str:
        return """
        (() => {
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const textOf = (node) => String(node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          const dialogs = [...document.querySelectorAll('.weui-desktop-dialog__wrp, .weui-desktop-dialog, [role="dialog"]')]
            .filter((node) => visible(node) && textOf(node).includes('选择图片') && textOf(node).includes('正文插入'));
          if (!dialogs.length) return {closed: false};
          const dialog = dialogs[0];
          const close = [...dialog.querySelectorAll('button, a, [role="button"], .weui-desktop-dialog__close, [class*=close]')]
            .filter(visible)
            .find((node) => ['取消', '关闭', '×', 'x'].some((text) => textOf(node).includes(text) || node.getAttribute('aria-label') === text || node.getAttribute('title') === text));
          if (!close) return {closed: false, reason: 'close_not_found'};
          close.click();
          return {closed: true};
        })()
        """

    def _focus_editor_for_upload(self, session: BrowserSession) -> None:
        result = self.evaluate(session, self._focus_editor_for_upload_expression())
        self._trace(
            "upload.editor_focus_for_inline",
            ok=isinstance(result, Mapping) and bool(result.get("ok")),
            reason=result.get("reason") if isinstance(result, Mapping) else "",
        )

    @staticmethod
    def _focus_editor_for_upload_expression() -> str:
        return f"""
        (() => {{
          const selectors = {json.dumps(list(EDITOR_SELECTORS))};
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const candidates = [...new Set(selectors.flatMap((selector) => [...document.querySelectorAll(selector)]))];
          const matches = candidates.filter((node) => visible(node) && node.isContentEditable && node.getAttribute('aria-disabled') !== 'true');
          if (!matches.length) return {{ok: false, reason: 'editor_not_found'}};
          const node = matches[0];
          node.focus();
          const range = document.createRange();
          range.selectNodeContents(node);
          range.collapse(false);
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          return {{ok: true}};
        }})()
        """

    def _set_cover_from_article_image_picker(
        self,
        session: BrowserSession,
        cover_path: str,
        target_image_url: str = "",
    ) -> dict[str, Any]:
        stage = "start"
        last_snapshot: Mapping[str, Any] = {}
        self._trace(
            "upload.cover_picker_from_article_image.start",
            file={"name": Path(cover_path).name},
            target_image_url=target_image_url,
        )
        try:
            stage = "open_cover_entry"
            triggered = self._trigger_upload_entry(session, "cover")
            self._trace("upload.cover_picker.entry_triggered", triggered=triggered)
            if not triggered:
                raise WeChatPublishingError("微信封面选择入口未打开，无法从正文图片选择封面")

            stage = "open_from_article_entry"
            entry_result = self._wait_for_cover_picker_from_article_entry(session)
            self._trace(
                "upload.cover_picker.from_article_entry",
                result=entry_result,
            )

            stage = "wait_image_candidates"
            snapshot = self._wait_for_cover_picker_snapshot(session, target_image_url)
            last_snapshot = snapshot
            image_count = int(snapshot.get("imageCount") or 0)
            selected_value = snapshot.get("selectedIndex")
            selected_index = int(selected_value if selected_value is not None else -1)
            self._trace(
                "upload.cover_picker.candidates",
                image_count=image_count,
                selected_index=selected_index,
                candidates=snapshot.get("candidates") or [],
                dialog_text=snapshot.get("dialogText") or "",
            )

            stage = "select_image"
            select_result = self.evaluate(
                session,
                self._cover_picker_select_image_expression(selected_index, target_image_url),
            )
            self._trace(
                "upload.cover_picker.image_selected",
                result=select_result if isinstance(select_result, Mapping) else {"value": select_result},
            )
            if not (isinstance(select_result, Mapping) and select_result.get("ok")):
                reason = select_result.get("reason") if isinstance(select_result, Mapping) else "unknown"
                raise WeChatPublishingError(f"未选中正文候选封面图：{reason}")

            if not select_result.get("selectedBefore"):
                click_rect = select_result.get("clickableRect")
                if not isinstance(click_rect, Mapping):
                    raise WeChatPublishingError("正文候选封面图缺少可点击区域")
                self._dispatch_mouse_click(session, click_rect)
                select_result = dict(select_result)
                select_result["clickMethod"] = "cdp_mouse"
                self._trace("upload.cover_picker.image_click_dispatched", result=select_result)
                time.sleep(0.35)

            stage = "wait_image_selected"
            selection_result = self._wait_for_cover_picker_selection(
                session,
                selected_index,
                target_image_url,
            )
            self._trace("upload.cover_picker.image_selection_confirmed", result=selection_result)

            selection_to_next_delay = 3.0
            self._trace(
                "upload.cover_picker.wait_before_next",
                seconds=selection_to_next_delay,
            )
            time.sleep(selection_to_next_delay)

            stage = "click_next"
            next_result = self._wait_and_click_next_or_detect_edit_cover(session)
            self._trace("upload.cover_picker.next", result=next_result)

            complete_clicked = False
            edit_cover_confirmed = False
            cover_applied_verified = False
            if not next_result.get("skipped"):
                time.sleep(0.35)
            stage = "detect_edit_cover_dialog"
            edit_cover_state = next_result.get("editCoverState")
            if not isinstance(edit_cover_state, Mapping):
                edit_cover_state = self._edit_cover_dialog_state(session)
            self._trace("upload.cover_picker.edit_cover_dialog_detected", result=edit_cover_state)
            if edit_cover_state.get("dialogOpen"):
                stage = "confirm_edit_cover"
                edit_cover_confirm_delay = 2.0
                self._trace(
                    "upload.cover_picker.wait_before_edit_cover_confirm",
                    seconds=edit_cover_confirm_delay,
                )
                time.sleep(edit_cover_confirm_delay)
                confirm_result = self._wait_and_confirm_edit_cover(session)
                complete_clicked = True
                edit_cover_confirmed = True
                self._trace(
                    "upload.cover_picker.edit_cover_confirm_dispatched",
                    result=confirm_result,
                )

                stage = "wait_edit_cover_dialog_closed"
                if not self._wait_for_edit_cover_dialog_closed(session):
                    raise WeChatPublishingError("编辑封面弹窗未关闭")
                self._trace("upload.cover_picker.edit_cover_dialog_closed")
                self._trace(
                    "upload.cover_picker.returned_to_editor_after_edit_cover_confirm",
                    result=self._article_editor_return_state(session),
                )

                stage = "wait_cover_applied"
                cover_applied_verified = self._wait_for_cover_picker_cover_applied(session)
                cover_applied = edit_cover_confirmed
                self._trace(
                    "upload.cover_picker.cover_applied_after_edit_cover_confirm",
                    cover_applied=cover_applied_verified,
                    treated_as_applied=cover_applied,
                )
            else:
                stage = "wait_cover_applied_after_next"
                cover_applied = self._wait_for_cover_picker_cover_applied(session, timeout=1.5)
                cover_applied_verified = cover_applied
                self._trace(
                    "upload.cover_picker.cover_applied_after_next",
                    cover_applied=cover_applied,
                )
            if not cover_applied:
                stage = "click_complete"
                complete_result = self._wait_and_click_cover_picker_button(
                    session,
                    ("完成", "确认", "确定"),
                    "codex-cover-picker:click-complete",
                )
                complete_clicked = True
                self._trace("upload.cover_picker.complete", result=complete_result)

                stage = "wait_cover_applied"
                cover_applied = self._wait_for_cover_picker_cover_applied(session)
                cover_applied_verified = cover_applied
            self._trace(
                "upload.cover_picker.cover_applied",
                cover_applied=cover_applied,
                cover_applied_verified=cover_applied_verified,
            )

            self._trace(
                "upload.cover_picker_from_article_image.completed",
                image_count=image_count,
                selected_index=selected_index,
                from_article_entry_clicked=bool(entry_result.get("clicked")),
                next_clicked=True,
                complete_clicked=complete_clicked,
                cover_applied=cover_applied,
                cover_applied_verified=cover_applied_verified,
            )
            return {
                "role": "cover",
                "source": "wechat_article_image_picker",
                "remote_url": str(cover_path),
            }
        except Exception as exc:
            self._trace(
                "upload.cover_picker_from_article_image.failed",
                stage=stage,
                error=str(exc),
                last_snapshot=dict(last_snapshot),
            )
            if isinstance(exc, WeChatPublishingError):
                raise WeChatPublishingError(f"微信封面选择失败（{stage}）：{exc}") from exc
            raise

    def _wait_for_cover_picker_from_article_entry(
        self,
        session: BrowserSession,
        timeout: float = 4,
    ) -> Mapping[str, Any]:
        last_result: Mapping[str, Any] = {}
        attempt = 0
        deadline = time.time() + timeout
        while time.time() < deadline:
            attempt += 1
            raw_entry_result = self.evaluate(
                session,
                self._cover_picker_from_article_entry_expression(),
            )
            entry_result = (
                dict(raw_entry_result)
                if isinstance(raw_entry_result, Mapping)
                else {"value": raw_entry_result}
            )
            if (
                entry_result.get("ok")
                and not entry_result.get("alreadyOpen")
                and not entry_result.get("clicked")
            ):
                click_rect = entry_result.get("clickableRect") or entry_result.get("rect")
                if click_rect:
                    self._dispatch_mouse_click(session, click_rect)
                    entry_result["clicked"] = True
                    entry_result["clickMethod"] = "cdp_mouse"
                    time.sleep(0.35)
            self._trace(
                "upload.cover_picker.from_article_entry_wait",
                attempt=attempt,
                result=entry_result,
            )
            if entry_result.get("ok"):
                return entry_result
            last_result = entry_result
            time.sleep(0.2)
        self._trace(
            "upload.cover_picker.from_article_entry_timeout",
            attempts=attempt,
            last_result=dict(last_result),
        )
        reason = str(last_result.get("reason") or "unknown")
        raise WeChatPublishingError(f"未打开从正文选择入口：{reason}")

    def _wait_for_cover_picker_snapshot(
        self,
        session: BrowserSession,
        target_image_url: str,
        timeout: float = 8,
    ) -> Mapping[str, Any]:
        last_snapshot: Mapping[str, Any] = {}
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = self.evaluate(
                session,
                self._cover_picker_snapshot_expression(target_image_url),
            )
            if isinstance(value, Mapping):
                last_snapshot = dict(value)
                image_count = int(value.get("imageCount") or 0)
                selected_value = value.get("selectedIndex")
                selected_index = int(selected_value if selected_value is not None else -1)
                if value.get("ok") and image_count > 0 and selected_index >= 0:
                    return value
            time.sleep(0.25)
        self._trace("upload.cover_picker.candidates_timeout", last_snapshot=dict(last_snapshot))
        reason = str(last_snapshot.get("reason") or "article_image_not_found")
        image_count = int(last_snapshot.get("imageCount") or 0)
        raise WeChatPublishingError(
            f"等待正文封面候选图超时；原因：{reason}；正文候选图片数：{image_count}"
        )

    def _wait_for_cover_picker_selection(
        self,
        session: BrowserSession,
        selected_index: int,
        target_image_url: str,
        timeout: float = 5,
    ) -> Mapping[str, Any]:
        last_result: Mapping[str, Any] = {}
        stable_checks = 0
        required_stable_checks = 2
        attempt = 0
        deadline = time.time() + timeout
        while time.time() < deadline:
            attempt += 1
            value = self.evaluate(
                session,
                self._cover_picker_selection_state_expression(
                    selected_index,
                    target_image_url,
                ),
            )
            if isinstance(value, Mapping):
                last_result = dict(value)
                if value.get("ok"):
                    stable_checks += 1
                    last_result["attempt"] = attempt
                    last_result["stableChecks"] = stable_checks
                    self._trace(
                        "upload.cover_picker.selection_wait",
                        attempt=attempt,
                        stable_checks=stable_checks,
                        result=dict(last_result),
                    )
                    if stable_checks >= required_stable_checks:
                        time.sleep(0.45)
                        return last_result
                else:
                    stable_checks = 0
                    self._trace(
                        "upload.cover_picker.selection_wait",
                        attempt=attempt,
                        stable_checks=stable_checks,
                        result=dict(last_result),
                    )
            time.sleep(0.3)
        self._trace("upload.cover_picker.selection_timeout", last_result=dict(last_result))
        reason = str(last_result.get("reason") or "candidate_not_selected")
        raise WeChatPublishingError(f"正文候选封面图未选中：{reason}")

    def _wait_and_click_cover_picker_button(
        self,
        session: BrowserSession,
        texts: Sequence[str],
        marker: str,
        timeout: float = 8,
    ) -> Mapping[str, Any]:
        last_result: Mapping[str, Any] = {}
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = self.evaluate(
                session,
                self._cover_picker_click_button_expression(texts, marker),
            )
            if isinstance(value, Mapping):
                last_result = dict(value)
                if value.get("ok"):
                    return value
            time.sleep(0.25)
        self._trace(
            "upload.cover_picker.button_timeout",
            texts=list(texts),
            marker=marker,
            last_result=dict(last_result),
        )
        reason = str(last_result.get("reason") or "not_found")
        raise WeChatPublishingError(f"微信封面弹窗按钮未点击成功：{'/'.join(texts)}；原因：{reason}")

    def _wait_and_click_next_or_detect_edit_cover(
        self,
        session: BrowserSession,
        timeout: float = 8,
    ) -> Mapping[str, Any]:
        last_result: Mapping[str, Any] = {}
        last_edit_cover_state: Mapping[str, Any] = {}
        marker = "codex-cover-picker:click-next"
        deadline = time.time() + timeout
        while time.time() < deadline:
            edit_cover_state = self._edit_cover_dialog_state(session)
            last_edit_cover_state = dict(edit_cover_state)
            if edit_cover_state.get("dialogOpen"):
                result = {
                    "ok": True,
                    "skipped": True,
                    "reason": "already_on_edit_cover",
                    "editCoverState": dict(edit_cover_state),
                }
                self._trace("upload.cover_picker.next_already_on_edit_cover", result=result)
                return result

            value = self.evaluate(
                session,
                self._cover_picker_click_button_expression(("下一步",), marker),
            )
            if isinstance(value, Mapping):
                last_result = dict(value)
                if value.get("ok"):
                    return value
            time.sleep(0.25)
        self._trace(
            "upload.cover_picker.button_timeout",
            texts=["下一步"],
            marker=marker,
            last_result=dict(last_result),
            edit_cover_state=dict(last_edit_cover_state),
        )
        reason = str(last_result.get("reason") or "not_found")
        raise WeChatPublishingError(f"微信封面弹窗按钮未点击成功：下一步；原因：{reason}")

    def _wait_for_cover_picker_cover_applied(
        self,
        session: BrowserSession,
        timeout: float = 5,
    ) -> bool:
        deadline = time.time() + timeout
        last_result: Mapping[str, Any] = {}
        while time.time() < deadline:
            value = self.evaluate(session, self._cover_picker_cover_applied_expression())
            if isinstance(value, Mapping):
                last_result = dict(value)
                if value.get("ok") and value.get("coverApplied"):
                    return True
            time.sleep(0.25)
        self._trace("upload.cover_picker.cover_applied_unverified", last_result=dict(last_result))
        return False

    def _edit_cover_dialog_state(self, session: BrowserSession) -> Mapping[str, Any]:
        value = self.evaluate(session, self._edit_cover_dialog_state_expression())
        return dict(value) if isinstance(value, Mapping) else {"value": value}

    def _wait_and_confirm_edit_cover(
        self,
        session: BrowserSession,
        timeout: float = 5,
    ) -> Mapping[str, Any]:
        last_result: Mapping[str, Any] = {}
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = self.evaluate(session, self._edit_cover_confirm_expression())
            if isinstance(value, Mapping):
                last_result = dict(value)
                if value.get("ok"):
                    return value
            time.sleep(0.25)
        self._trace("upload.cover_picker.edit_cover_confirm_timeout", last_result=dict(last_result))
        reason = str(last_result.get("reason") or "not_found")
        raise WeChatPublishingError(f"编辑封面确认按钮未点击成功：{reason}")

    def _wait_for_edit_cover_dialog_closed(
        self,
        session: BrowserSession,
        timeout: float = 5,
    ) -> bool:
        last_result: Mapping[str, Any] = {}
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = self._edit_cover_dialog_state(session)
            last_result = value
            if not value.get("dialogOpen"):
                return True
            time.sleep(0.25)
        self._trace("upload.cover_picker.edit_cover_dialog_close_timeout", last_result=dict(last_result))
        return False

    def _article_editor_return_state(self, session: BrowserSession) -> Mapping[str, Any]:
        try:
            value = self.evaluate(session, self._article_editor_return_state_expression())
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return dict(value) if isinstance(value, Mapping) else {"value": value}

    @staticmethod
    def _cover_picker_common_source() -> str:
        return r"""
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const textOf = (node) => String(node?.innerText || node?.textContent || '').replace(/\s+/g, ' ').trim();
          const normalizeImageUrl = (value) => {
            try {
              const parsed = new URL(value, location.href);
              return parsed.origin + parsed.pathname;
            } catch (_) {
              return String(value || '').split('?')[0];
            }
          };
          const dialogNodes = () => [...document.querySelectorAll('.weui-desktop-dialog__wrp, .weui-desktop-dialog, [role="dialog"]')]
            .filter((node) => visible(node));
          const imageDialog = () => {
            const dialogs = dialogNodes();
            return dialogs.find((node) => textOf(node).includes('选择图片') && (textOf(node).includes('正文插入') || textOf(node).includes('正文') || textOf(node).includes('下一步')))
              || dialogs.find((node) => textOf(node).includes('下一步') && findImageCandidates(node).length)
              || null;
          };
          const imageSelectors = [
            '.appmsg_conent_img_container img',
            '.appmsg_content_img_container img',
            '.appmsg_content_img_list img',
            '.appmsg_conent_img_container [style*="background-image"]',
            '.appmsg_content_img_container [style*="background-image"]',
            '[class*=appmsg][class*=img] img',
            '[class*=image_item] img',
            '[class*=img_item] img',
            '[class*=image_item] [style*="background-image"]',
            '[class*=img_item] [style*="background-image"]'
          ];
          const findImageCandidates = (root) => {
            const candidates = [...new Set(imageSelectors.flatMap((selector) => [...root.querySelectorAll(selector)]))]
              .filter((node) => visible(node));
            return candidates.filter((node) => {
              const rect = node.getBoundingClientRect();
              const isImage = node.matches('img');
              const hasBackground = String(getComputedStyle(node).backgroundImage || '').includes('url(');
              return (isImage || hasBackground) && rect.width >= 20 && rect.height >= 20;
            });
          };
          const clickableFor = (node) => node.closest('li.appmsg_conent_img_item, li.appmsg_content_img_item, li, label, [role="button"], .appmsg_conent_img_container, .appmsg_content_img_container, [class*=image_item], [class*=img_item]') || node;
          const findImageCandidateCards = (root) => {
            const cards = [...root.querySelectorAll('li.appmsg_conent_img_item, li.appmsg_content_img_item')]
              .filter((node) => visible(node))
              .filter((node) => {
                const rect = node.getBoundingClientRect();
                return rect.width >= 20 && rect.height >= 20 && Boolean(imageSrcOf(node));
              });
            if (cards.length) return cards;
            return [...new Set(findImageCandidates(root).map((node) => clickableFor(node)))]
              .filter((node) => visible(node));
          };
          const imageSrcOf = (candidate) => {
            const image = candidate.matches('img') ? candidate : candidate.querySelector('img');
            if (image?.src) return image.src;
            const styled = candidate.querySelector?.('[style*="background-image"]') || candidate;
            const match = String(getComputedStyle(styled).backgroundImage || '').match(/url\(["']?([^"')]+)["']?\)/);
            return match ? match[1] : '';
          };
          const selectedIndexFor = (candidates, targetImageUrl) => {
            if (!targetImageUrl) return candidates.length ? 0 : -1;
            const targetNormalized = normalizeImageUrl(targetImageUrl);
            return candidates.findIndex((candidate) => {
              const src = imageSrcOf(candidate);
              return normalizeImageUrl(src) === targetNormalized || String(src || '').includes(targetImageUrl);
            });
          };
          const isSelectedCandidate = (candidate) => candidate.classList.contains('selected')
            || candidate.getAttribute('aria-selected') === 'true'
            || Boolean(candidate.querySelector('input:checked, [aria-selected="true"], .selected'));
        """

    @staticmethod
    def _cover_picker_from_article_entry_expression() -> str:
        return f"""
        (() => {{
          /* codex-cover-picker:from-article-entry */
          {ChromeCDPBrowserGateway._cover_picker_common_source()}
          const dialog = imageDialog();
          if (dialog && findImageCandidates(dialog).length) {{
            return {{ok: true, clicked: false, alreadyOpen: true}};
          }}
          const directWechatSelector = '.pop-opr__group-select-cover .js_selectCoverFromContent';
          const directWechatEntries = [...document.querySelectorAll(directWechatSelector)]
            .filter((node) => visible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
            .map((node) => {{
              const clickable = node.closest('button, a, [role="button"], [role="menuitem"], li, label') || node;
              const rect = node.getBoundingClientRect();
              const clickableRect = clickable.getBoundingClientRect();
              return {{node, clickable, text: textOf(node), rect, clickableRect}};
            }})
            .filter((item) => item.text.includes('从正文选择') && item.rect.width >= 20 && item.rect.height >= 10)
            .sort((left, right) => left.clickableRect.top - right.clickableRect.top || left.clickableRect.left - right.clickableRect.left);
          const directWechatSummaries = directWechatEntries.slice(0, 8).map((item, index) => ({{
            index,
            text: item.text.slice(0, 80),
            rect: {{
              x: Math.round(item.rect.x),
              y: Math.round(item.rect.y),
              width: Math.round(item.rect.width),
              height: Math.round(item.rect.height),
            }},
            clickableRect: {{
              x: Math.round(item.clickableRect.x),
              y: Math.round(item.clickableRect.y),
              width: Math.round(item.clickableRect.width),
              height: Math.round(item.clickableRect.height),
            }},
          }}));
          const directWechatEntry = directWechatEntries[0];
          if (directWechatEntry) {{
            directWechatEntry.clickable.scrollIntoView({{block: 'center'}});
            const clickableRect = directWechatEntry.clickable.getBoundingClientRect();
            return {{
              ok: true,
              clicked: false,
              source: 'wechat-cover-menu',
              selector: directWechatSelector,
              text: directWechatEntry.text.slice(0, 80),
              directWechatEntries: directWechatSummaries,
              rect: {{
                x: Math.round(directWechatEntry.rect.x),
                y: Math.round(directWechatEntry.rect.y),
                width: Math.round(directWechatEntry.rect.width),
                height: Math.round(directWechatEntry.rect.height),
              }},
              clickableRect: {{
                x: Math.round(clickableRect.x),
                y: Math.round(clickableRect.y),
                width: Math.round(clickableRect.width),
                height: Math.round(clickableRect.height),
              }},
            }};
          }}
          const selector = 'button, a, [role="button"], [role="menuitem"], li, label, span';
          const menuRoots = [...document.querySelectorAll(
            '.pop-opr__group-select-cover, .weui-desktop-popover, .weui-desktop-popover__wrp, .weui-desktop-dropdown, .weui-desktop-menu, .weui-desktop-menu__wrp, [role="menu"], .popover, .dropdown, .menu, [class*="popover"], [class*="dropdown"], [class*="menu"]'
          )].filter(visible);
          const scopedNodes = [...new Set(
            (menuRoots.length ? menuRoots : [document]).flatMap((root) => [...root.querySelectorAll(selector)])
          )];
          const menuSummaries = menuRoots.slice(0, 8).map((root, index) => {{
            const rect = root.getBoundingClientRect();
            return {{
              index,
              tagName: String(root.tagName || '').toLowerCase(),
              className: String(root.className || '').slice(0, 240),
              text: textOf(root).slice(0, 240),
              rect: {{
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
              }},
            }};
          }});
          const visibleMenuTexts = [...new Set(
            scopedNodes
              .filter((node) => visible(node))
              .map((node) => textOf(node))
              .filter((text) => text && text.length <= 80)
          )].slice(0, 24);
          const exactTexts = ['从正文选择', '正文选择', '从正文插入'];
          const entryCandidates = scopedNodes
            .filter((node) => visible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
            .map((node) => {{
              const text = textOf(node);
              const rect = node.getBoundingClientRect();
              const clickable = node.closest('button, a, [role="button"], [role="menuitem"], li, label') || node;
              const clickableRect = clickable.getBoundingClientRect();
              const inMenu = menuRoots.some((root) => root === node || root.contains(node));
              return {{node, clickable, text, rect, clickableRect, inMenu}};
            }})
            .filter((item) => {{
              const {{text, rect, clickableRect}} = item;
              const textMatches = text === '从正文选择' || exactTexts.includes(text) || (text.length <= 12 && text.includes('从正文选择'));
              return textMatches
                && text.length <= 24
                && rect.width >= 20
                && rect.height >= 10
                && rect.width <= 360
                && rect.height <= 96
                && clickableRect.width >= 20
                && clickableRect.height >= 10
                && clickableRect.width <= 420
                && clickableRect.height <= 120;
            }});
          entryCandidates.sort((left, right) => {{
            const leftExact = left.text === '从正文选择' ? 0 : 1;
            const rightExact = right.text === '从正文选择' ? 0 : 1;
            const leftMenu = left.inMenu ? 0 : 1;
            const rightMenu = right.inMenu ? 0 : 1;
            return leftExact - rightExact
              || leftMenu - rightMenu
              || left.clickableRect.top - right.clickableRect.top
              || left.clickableRect.left - right.clickableRect.left;
          }});
          const summaries = entryCandidates.slice(0, 8).map((item, index) => ({{
            index,
            text: item.text.slice(0, 40),
            inMenu: item.inMenu,
            rect: {{
              x: Math.round(item.rect.x),
              y: Math.round(item.rect.y),
              width: Math.round(item.rect.width),
              height: Math.round(item.rect.height),
            }},
            clickableRect: {{
              x: Math.round(item.clickableRect.x),
              y: Math.round(item.clickableRect.y),
              width: Math.round(item.clickableRect.width),
              height: Math.round(item.clickableRect.height),
            }},
          }}));
          const entry = entryCandidates[0];
          if (!entry) return {{
            ok: false,
            reason: 'from_article_entry_not_found',
            menuRootCount: menuRoots.length,
            menuRoots: menuSummaries,
            visibleMenuTexts,
            directWechatEntries: directWechatSummaries,
            entryCandidates: summaries,
          }};
          entry.clickable.scrollIntoView({{block: 'center'}});
          const rect = entry.rect;
          const clickableRect = entry.clickable.getBoundingClientRect();
          return {{
            ok: true,
            clicked: false,
            source: 'precise-menu-item',
            text: entry.text.slice(0, 80),
            menuRootCount: menuRoots.length,
            menuRoots: menuSummaries,
            visibleMenuTexts,
            entryCandidates: summaries,
            rect: {{
              x: Math.round(rect.x),
              y: Math.round(rect.y),
              width: Math.round(rect.width),
              height: Math.round(rect.height),
            }},
            clickableRect: {{
              x: Math.round(clickableRect.x),
              y: Math.round(clickableRect.y),
              width: Math.round(clickableRect.width),
              height: Math.round(clickableRect.height),
            }},
          }};
        }})()
        """

    @staticmethod
    def _cover_picker_snapshot_expression(target_image_url: str = "") -> str:
        return f"""
        (() => {{
          /* codex-cover-picker:snapshot */
          const targetImageUrl = {json.dumps(target_image_url)};
          {ChromeCDPBrowserGateway._cover_picker_common_source()}
          const dialog = imageDialog();
          const root = dialog || document;
          const candidates = dialog ? findImageCandidateCards(dialog) : [];
          const selectedIndex = selectedIndexFor(candidates, targetImageUrl);
          const selectedIndices = candidates
            .map((candidate, index) => isSelectedCandidate(candidate) ? index : -1)
            .filter((index) => index >= 0);
          const summaries = candidates.slice(0, 12).map((candidate, index) => {{
            const rect = candidate.getBoundingClientRect();
            const src = imageSrcOf(candidate);
            return {{
              index,
              src,
              text: textOf(candidate).slice(0, 80),
              rect: {{
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
              }},
            }};
          }});
          const hasNext = [...root.querySelectorAll('button, a, [role="button"]')]
            .some((node) => visible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true' && textOf(node).includes('下一步'));
          return {{
            ok: Boolean(dialog && candidates.length && selectedIndex >= 0),
            reason: !dialog ? 'image_dialog_not_found' : (!candidates.length ? 'article_image_not_found' : (selectedIndex < 0 ? 'target_image_not_found' : '')),
            dialogFound: Boolean(dialog),
            imageCount: candidates.length,
            selectedIndex,
            selectedIndices,
            candidates: summaries,
            nextButtonFound: hasNext,
            dialogText: textOf(root).slice(0, 320),
          }};
        }})()
        """

    @staticmethod
    def _article_editor_return_state_expression() -> str:
        return r"""
        (() => {
          /* codex-cover-picker:article-editor-return-state */
          const visible = (node) => !!node
            && getComputedStyle(node).visibility !== 'hidden'
            && getComputedStyle(node).display !== 'none'
            && node.getClientRects().length > 0;
          const textOf = (node) => String(node?.innerText || node?.textContent || '')
            .replace(/\s+/g, ' ')
            .trim();
          const buttons = [...document.querySelectorAll('button, a, [role="button"]')]
            .filter(visible)
            .map((node) => ({
              text: textOf(node).slice(0, 40),
              tag: String(node.tagName || '').toLowerCase(),
              className: String(node.className || '').slice(0, 120),
              disabled: Boolean(node.disabled) || node.getAttribute('aria-disabled') === 'true',
            }))
            .filter((item) => item.text)
            .slice(0, 40);
          const editor = document.querySelector(
            ".ProseMirror[contenteditable='true'], #ueditor_0, [contenteditable='true'].js_editor, [contenteditable='true']"
          );
          const dialogs = [...document.querySelectorAll(
            '.weui-desktop-dialog__wrp[isshow="true"], .weui-desktop-dialog__wrp, [role="dialog"]'
          )]
            .filter(visible)
            .map((node) => textOf(node).slice(0, 160));
          const coverNodes = [...document.querySelectorAll(
            '.js_cover_area, .select-cover__preview, .select_cover_preview, .js_cover_preview_square, .js_cover_preview, [class*="cover"]'
          )]
            .filter(visible)
            .slice(0, 12);
          const coverNodeTexts = coverNodes.map((node) => textOf(node).slice(0, 80));
          return {
            ok: true,
            url: location.href,
            title: document.title,
            editorReady: Boolean(editor) && visible(editor),
            editCoverDialogOpen: dialogs.some((text) => text.includes('编辑封面')),
            visibleDialogTexts: dialogs,
            saveDraftButtonVisible: buttons.some((item) => item.text.includes('保存为草稿') || item.text.includes('保存草稿')),
            publishButtonVisible: buttons.some((item) => item.text === '发表' || item.text.includes('发表')),
            visibleButtonTexts: buttons,
            coverAreaVisible: coverNodes.length > 0,
            coverNodeTexts,
            scrollY: Math.round(window.scrollY || 0),
          };
        })()
        """

    @staticmethod
    def _edit_cover_dialog_state_expression() -> str:
        return r"""
        (() => {
          /* codex-cover-picker:edit-cover-dialog-state */
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const textOf = (node) => String(node?.innerText || node?.textContent || '').replace(/\s+/g, ' ').trim();
          const dialogs = [...document.querySelectorAll('.weui-desktop-dialog__wrp[isshow="true"], .weui-desktop-dialog__wrp')]
            .filter((node) => visible(node));
          const dialog = dialogs.find((node) => {
            const buttons = [...node.querySelectorAll('button.weui-desktop-btn.weui-desktop-btn_primary')];
            return textOf(node).includes('编辑封面') && buttons.some((button) => textOf(button) === '确认');
          });
          return {
            ok: true,
            dialogOpen: Boolean(dialog),
            dialogText: textOf(dialog || '').slice(0, 240),
            dialogCount: dialogs.length,
          };
        })()
        """

    @staticmethod
    def _edit_cover_confirm_expression() -> str:
        return r"""
        (() => {
          /* codex-cover-picker:click-edit-cover-confirm */
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const textOf = (node) => String(node?.innerText || node?.textContent || '').replace(/\s+/g, ' ').trim();
          const dialogs = [...document.querySelectorAll('.weui-desktop-dialog__wrp[isshow="true"], .weui-desktop-dialog__wrp')]
            .filter((node) => visible(node));
          const dialog = dialogs.find((node) => textOf(node).includes('编辑封面'));
          if (!dialog) return {ok: false, reason: 'edit_cover_dialog_not_found'};
          const button = [...dialog.querySelectorAll('button.weui-desktop-btn.weui-desktop-btn_primary')]
            .find((node) => {
              const buttonText = textOf(node);
              return buttonText === '确认'
                && !node.disabled
                && node.getAttribute('aria-disabled') !== 'true'
                && !String(node.className || '').includes('disabled');
            });
          if (!button) {
            return {
              ok: false,
              reason: 'confirm_button_not_ready',
              buttons: [...dialog.querySelectorAll('button')].filter(visible).map((node) => ({
                text: textOf(node),
                className: String(node.className || '').slice(0, 160),
                disabled: Boolean(node.disabled) || node.getAttribute('aria-disabled') === 'true',
              })),
            };
          }
          button.scrollIntoView({block: 'center'});
          button.click();
          return {
            ok: true,
            buttonText: textOf(button),
            buttonClass: String(button.className || '').slice(0, 160),
          };
        })()
        """

    @staticmethod
    def _cover_picker_select_image_expression(
        selected_index: int,
        target_image_url: str = "",
    ) -> str:
        return f"""
        (() => {{
          /* codex-cover-picker:select-image */
          const selectedIndex = {int(selected_index)};
          const targetImageUrl = {json.dumps(target_image_url)};
          {ChromeCDPBrowserGateway._cover_picker_common_source()}
          const dialog = imageDialog();
          if (!dialog) return {{ok: false, reason: 'image_dialog_not_found'}};
          const candidates = findImageCandidateCards(dialog);
          const resolvedIndex = targetImageUrl ? selectedIndexFor(candidates, targetImageUrl) : selectedIndex;
          const node = resolvedIndex >= 0 ? candidates[resolvedIndex] : null;
          if (!node) return {{ok: false, reason: 'candidate_not_found', imageCount: candidates.length, selectedIndex: resolvedIndex}};
          node.scrollIntoView({{block: 'center'}});
          const rect = node.getBoundingClientRect();
          return {{
            ok: true,
            imageCount: candidates.length,
            selectedIndex: resolvedIndex,
            src: imageSrcOf(node),
            text: textOf(node).slice(0, 80),
            selectedBefore: isSelectedCandidate(node),
            candidateClass: String(node.className || ''),
            candidateTag: String(node.tagName || '').toLowerCase(),
            clickableRect: {{
              x: Math.round(rect.x),
              y: Math.round(rect.y),
              width: Math.round(rect.width),
              height: Math.round(rect.height),
            }},
          }};
        }})()
        """

    @staticmethod
    def _cover_picker_selection_state_expression(
        selected_index: int,
        target_image_url: str = "",
    ) -> str:
        return f"""
        (() => {{
          /* codex-cover-picker:selection-state */
          const selectedIndex = {int(selected_index)};
          const targetImageUrl = {json.dumps(target_image_url)};
          {ChromeCDPBrowserGateway._cover_picker_common_source()}
          const dialog = imageDialog();
          if (!dialog) return {{ok: false, reason: 'image_dialog_not_found'}};
          const candidates = findImageCandidateCards(dialog);
          const resolvedIndex = targetImageUrl ? selectedIndexFor(candidates, targetImageUrl) : selectedIndex;
          const node = resolvedIndex >= 0 ? candidates[resolvedIndex] : null;
          const selectedIndices = candidates
            .map((candidate, index) => isSelectedCandidate(candidate) ? index : -1)
            .filter((index) => index >= 0);
          const nextButtons = [...dialog.querySelectorAll('button, a, [role="button"]')]
            .filter((button) => visible(button) && textOf(button).includes('下一步'))
            .map((button) => {{
              const className = String(button.className || '');
              return {{
                disabled: Boolean(button.disabled) || button.getAttribute('aria-disabled') === 'true' || className.includes('disabled'),
                className: className.slice(0, 160),
              }};
            }});
          const nextEnabled = nextButtons.some((button) => !button.disabled);
          const candidateSelected = Boolean(node) && isSelectedCandidate(node);
          return {{
            ok: Boolean(node && candidateSelected && nextEnabled),
            reason: !node ? 'candidate_not_found' : (!candidateSelected ? 'candidate_not_selected' : (!nextEnabled ? 'next_still_disabled' : '')),
            imageCount: candidates.length,
            selectedIndex: resolvedIndex,
            selectedIndices,
            candidateSelected,
            candidateClass: node ? String(node.className || '') : '',
            nextButtons,
          }};
        }})()
        """

    @staticmethod
    def _cover_picker_click_button_expression(
        texts: Sequence[str],
        marker: str,
    ) -> str:
        return f"""
        (() => {{
          /* {marker} */
          const texts = {json.dumps(list(texts), ensure_ascii=False)};
          {ChromeCDPBrowserGateway._cover_picker_common_source()}
          const roots = [...dialogNodes(), document];
          for (const root of roots) {{
            const availableButtons = [...root.querySelectorAll('button, a, [role="button"]')]
              .filter((node) => visible(node))
              .map((node) => {{
                const buttonText = textOf(node);
                const buttonClass = String(node.className || '');
                const disabled = Boolean(node.disabled) || buttonClass.includes('disabled');
                const ariaDisabled = node.getAttribute('aria-disabled') === 'true';
                const rect = node.getBoundingClientRect();
                return {{
                  text: buttonText.slice(0, 80),
                  disabled,
                  ariaDisabled,
                  buttonClass: buttonClass.slice(0, 160),
                  rect: {{
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                  }},
                  node,
                }};
              }});
            const textMatches = availableButtons.filter((item) => texts.some((text) => item.text.includes(text)));
            const candidates = textMatches
              .filter((item) => !item.disabled && !item.ariaDisabled)
              .map((item) => item.node)
              .filter((node) => texts.some((text) => textOf(node).includes(text)));
            candidates.sort((left, right) => {{
              const leftRect = left.getBoundingClientRect();
              const rightRect = right.getBoundingClientRect();
              return rightRect.bottom - leftRect.bottom || rightRect.right - leftRect.right;
            }});
            if (candidates.length) {{
              const node = candidates[0];
              node.scrollIntoView({{block: 'center'}});
              node.click();
              return {{ok: true, text: textOf(node).slice(0, 80), count: candidates.length}};
            }}
            if (textMatches.length) {{
              return {{
                ok: false,
                reason: 'button_disabled',
                availableButtons: textMatches.map((item) => {{
                  const {{node, ...summary}} = item;
                  return summary;
                }}),
                dialogText: textOf(root).slice(0, 320),
              }};
            }}
          }}
          const activeDialog = dialogNodes()[0] || document;
          const availableButtons = [...activeDialog.querySelectorAll('button, a, [role="button"]')]
            .filter((node) => visible(node))
            .map((node) => {{
              const rect = node.getBoundingClientRect();
              return {{
                text: textOf(node).slice(0, 80),
                disabled: Boolean(node.disabled) || String(node.className || '').includes('disabled'),
                ariaDisabled: node.getAttribute('aria-disabled') === 'true',
                buttonClass: String(node.className || '').slice(0, 160),
                rect: {{
                  x: Math.round(rect.x),
                  y: Math.round(rect.y),
                  width: Math.round(rect.width),
                  height: Math.round(rect.height),
                }},
              }};
            }});
          return {{ok: false, reason: 'button_not_found', availableButtons, dialogText: textOf(activeDialog).slice(0, 320)}};
        }})()
        """

    @staticmethod
    def _cover_picker_cover_applied_expression() -> str:
        return f"""
        (() => {{
          /* codex-cover-picker:cover-applied */
          {ChromeCDPBrowserGateway._cover_picker_common_source()}
          const editCoverDialog = [...document.querySelectorAll('.weui-desktop-dialog__wrp[isshow="true"], .weui-desktop-dialog__wrp')]
            .filter((node) => visible(node) && textOf(node).includes('编辑封面'))[0];
          if (editCoverDialog) {{
            return {{
              ok: true,
              coverApplied: false,
              editCoverDialogOpen: true,
              dialogText: textOf(editCoverDialog).slice(0, 240),
            }};
          }}
          const coverNodes = [...document.querySelectorAll('#js_cover_area, #js_cover_description_area, .js_cover_area, .js_cover_preview_square, .select-cover__preview')].filter(visible);
          const applied = coverNodes.find((cover) => {{
            const text = textOf(cover);
            const style = getComputedStyle(cover);
            const preview = cover.querySelector('img, [style*="background-image"]');
            return !text.includes('拖拽或选择封面')
              && !text.includes('选择封面')
              && (preview || String(style.backgroundImage || '').includes('url('));
          }});
          return {{
            ok: true,
            coverApplied: Boolean(applied),
            coverAreaCount: coverNodes.length,
            coverText: textOf(applied || coverNodes[0] || document).slice(0, 160),
          }};
        }})()
        """

    def _set_value(
        self,
        session: BrowserSession,
        selectors: Sequence[str],
        value: str,
        label: str,
        required: bool = True,
    ) -> None:
        expression = f"""
        (() => {{
          const selectors = {json.dumps(list(selectors))};
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const candidates = [...new Set(selectors.flatMap((selector) => [...document.querySelectorAll(selector)]))];
          const matches = candidates.filter((node) => visible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true' && (node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement || node.isContentEditable));
          if (matches.length > 1) return {{ok: false, reason: 'ambiguous', count: matches.length}};
          if (matches.length === 1) {{
            const node = matches[0];
            if (node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement) {{
              const prototype = node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
              const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
              setter ? setter.call(node, {json.dumps(value, ensure_ascii=False)}) : (node.value = {json.dumps(value, ensure_ascii=False)});
            }} else {{
              node.textContent = {json.dumps(value, ensure_ascii=False)};
            }}
            node.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText', data: {json.dumps(value, ensure_ascii=False)}}}));
            node.dispatchEvent(new Event('change', {{bubbles: true}}));
            return {{ok: true}};
          }}
          return {{ok: false, reason: candidates.length ? 'hidden_or_disabled' : 'not_found'}};
        }})()
        """
        result = self.evaluate(session, expression)
        self._require_dom_result(result, label, selectors, required)

    def _set_editor_html(self, session: BrowserSession, content_html: str) -> None:
        expression = f"""
        (() => {{
          const selectors = {json.dumps(list(EDITOR_SELECTORS))};
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const candidateRecords = [];
          selectors.forEach((selector, priority) => {{
            [...document.querySelectorAll(selector)].forEach((node) => {{
              candidateRecords.push({{node, priority, selector}});
            }});
          }});
          const bestByNode = new Map();
          candidateRecords.forEach((record) => {{
            const existing = bestByNode.get(record.node);
            if (!existing || record.priority < existing.priority) bestByNode.set(record.node, record);
          }});
          const matches = [...bestByNode.values()].filter((record) => {{
            const node = record.node;
            return visible(node) && node.isContentEditable && node.getAttribute('aria-disabled') !== 'true';
          }}).map((record) => {{
            const rect = record.node.getBoundingClientRect();
            return {{...record, area: Math.max(0, rect.width) * Math.max(0, rect.height)}};
          }});
          matches.sort((left, right) => left.priority - right.priority || right.area - left.area);
          if (matches.length >= 1) {{
            const selected = matches[0];
            const node = selected.node;
            node.focus();
            const html = {json.dumps(content_html, ensure_ascii=False)};
            const selection = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(node);
            selection.removeAllRanges();
            selection.addRange(range);
            const inserted = document.execCommand && document.execCommand('insertHTML', false, html);
            if (!inserted) node.innerHTML = html;
            node.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertHTML'}}));
            node.dispatchEvent(new Event('change', {{bubbles: true}}));
            return {{ok: true, count: matches.length, selected: selected.selector, area: selected.area, mode: inserted ? 'insertHTML' : 'innerHTML'}};
          }}
          return {{ok: false, reason: candidateRecords.length ? 'hidden_or_disabled' : 'not_found'}};
        }})()
        """
        result = self.evaluate(session, expression)
        self._require_dom_result(result, "正文", EDITOR_SELECTORS, True)

    def _click_first(
        self,
        session: BrowserSession,
        selectors: Sequence[str],
        label: str,
        required: bool = True,
    ) -> bool:
        expression = f"""
        (() => {{
          const selectors = {json.dumps(list(selectors))};
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const candidates = [...new Set(selectors.flatMap((selector) => [...document.querySelectorAll(selector)]))];
          const matches = candidates.filter((node) => visible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true');
          if (matches.length > 1) return {{ok: false, reason: 'ambiguous', count: matches.length}};
          if (matches.length === 1) {{
            const node = matches[0];
            node.scrollIntoView({{block: 'center'}});
            node.click();
            return {{ok: true, text: (node.innerText || node.textContent || '').trim()}};
          }}
          return {{ok: false, reason: candidates.length ? 'hidden_or_disabled' : 'not_found'}};
        }})()
        """
        result = self.evaluate(session, expression)
        found = isinstance(result, dict) and bool(result.get("ok"))
        self._require_dom_result(result, label, selectors, required)
        return found

    def _click_by_text(
        self,
        session: BrowserSession,
        texts: Sequence[str],
        label: str,
        required: bool = True,
    ) -> bool:
        result = self.evaluate(session, self._click_text_expression(texts))
        found = isinstance(result, dict) and bool(result.get("ok"))
        self._require_dom_result(result, label, texts, required)
        return found

    def _trigger_upload_entry(self, session: BrowserSession, role: str) -> bool:
        result = self.evaluate(session, self._upload_trigger_expression(role))
        self._trace(
            "upload.trigger_result",
            role=role,
            result=dict(result) if isinstance(result, Mapping) else {"value": result},
        )
        found = isinstance(result, dict) and bool(result.get("ok"))
        if found:
            click_method = "dom"
            if role == "cover":
                self._dispatch_mouse_click(session, result.get("rect"))
                click_method = "cdp_mouse"
            self._trace(
                "upload.trigger_dispatched",
                role=role,
                click_method=click_method,
                selected_text=str(result.get("selectedText") or ""),
                selected_class_name=str(result.get("selectedClassName") or ""),
                rect=result.get("rect"),
            )
            time.sleep(0.35)
        return found

    def _dispatch_mouse_click(self, session: BrowserSession, rect: Any) -> None:
        if not isinstance(rect, Mapping):
            return
        width = float(rect.get("width") or 0)
        height = float(rect.get("height") or 0)
        if width <= 0 or height <= 0:
            return
        x = round(float(rect.get("x") or 0) + width / 2)
        y = round(float(rect.get("y") or 0) + height / 2)
        for params in (
            {"type": "mouseMoved", "x": x, "y": y, "button": "none"},
            {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
            {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
        ):
            self._cdp_call(session, "Input.dispatchMouseEvent", params)

    @staticmethod
    def _click_text_expression(texts: Sequence[str]) -> str:
        return f"""
        (() => {{
          const texts = {json.dumps(list(texts), ensure_ascii=False)};
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const candidates = [...document.querySelectorAll('button, a, [role=button]')];
          const matches = candidates.filter((node) => visible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true' && texts.some((text) => (node.innerText || node.textContent || '').trim().includes(text)));
          matches.sort((left, right) => {{
            const leftRect = left.getBoundingClientRect();
            const rightRect = right.getBoundingClientRect();
            return rightRect.bottom - leftRect.bottom || rightRect.right - leftRect.right;
          }});
          if (matches.length >= 1) {{
            const node = matches[0];
            node.scrollIntoView({{block: 'center'}});
            node.click();
            return {{ok: true, text: (node.innerText || node.textContent || '').trim(), count: matches.length}};
          }}
          return {{ok: false, reason: 'not_found'}};
        }})()
        """

    @staticmethod
    def _upload_trigger_expression(role: str) -> str:
        texts = COVER_UPLOAD_TEXTS if role == "cover" else INLINE_UPLOAD_TEXTS
        trigger_with_dom_click = role != "cover"
        selectors = (
            (
                ".js_cover_area",
                ".js_cover_upload",
                ".cover-upload",
                "[class*=cover]",
                "[class*=upload]",
                "label",
                "button",
                "a",
                "[role=button]",
            )
            if role == "cover"
            else (
                ".edui-for-insertimage",
                ".js_editor_insertimage",
                "[class*=upload]",
                "[aria-label*='图片']",
                "[title*='图片']",
                "label",
                "button",
                "a",
                "[role=button]",
            )
        )
        return f"""
        (() => {{
          const texts = {json.dumps(list(texts), ensure_ascii=False)};
          const selectors = {json.dumps(list(selectors), ensure_ascii=False)};
          const triggerWithDomClick = {str(trigger_with_dom_click).lower()};
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const entries = [];
          const seen = new Set();
          selectors.forEach((selector) => {{
            document.querySelectorAll(selector).forEach((node) => {{
              if (seen.has(node)) return;
              seen.add(node);
              entries.push({{node, selector}});
            }});
          }});
          const matches = entries.filter((entry) => {{
            const node = entry.node;
            if (!visible(node) || node.disabled || node.getAttribute('aria-disabled') === 'true') return false;
            const text = (node.innerText || node.textContent || node.getAttribute('aria-label') || node.getAttribute('title') || '').trim();
            return texts.some((value) => text.includes(value));
          }}).map((entry) => {{
            const node = entry.node;
            const target = node.closest('label, button, a, [role=button]') || node;
            const text = (node.innerText || node.textContent || node.getAttribute('aria-label') || node.getAttribute('title') || '').trim();
            const rect = target.getBoundingClientRect();
            const exactRank = texts.findIndex((value) => text === value);
            const containsRank = texts.findIndex((value) => text.includes(value));
            return {{
              node: target,
              selector: entry.selector,
              text,
              className: String(target.className || ''),
              tagName: String(target.tagName || '').toLowerCase(),
              area: Math.max(0, rect.width) * Math.max(0, rect.height),
              top: rect.top,
              left: rect.left,
              textLength: text.length,
              matchRank: exactRank >= 0 ? exactRank * 2 : containsRank * 2 + 1,
            }};
          }});
          matches.sort((left, right) => left.matchRank - right.matchRank || left.textLength - right.textLength || left.area - right.area || left.top - right.top || left.left - right.left);
          if (matches.length >= 1) {{
            const selected = matches[0];
            const node = selected.node;
            node.scrollIntoView({{block: 'center'}});
            const rect = node.getBoundingClientRect();
            if (triggerWithDomClick) node.click();
            return {{
              ok: true,
              domClickPerformed: triggerWithDomClick,
              count: matches.length,
              selectedSelector: selected.selector,
              selectedText: selected.text.slice(0, 160),
              selectedClassName: selected.className.slice(0, 240),
              selectedTagName: selected.tagName,
              triggerCandidates: matches.slice(0, 8).map((item) => ({{
                selector: item.selector,
                text: item.text.slice(0, 80),
                className: item.className.slice(0, 120),
                tagName: item.tagName,
                matchRank: item.matchRank,
              }})),
              rect: {{
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
              }},
            }};
          }}
          return {{ok: false, reason: 'not_found'}};
        }})()
        """

    @staticmethod
    def _require_dom_result(
        result: Any,
        label: str,
        selectors: Sequence[str],
        required: bool,
    ) -> None:
        if isinstance(result, Mapping) and result.get("ok"):
            return
        reason = str(result.get("reason") or "") if isinstance(result, Mapping) else ""
        if reason == "ambiguous":
            raise WeChatPublishingError(
                f"微信页面{label}控件不唯一，拒绝操作；选择器：{result.get('selector') or ', '.join(selectors)}"
            )
        if required:
            detail = "不可见或不可用" if reason == "hidden_or_disabled" else "未找到"
            raise WeChatPublishingError(
                f"微信页面{label}控件{detail}，已尝试选择器：{', '.join(selectors)}"
            )

    def _wait_for_target(self, port: int, process: subprocess.Popen[Any]) -> dict[str, Any]:
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            if process.poll() is not None:
                raise WeChatPublishingError(
                    f"Chrome 启动失败，无法打开微信公众号后台{self._chrome_process_error(process)}"
                )
            try:
                targets = self._targets(port)
                page = next((item for item in targets if item.get("type") == "page"), None)
                if page and page.get("webSocketDebuggerUrl"):
                    return page
            except Exception:
                pass
            time.sleep(0.25)
        raise WeChatPublishingError("Chrome 启动超时，未取得 CDP 页面会话")

    @staticmethod
    def _chrome_process_error(process: subprocess.Popen[Any]) -> str:
        stderr = getattr(process, "stderr", None)
        if stderr is None:
            return ""
        try:
            detail = str(stderr.read() or "").strip()
        except Exception:
            return ""
        if not detail:
            return ""
        return f"：{detail[-500:]}"

    def _wait_for_document(self, session: BrowserSession) -> None:
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            try:
                ready = self.evaluate(session, "document.readyState")
                if ready in {"interactive", "complete"}:
                    session.url = self._current_url(session)
                    return
            except Exception:
                self._refresh_target(session)
            time.sleep(0.25)
        raise WeChatPublishingError("微信页面加载超时")

    def _current_url(self, session: BrowserSession) -> str:
        value = self.evaluate(session, "location.href")
        session.url = str(value or session.url)
        return session.url

    def _require_session_host(self, session: BrowserSession) -> None:
        current_url = self._current_url(session)
        parsed = urllib.parse.urlparse(current_url)
        if parsed.scheme != "https" or parsed.hostname != "mp.weixin.qq.com":
            raise WeChatPublishingError("扫码确认会话页面不是 mp.weixin.qq.com")

    def _refresh_target(self, session: BrowserSession) -> None:
        target = self._target(session.port, session.target_id)
        if not target:
            targets = self._targets(session.port)
            target = next((item for item in targets if item.get("type") == "page" and "mp.weixin.qq.com" in str(item.get("url") or "")), None)
        if not target:
            raise WeChatPublishingError("微信浏览器页面已关闭")
        session.target_id = str(target["id"])
        session.websocket_url = str(target["webSocketDebuggerUrl"])
        session.url = str(target.get("url") or session.url)

    def _targets(self, port: int) -> list[dict[str, Any]]:
        request = urllib.request.Request(f"http://127.0.0.1:{port}/json/list")
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, list) else []

    def _target(self, port: int, target_id: str) -> dict[str, Any] | None:
        return next((item for item in self._targets(port) if str(item.get("id")) == target_id), None)

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _stop_process(process: subprocess.Popen[Any], graceful: bool) -> None:
        if process.poll() is not None:
            return
        if graceful:
            try:
                process.wait(timeout=3)
                return
            except subprocess.TimeoutExpired:
                pass
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    def _cdp_call(self, session: BrowserSession, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        try:
            return self._cdp_call_once(session, method, params)
        except WeChatPublishingError as exc:
            if not self._is_stale_target_error(exc):
                raise
            old_target_id = session.target_id
            self._trace(
                "cdp.stale_target_retry",
                method=method,
                target_id=old_target_id,
                error=str(exc),
            )
            self._refresh_target(session)
            self._trace(
                "cdp.stale_target_refreshed",
                method=method,
                old_target_id=old_target_id,
                target_id=session.target_id,
                url=session.url,
            )
            return self._cdp_call_once(session, method, params)

    @staticmethod
    def _is_stale_target_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "no such target",
                "target closed",
                "inspected target navigated or closed",
                "cannot find context with specified id",
            )
        )

    @staticmethod
    def _cdp_call_once(session: BrowserSession, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        connection = None
        call_id = 1
        try:
            connection = websocket.create_connection(session.websocket_url, timeout=5)
            connection.send(json.dumps({"id": call_id, "method": method, "params": dict(params or {})}))
            while True:
                payload = json.loads(connection.recv())
                if payload.get("id") != call_id:
                    continue
                if payload.get("error"):
                    message = payload["error"].get("message") or "CDP 调用失败"
                    raise WeChatPublishingError(f"{method}：{message}")
                return payload.get("result") or {}
        except WeChatPublishingError:
            raise
        except (OSError, ValueError, websocket.WebSocketException) as exc:
            raise WeChatPublishingError(f"微信浏览器 CDP 会话失败：{exc}") from exc
        finally:
            if connection is not None:
                connection.close()
