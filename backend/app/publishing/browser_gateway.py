import base64
import html
import json
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
                stderr=subprocess.DEVNULL,
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
        expression = self._file_input_expression(selector_candidates)
        payload = self._cdp_call(
            session,
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": False},
        )
        exception = payload.get("exceptionDetails") or {}
        if exception:
            detail = exception.get("exception") or {}
            message = str(detail.get("description") or exception.get("text") or "上传控件无效")
            raise WeChatPublishingError(f"微信编辑器{label}上传控件无效：{message}")
        remote = payload.get("result") or {}
        object_id = str(remote.get("objectId") or "")
        if not object_id:
            raise WeChatPublishingError(
                f"微信编辑器未找到{label}上传控件，已尝试选择器：{', '.join(selector_candidates)}"
            )
        self._cdp_call(session, "DOM.setFileInputFiles", {"files": [str(path)], "objectId": object_id})
        return self._wait_for_uploaded_image(session, role, before)

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

        sections = []
        for block in payload.get("blocks") or []:
            content = str(block.get("content") or "").strip()
            if content:
                sections.append(f"<p>{html.escape(content).replace(chr(10), '<br>')}</p>")
        self._set_editor_html(session, "".join(sections))

    def upload_article_assets(
        self, session: BrowserSession, payload: Mapping[str, Any]
    ) -> None:
        self.set_file_input(
            session,
            COVER_FILE_SELECTORS,
            str(payload["cover_path"]),
            "封面图",
            "cover",
        )
        for block in payload.get("blocks") or []:
            image_path = str(block.get("image_path") or "").strip()
            if image_path:
                self.set_file_input(
                    session,
                    INLINE_FILE_SELECTORS,
                    image_path,
                    "正文配图",
                    "inline",
                )

    def click_save_draft(self, session: BrowserSession) -> None:
        self._click_first(session, SAVE_DRAFT_SELECTORS, "保存草稿按钮")

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
    def _file_input_expression(selectors: Sequence[str]) -> str:
        return f"""
        (() => {{
          const selectors = {json.dumps(list(selectors))};
          const visible = (node) => !!node && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none' && node.getClientRects().length > 0;
          const visibleControl = (node) => visible(node) || visible(node.closest('label, [role=button], .weui-desktop-upload, .upload-box, [class*=upload]'));
          const candidates = [...new Set(selectors.flatMap((selector) => [...document.querySelectorAll(selector)]))];
          const matches = candidates.filter((node) => {{
            if (!(node instanceof HTMLInputElement) || node.type !== 'file' || node.disabled) return false;
            const accept = (node.accept || '').toLowerCase();
            return (accept.includes('image') || accept.includes('.jpg') || accept.includes('.jpeg') || accept.includes('.png')) && visibleControl(node);
          }});
          if (matches.length > 1) throw new Error(`ambiguous file input (${{matches.length}} unique nodes)`);
          if (matches.length === 1) return matches[0];
          return null;
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
          const candidates = [...new Set(selectors.flatMap((selector) => [...document.querySelectorAll(selector)]))];
          const matches = candidates.filter((node) => visible(node) && node.isContentEditable && node.getAttribute('aria-disabled') !== 'true');
          if (matches.length > 1) return {{ok: false, reason: 'ambiguous', count: matches.length}};
          if (matches.length === 1) {{
            const node = matches[0];
            node.focus();
            node.innerHTML = {json.dumps(content_html, ensure_ascii=False)};
            node.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText'}}));
            return {{ok: true}};
          }}
          return {{ok: false, reason: candidates.length ? 'hidden_or_disabled' : 'not_found'}};
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
                raise WeChatPublishingError("Chrome 启动失败，无法打开微信公众号后台")
            try:
                targets = self._targets(port)
                page = next((item for item in targets if item.get("type") == "page"), None)
                if page and page.get("webSocketDebuggerUrl"):
                    return page
            except Exception:
                pass
            time.sleep(0.25)
        raise WeChatPublishingError("Chrome 启动超时，未取得 CDP 页面会话")

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
