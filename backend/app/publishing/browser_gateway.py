import base64
import html
import json
import socket
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
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
    "input[type='file'][accept*='image']",
)
INLINE_FILE_SELECTORS = (
    ".js_editor_insertimage input[type='file']",
    ".edui-for-insertimage input[type='file']",
    ".js_image_upload input[type='file']",
    "input[type='file'][accept*='image']",
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


class WeChatPublishingError(RuntimeError):
    pass


@dataclass
class BrowserSession:
    session_id: str
    profile_path: Path
    port: int
    target_id: str
    websocket_url: str
    process: subprocess.Popen[Any] | None = None
    url: str = WECHAT_HOME_URL


class ChromeCDPBrowserGateway:
    def __init__(
        self,
        chrome_path: str | Path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        startup_timeout: float = 20,
        result_timeout: float = 45,
    ):
        self.chrome_path = Path(chrome_path)
        self.startup_timeout = startup_timeout
        self.result_timeout = result_timeout
        self._sessions: dict[str, BrowserSession] = {}

    def open_profile(self, profile_path: Path, url: str) -> BrowserSession:
        profile_path = profile_path.expanduser().resolve()
        if not profile_path.is_dir():
            raise WeChatPublishingError(f"浏览器 Profile 不存在：{profile_path}")
        if not self.chrome_path.is_file():
            raise WeChatPublishingError("未找到 Google Chrome，无法打开微信公众号后台")
        port = self._free_port()
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
        try:
            target = self._wait_for_target(port, process)
        except Exception:
            if process.poll() is None:
                process.terminate()
            raise
        session = BrowserSession(
            session_id=uuid.uuid4().hex,
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

    def resume_profile(self, profile_path: Path, job_context: Mapping[str, Any]) -> BrowserSession:
        session_id = str(job_context.get("session_id") or "")
        retained = self._sessions.get(session_id)
        if retained and retained.profile_path == profile_path.expanduser().resolve():
            self._refresh_target(retained)
            return retained
        try:
            port = int(job_context["debug_port"])
            target_id = str(job_context["target_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WeChatPublishingError("扫码确认会话信息不完整，无法恢复") from exc
        target = self._target(port, target_id)
        if not target:
            raise WeChatPublishingError("扫码确认浏览器会话已关闭，请重新发起发布")
        session = BrowserSession(
            session_id=session_id or uuid.uuid4().hex,
            profile_path=profile_path.expanduser().resolve(),
            port=port,
            target_id=target_id,
            websocket_url=str(target["webSocketDebuggerUrl"]),
            url=str(target.get("url") or job_context.get("platform_url") or WECHAT_HOME_URL),
        )
        self._sessions[session.session_id] = session
        return session

    def navigate(self, session: BrowserSession, url: str) -> None:
        self._cdp_call(session, "Page.navigate", {"url": url})
        self._wait_for_document(session)

    def evaluate(self, session: BrowserSession, expression: str) -> Any:
        result = self._cdp_call(
            session,
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        ).get("result") or {}
        if result.get("subtype") == "error":
            raise WeChatPublishingError(str(result.get("description") or "微信页面脚本执行失败"))
        return result.get("value")

    def set_file_input(
        self,
        session: BrowserSession,
        selector_candidates: Sequence[str],
        file_path: str | Path,
        label: str = "图片",
    ) -> str:
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise WeChatPublishingError(f"{label}文件不存在：{path}")
        expression = self._candidate_expression(selector_candidates, "node")
        remote = self._cdp_call(
            session,
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": False},
        ).get("result") or {}
        object_id = str(remote.get("objectId") or "")
        if not object_id:
            raise WeChatPublishingError(
                f"微信编辑器未找到{label}上传控件，已尝试选择器：{', '.join(selector_candidates)}"
            )
        self._cdp_call(session, "DOM.setFileInputFiles", {"files": [str(path)], "objectId": object_id})
        return str(self.evaluate(session, f"({expression}).selector") or "")

    def screenshot(self, session: BrowserSession, destination: str | Path | None = None) -> str:
        path = Path(destination) if destination else Path(tempfile.gettempdir()) / f"wechat-confirm-{int(time.time())}.png"
        result = self._cdp_call(session, "Page.captureScreenshot", {"format": "png", "fromSurface": True})
        data = str(result.get("data") or "")
        if not data:
            raise WeChatPublishingError("微信确认页面截图失败")
        path.write_bytes(base64.b64decode(data))
        return str(path)

    def close(self, session: BrowserSession) -> None:
        self._sessions.pop(session.session_id, None)
        try:
            self._cdp_call(session, "Browser.close")
        except Exception:
            if session.process and session.process.poll() is None:
                session.process.terminate()

    def session_context(self, session: BrowserSession) -> dict[str, Any]:
        current_url = self._current_url(session)
        return {
            "session_id": session.session_id,
            "debug_port": session.port,
            "target_id": session.target_id,
            "profile_path": str(session.profile_path),
            "platform_url": current_url,
        }

    def require_authenticated(self, session: BrowserSession) -> None:
        deadline = time.time() + 12
        last_url = ""
        while time.time() < deadline:
            last_url = self._current_url(session)
            if "/cgi-bin/" in last_url and "token=" in last_url:
                return
            cookies = self._cdp_call(session, "Network.getAllCookies").get("cookies") or []
            names = {str(cookie.get("name") or "") for cookie in cookies}
            if names & {"slave_sid", "bizuin"}:
                return
            time.sleep(0.5)
        raise WeChatPublishingError(f"微信公众号登录态失效，请重新扫码（当前页面：{last_url or '未知'}）")

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
        self.set_file_input(session, COVER_FILE_SELECTORS, str(payload["cover_path"]), "封面图")
        for block in payload.get("blocks") or []:
            image_path = str(block.get("image_path") or "").strip()
            if image_path:
                self.set_file_input(session, INLINE_FILE_SELECTORS, image_path, "正文配图")
                time.sleep(0.5)

    def click_save_draft(self, session: BrowserSession) -> None:
        self._click_first(session, SAVE_DRAFT_SELECTORS, "保存草稿按钮")

    def click_publish(self, session: BrowserSession) -> None:
        self._click_first(session, PUBLISH_SELECTORS, "发表按钮")
        time.sleep(0.8)
        self._click_first(session, PUBLISH_CONFIRM_SELECTORS, "发表确认按钮", required=False)

    def verify_draft(self, session: BrowserSession, title: str) -> dict[str, Any]:
        return self._wait_for_receipt(session, title, kind="draft")

    def verify_publish_or_confirmation(self, session: BrowserSession, title: str) -> dict[str, Any]:
        return self._wait_for_receipt(session, title, kind="publish")

    def _wait_for_receipt(self, session: BrowserSession, title: str, kind: str) -> dict[str, Any]:
        deadline = time.time() + self.result_timeout
        while time.time() < deadline:
            observation = self.evaluate(session, self._receipt_expression(title, kind))
            if not isinstance(observation, dict):
                time.sleep(0.5)
                continue
            platform_url = self._wechat_url(str(observation.get("evidenceUrl") or observation.get("url") or ""))
            if kind == "publish" and observation.get("confirmation"):
                screenshot = self.screenshot(session)
                return {
                    "status": "awaiting_confirmation",
                    "platform_url": platform_url,
                    "response": {
                        "selector": observation.get("confirmationSelector"),
                        "text": observation.get("confirmationText"),
                        "url": observation.get("url"),
                        "screenshot": screenshot,
                    },
                }
            platform_id = str(observation.get("platformId") or "").strip()
            if platform_id and platform_url:
                return {
                    "status": "platform_draft" if kind == "draft" else "published",
                    "draft_id" if kind == "draft" else "publish_id": platform_id,
                    "platform_url": platform_url,
                    "response": {
                        "selector": observation.get("selector"),
                        "attribute": observation.get("attribute"),
                        "value": platform_id,
                        "text": observation.get("text"),
                        "url": observation.get("url"),
                    },
                }
            time.sleep(0.5)
        label = "草稿" if kind == "draft" else "发布"
        raise WeChatPublishingError(f"微信页面未返回可验证的{label}结果，请检查浏览器页面后重试")

    @staticmethod
    def _receipt_expression(title: str, kind: str) -> str:
        title_json = json.dumps(title, ensure_ascii=False)
        id_attributes = ["data-draft-id", "data-media-id", "data-appmsgid", "data-id"] if kind == "draft" else ["data-publish-id", "data-msg-id", "data-id"]
        query_names = ["draft_id", "media_id", "appmsgid"] if kind == "draft" else ["publish_id", "msgid"]
        success_selectors = (
            ["[data-draft-id]", "[data-media-id]", ".js_draft_item[data-id]", ".appmsg[data-appmsgid]"]
            if kind == "draft"
            else ["[data-publish-id]", "[data-msg-id]", ".publish_success[data-id]", ".js_publish_success[data-id]"]
        )
        return f"""
        (() => {{
          const expectedTitle = {title_json};
          const selectors = {json.dumps(success_selectors)};
          const attributes = {json.dumps(id_attributes)};
          const queryNames = {json.dumps(query_names)};
          const confirmationSelectors = ['.js_qrcode', '.weui-desktop-qrcheck', '[class*=qrcode]', '[class*=qr_code]'];
          const bodyText = document.body?.innerText || '';
          const confirmationText = ['管理员扫码', '微信扫码确认', '运营者扫码', '管理员确认'].find((text) => bodyText.includes(text));
          if (confirmationText) {{
            const confirmationNode = confirmationSelectors.map((selector) => [selector, document.querySelector(selector)]).find((item) => item[1]);
            if (confirmationNode) return {{
              url: location.href,
              confirmation: true,
              confirmationSelector: confirmationNode[0],
              confirmationText
            }};
          }}
          for (const selector of selectors) {{
            for (const node of document.querySelectorAll(selector)) {{
              const container = node.closest('li, tr, article, .weui-desktop-panel, .appmsg, .publish_success') || node;
              const text = (container.innerText || node.innerText || '').trim();
              if (expectedTitle && text && !text.includes(expectedTitle)) continue;
              for (const attribute of attributes) {{
                const value = node.getAttribute(attribute) || container.getAttribute?.(attribute);
                if (value) return {{url: location.href, selector, attribute, platformId: value, text}};
              }}
              const link = node.closest('a') || node.querySelector?.('a[href]') || container.querySelector?.('a[href]');
              if (link?.href) {{
                const parsed = new URL(link.href, location.href);
                for (const queryName of queryNames) {{
                  const value = parsed.searchParams.get(queryName);
                  if (value) return {{url: location.href, evidenceUrl: parsed.href, selector, attribute: `href:${{queryName}}`, platformId: value, text}};
                }}
              }}
            }}
          }}
          const current = new URL(location.href);
          for (const queryName of queryNames) {{
            const value = current.searchParams.get(queryName);
            if (value) return {{url: location.href, evidenceUrl: location.href, selector: 'location.href', attribute: queryName, platformId: value, text: document.title}};
          }}
          return {{url: location.href}};
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
          for (const selector of selectors) {{
            const node = document.querySelector(selector);
            if (!node) continue;
            const prototype = node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
            setter ? setter.call(node, {json.dumps(value, ensure_ascii=False)}) : (node.value = {json.dumps(value, ensure_ascii=False)});
            node.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText', data: {json.dumps(value, ensure_ascii=False)}}}));
            node.dispatchEvent(new Event('change', {{bubbles: true}}));
            return {{ok: true, selector}};
          }}
          return {{ok: false}};
        }})()
        """
        result = self.evaluate(session, expression)
        if required and (not isinstance(result, dict) or not result.get("ok")):
            raise WeChatPublishingError(f"微信编辑器未找到{label}控件，已尝试选择器：{', '.join(selectors)}")

    def _set_editor_html(self, session: BrowserSession, content_html: str) -> None:
        expression = f"""
        (() => {{
          const selectors = {json.dumps(list(EDITOR_SELECTORS))};
          for (const selector of selectors) {{
            const node = document.querySelector(selector);
            if (!node) continue;
            node.focus();
            node.innerHTML = {json.dumps(content_html, ensure_ascii=False)};
            node.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText'}}));
            return {{ok: true, selector}};
          }}
          return {{ok: false}};
        }})()
        """
        result = self.evaluate(session, expression)
        if not isinstance(result, dict) or not result.get("ok"):
            raise WeChatPublishingError(f"微信编辑器未找到正文控件，已尝试选择器：{', '.join(EDITOR_SELECTORS)}")

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
          for (const selector of selectors) {{
            const node = document.querySelector(selector);
            if (!node || node.disabled || node.getAttribute('aria-disabled') === 'true') continue;
            node.scrollIntoView({{block: 'center'}});
            node.click();
            return {{ok: true, selector, text: (node.innerText || node.textContent || '').trim()}};
          }}
          return {{ok: false}};
        }})()
        """
        result = self.evaluate(session, expression)
        found = isinstance(result, dict) and bool(result.get("ok"))
        if required and not found:
            raise WeChatPublishingError(f"微信页面未找到{label}，已尝试选择器：{', '.join(selectors)}")
        return found

    @staticmethod
    def _candidate_expression(selectors: Sequence[str], result_name: str) -> str:
        return f"""
        (() => {{
          const selectors = {json.dumps(list(selectors))};
          for (const selector of selectors) {{
            const {result_name} = document.querySelector(selector);
            if ({result_name}) {{ {result_name}.selector = selector; return {result_name}; }}
          }}
          return null;
        }})()
        """

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
    def _wechat_url(value: str) -> str:
        parsed = urllib.parse.urlparse(value)
        return value if parsed.scheme == "https" and parsed.hostname == "mp.weixin.qq.com" else ""

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

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
