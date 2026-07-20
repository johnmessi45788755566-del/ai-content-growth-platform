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
                if role == "cover":
                    self._trigger_upload_entry(session, role)
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
                ):
                    self._trace("upload.input_reresolve_failed", role=role, label=label)
                    raise WeChatPublishingError(
                        f"微信编辑器{label}上传控件已刷新，但重新定位失败"
                    ) from last_error
                self._trace("upload.input_reresolved", role=role, label=label)

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
        if not backend_node_id and object_id:
            backend_node_id = self._backend_node_id_for_object(session, object_id)
        params: dict[str, Any] = {"files": [str(path)]}
        if backend_node_id:
            params["backendNodeId"] = backend_node_id
        elif object_id:
            params["objectId"] = object_id
        else:
            raise WeChatPublishingError("微信编辑器上传控件引用无效")
        return params

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
            try:
                self.set_file_input(
                    session,
                    COVER_FILE_SELECTORS,
                    cover_path,
                    "封面图",
                    "cover",
                )
            except WeChatPublishingError as exc:
                if not self._is_cover_upload_input_missing_error(exc):
                    raise
                self._trace(
                    "upload.cover_fallback_to_article_image_picker",
                    reason=str(exc),
                )
                self._close_cover_image_picker_if_open(session)
                self._focus_editor_for_upload(session)
                self.set_file_input(
                    session,
                    INLINE_FILE_SELECTORS,
                    cover_path,
                    "封面候选图",
                    "inline",
                )
                self._set_cover_from_article_image_picker(session, cover_path)

    @staticmethod
    def _is_cover_upload_input_missing_error(exc: Exception) -> bool:
        message = str(exc)
        return "封面图上传控件" in message and "未找到" in message

    def _article_body_html(self, payload: Mapping[str, Any]) -> str:
        sections = []
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
    def _block_content_html(block_type: str, content: str) -> str:
        escaped = html.escape(content).replace(chr(10), "<br>")
        if block_type == "heading":
            return f"<h2>{escaped}</h2>"
        if block_type == "intro":
            return f"<blockquote>{escaped}</blockquote>"
        return f"<p>{escaped}</p>"

    @staticmethod
    def _inline_image_html(image_path: str) -> str:
        if image_path.startswith("data:image/"):
            return (
                "<p>"
                f"<img src=\"{html.escape(image_path, quote=True)}\" "
                "style=\"max-width:100%;height:auto;display:block;margin:16px auto;\" />"
                "</p>"
            )
        if image_path.startswith("http://") or image_path.startswith("https://"):
            return (
                "<p>"
                f"<img src=\"{html.escape(image_path, quote=True)}\" "
                "style=\"max-width:100%;height:auto;display:block;margin:16px auto;\" />"
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
            "style=\"max-width:100%;height:auto;display:block;margin:16px auto;\" />"
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
          const visibleControl = (node) => visible(node) || visible(node.closest('label, [role=button], .weui-desktop-upload, .upload-box, [class*=upload]'));
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
          matches.sort((left, right) => left.priority - right.priority);
          const bestPriority = matches[0]?.priority;
          const bestMatches = matches.filter((record) => record.priority === bestPriority);
          if (bestMatches.length > 1) throw new Error(`ambiguous file input (${{bestMatches.length}} unique nodes)`);
          if (bestMatches.length === 1) {{
            if (marker) bestMatches[0].node.setAttribute('data-codex-upload-marker', marker);
            return bestMatches[0].node;
          }}
          return null;
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
        self, session: BrowserSession, cover_path: str
    ) -> dict[str, Any]:
        self._trace(
            "upload.cover_picker_from_article_image.start",
            file={"name": Path(cover_path).name},
        )
        triggered = self._trigger_upload_entry(session, "cover")
        if not triggered:
            raise WeChatPublishingError("微信封面选择入口未打开，无法从正文图片选择封面")
        result = self.evaluate(session, self._cover_image_picker_expression())
        if not (isinstance(result, Mapping) and result.get("ok")):
            reason = result.get("reason") if isinstance(result, Mapping) else "unknown"
            image_count = result.get("imageCount") if isinstance(result, Mapping) else 0
            dialog_text = result.get("dialogText") if isinstance(result, Mapping) else ""
            self._trace(
                "upload.cover_picker_from_article_image.failed",
                reason=reason,
                image_count=image_count,
                dialog_text=dialog_text,
            )
            raise WeChatPublishingError(
                f"微信封面选择失败，未能从正文图片选择封面；原因：{reason}；正文候选图片数：{image_count}"
            )
        self._trace(
            "upload.cover_picker_from_article_image.completed",
            image_count=result.get("imageCount"),
        )
        return {
            "role": "cover",
            "source": "wechat_article_image_picker",
            "remote_url": str(cover_path),
        }

    @staticmethod
    def _cover_image_picker_expression() -> str:
        return """
        (() => new Promise((resolve) => {
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const textOf = (node) => String(node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          const dialogs = [...document.querySelectorAll('.weui-desktop-dialog__wrp, .weui-desktop-dialog, [role="dialog"]')]
            .filter((node) => visible(node) && textOf(node).includes('选择图片') && textOf(node).includes('正文插入'));
          const dialog = dialogs[0] || document;
          const selectors = [
            '.appmsg_conent_img_container img',
            '.appmsg_content_img_container img',
            '.appmsg_content_img_list img',
            '.appmsg_conent_img_container',
            '.appmsg_content_img_container',
            '[class*=appmsg][class*=img] img',
            '[class*=image_item] img',
            '[class*=img_item] img',
            '[class*=image_item]',
            '[class*=img_item]'
          ];
          const candidates = [...new Set(selectors.flatMap((selector) => [...dialog.querySelectorAll(selector)]))]
            .filter((node) => visible(node));
          const imageCandidates = candidates.filter((node) => node.matches('img') || node.querySelector?.('img, [style*="background-image"]'));
          if (!imageCandidates.length) {
            resolve({ok: false, reason: 'article_image_not_found', imageCount: 0, dialogText: textOf(dialog).slice(0, 240)});
            return;
          }
          const node = imageCandidates[0];
          const target = node.closest('li, label, [role="button"], .appmsg_conent_img_container, .appmsg_content_img_container, [class*=image_item], [class*=img_item]') || node;
          target.scrollIntoView({block: 'center'});
          if (target !== node) target.click();
          else node.click();
          setTimeout(() => {
            const buttons = [...dialog.querySelectorAll('button, a, [role="button"]')].filter(visible);
            const next = buttons.find((button) => textOf(button).includes('下一步'));
            if (!next) {
              resolve({ok: false, reason: 'next_button_not_found', imageCount: imageCandidates.length, dialogText: textOf(dialog).slice(0, 240)});
              return;
            }
            if (next.disabled || next.getAttribute('aria-disabled') === 'true') {
              resolve({ok: false, reason: 'next_button_disabled', imageCount: imageCandidates.length, dialogText: textOf(dialog).slice(0, 240)});
              return;
            }
            next.click();
            resolve({ok: true, imageCount: imageCandidates.length});
          }, 350);
        }))()
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
        found = isinstance(result, dict) and bool(result.get("ok"))
        if found:
            if role == "cover":
                self._dispatch_mouse_click(session, result.get("rect"))
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
                "body *",
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
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const nodes = [...new Set(selectors.flatMap((selector) => [...document.querySelectorAll(selector)]))];
          const matches = nodes.filter((node) => {{
            if (!visible(node) || node.disabled || node.getAttribute('aria-disabled') === 'true') return false;
            const text = (node.innerText || node.textContent || node.getAttribute('aria-label') || node.getAttribute('title') || '').trim();
            return texts.some((value) => text.includes(value));
          }}).map((node) => {{
            const target = node.closest('label, button, a, [role=button]') || node;
            const text = (node.innerText || node.textContent || node.getAttribute('aria-label') || node.getAttribute('title') || '').trim();
            const rect = target.getBoundingClientRect();
            return {{
              node: target,
              area: Math.max(0, rect.width) * Math.max(0, rect.height),
              top: rect.top,
              left: rect.left,
              textLength: text.length,
            }};
          }});
          matches.sort((left, right) => left.textLength - right.textLength || left.area - right.area || left.top - right.top || left.left - right.left);
          if (matches.length >= 1) {{
            const node = matches[0].node;
            node.scrollIntoView({{block: 'center'}});
            const rect = node.getBoundingClientRect();
            node.click();
            return {{
              ok: true,
              count: matches.length,
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

    @staticmethod
    def _cdp_call(session: BrowserSession, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        connection = websocket.create_connection(session.websocket_url, timeout=5)
        call_id = 1
        try:
            connection.send(json.dumps({"id": call_id, "method": method, "params": dict(params or {})}))
            while True:
                payload = json.loads(connection.recv())
                if payload.get("id") != call_id:
                    continue
                if payload.get("error"):
                    message = payload["error"].get("message") or "CDP 调用失败"
                    raise WeChatPublishingError(f"{method}：{message}")
                return payload.get("result") or {}
        except (OSError, ValueError, websocket.WebSocketException) as exc:
            raise WeChatPublishingError(f"微信浏览器 CDP 会话失败：{exc}") from exc
        finally:
            connection.close()
