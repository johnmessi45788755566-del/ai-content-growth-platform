import json
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

import websocket


PLATFORM_LOGIN_URLS = {
    "微信公众号": "https://mp.weixin.qq.com/",
    "视频号": "https://channels.weixin.qq.com/platform",
    "小红书": "https://creator.xiaohongshu.com/new/home",
    "抖音": "https://creator.douyin.com/",
}

PLATFORM_COOKIE_MARKERS = {
    "微信公众号": {"slave_sid", "bizuin"},
    "视频号": {"finder_uin", "session_key", "wxuin"},
    "小红书": {
        "access-token-creator.xiaohongshu.com",
        "customer-sso-sid",
        "galaxy.creator.beaker.session.id",
        "galaxy_creator_session_id",
    },
    "抖音": {"sessionid", "sessionid_ss", "uid_tt", "uid_tt_ss"},
}

PLATFORM_ID_COOKIES = {
    "微信公众号": ["bizuin"],
    "视频号": ["finder_uin", "wxuin"],
    "小红书": ["x-user-id-creator.xiaohongshu.com", "customerClientId"],
    "抖音": ["uid_tt", "uid_tt_ss"],
}

ACCOUNT_NAME_SELECTORS = {
    "微信公众号": [
        ".weui-desktop-account__nickname",
        ".weui-desktop-account__name",
        ".account_meta_info .nickname",
        ".account_info .nickname",
        "[class*='account_name']",
        "[class*='nickname']",
    ],
    "视频号": [".account-name", "[class*='accountName']", "[class*='nickname']"],
    "小红书": [
        ".name-box",
        ".user-name",
        ".user-info .name",
        "[class*='userName']",
        "[class*='nickname']",
        "[class*='user-name']",
    ],
    "抖音": [
        "[class*='header-'] [class^='name-']",
        ".user-info-name",
        "[class*='userName']",
        "[class*='nickname']",
        "[class*='user-name']",
    ],
}


def parse_metric_number(value: str | int | float | None) -> int:
    text = str(value or "0").strip().replace(",", "").replace("+", "")
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*([万亿]?)", text)
    if not match:
        return 0
    number = float(match.group(1))
    multiplier = {"万": 10_000, "亿": 100_000_000}.get(match.group(2), 1)
    return max(0, int(number * multiplier))


def _metric_after_label(text: str, *labels: str) -> int:
    for label in labels:
        pattern = rf"{re.escape(label)}(?:\([^)]*\)|（[^）]*）)?\s*(?:\n\s*)+([+\d][\d,.]*\s*[万亿]?)"
        match = re.search(pattern, text)
        if match:
            return parse_metric_number(match.group(1))
    return 0


class BrowserAuthManager:
    def __init__(
        self,
        profiles_dir: str | Path,
        chrome_path: str | Path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        timeout_seconds: int = 300,
    ):
        self.profiles_dir = Path(profiles_dir)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.chrome_path = Path(chrome_path)
        self.timeout_seconds = timeout_seconds
        self.sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start(
        self,
        platform: str,
        account_name: str,
        on_success: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        if platform not in PLATFORM_LOGIN_URLS:
            raise RuntimeError("暂不支持该平台登录")
        if not self.chrome_path.exists():
            raise RuntimeError("未找到 Google Chrome，请先安装 Chrome")

        session_id = uuid.uuid4().hex
        profile_path = self.profiles_dir / platform / session_id
        profile_path.mkdir(parents=True, exist_ok=True)
        self._enable_session_restore(profile_path)
        port = self._free_port()
        login_url = PLATFORM_LOGIN_URLS[platform]
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
                "--restore-last-session",
                "--new-window",
                login_url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        session = {
            "session_id": session_id,
            "platform": platform,
            "status": "waiting",
            "message": "已打开平台登录页，请在 Chrome 中完成扫码登录",
            "login_url": login_url,
            "profile_path": str(profile_path),
            "account": None,
            "error": "",
            "process": process,
            "port": port,
        }
        with self._lock:
            self.sessions[session_id] = session
        threading.Thread(
            target=self._monitor,
            args=(session_id, account_name.strip(), on_success),
            daemon=True,
        ).start()
        return self._public_session(session)

    def sync(self, account: dict[str, Any]) -> dict[str, Any]:
        platform = str(account.get("platform") or "")
        profile_path = Path(str(account.get("profile_path") or ""))
        if platform not in PLATFORM_LOGIN_URLS:
            raise RuntimeError("暂不支持该平台同步")
        if not profile_path.exists():
            raise RuntimeError("账号登录态不存在，请重新扫码登录")
        if not self.chrome_path.exists():
            raise RuntimeError("未找到 Google Chrome，无法同步平台数据")

        self._enable_session_restore(profile_path)
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
                "--restore-last-session",
                "--headless=new",
                PLATFORM_LOGIN_URLS[platform],
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.time() + 35
            page = None
            navigated = False
            last_error = ""
            while time.time() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("同步浏览器启动失败，请稍后重试")
                try:
                    page = self._platform_page(port, platform)
                    if page:
                        host = urllib.parse.urlparse(PLATFORM_LOGIN_URLS[platform]).netloc
                        if host not in str(page.get("url") or "") and not navigated:
                            self._cdp_call(page, "Page.navigate", {"url": PLATFORM_LOGIN_URLS[platform]})
                            navigated = True
                        elif host in str(page.get("url") or "") and self._is_authenticated(platform, page):
                            data = self._collect_platform_data(platform, page)
                            if data["metrics"] or data["works"]:
                                return data
                except (OSError, RuntimeError, urllib.error.URLError, websocket.WebSocketException) as exc:
                    last_error = str(exc)
                time.sleep(1)
            if page and not self._is_authenticated(platform, page):
                raise RuntimeError("平台登录态已失效，请重新扫码登录")
            raise RuntimeError(last_error or "未能从平台页面读取账号数据，请打开平台后台确认页面可访问")
        finally:
            self._close_browser(process, port)

    def get_status(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self.sessions.get(session_id)
            if not session:
                raise RuntimeError("登录会话不存在或已失效")
            return self._public_session(session)

    def cancel(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self.sessions.get(session_id)
            if not session:
                raise RuntimeError("登录会话不存在或已失效")
            if session["status"] == "waiting":
                session["status"] = "cancelled"
                session["message"] = "登录已取消"
            process = session["process"]
        self._close_browser(process, session.get("port"))
        return self._public_session(session)

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self.sessions.values())
        for session in sessions:
            self._close_browser(session["process"], session.get("port"))

    def _monitor(
        self,
        session_id: str,
        requested_name: str,
        on_success: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        deadline = time.time() + self.timeout_seconds
        while time.time() < deadline:
            with self._lock:
                session = self.sessions[session_id]
                if session["status"] != "waiting":
                    return
                process = session["process"]
                port = session["port"]
                platform = session["platform"]
            if process.poll() is not None:
                self._fail(session_id, "登录窗口已关闭，未检测到登录成功")
                return
            try:
                page = self._platform_page(port, platform)
                if page and self._is_authenticated(platform, page):
                    with self._lock:
                        session["message"] = "已检测到登录成功，正在读取真实账号信息"
                    identity = self._detect_account_identity(platform, port)
                    detected_name = identity.get("account_name", "")
                    external_id = identity.get("external_id", "")
                    sync_data = None
                    sync_error = ""
                    try:
                        sync_data = self._collect_platform_data(platform, page)
                    except Exception as exc:
                        sync_error = str(exc)
                    account = on_success(
                        {
                            "platform": platform,
                            "account_name": detected_name or requested_name or self._fallback_account_name(platform, external_id),
                            "external_id": external_id,
                            "profile_path": session["profile_path"],
                            "login_url": page.get("url") or session["login_url"],
                            "sync_data": sync_data,
                            "sync_error": sync_error,
                        }
                    )
                    with self._lock:
                        session["status"] = "success"
                        session["message"] = "登录成功，账号已加入工作台"
                        session["account"] = account
                    self._close_browser(process, port)
                    return
            except Exception:
                pass
            time.sleep(2)
        self._fail(session_id, "登录超时，请重新发起登录")
        with self._lock:
            session = self.sessions[session_id]
        self._close_browser(session["process"], session.get("port"))

    def _platform_page(self, port: int, platform: str) -> dict[str, Any] | None:
        host = urllib.parse.urlparse(PLATFORM_LOGIN_URLS[platform]).netloc
        request = urllib.request.Request(f"http://127.0.0.1:{port}/json/list")
        with urllib.request.urlopen(request, timeout=2) as response:
            pages = json.loads(response.read().decode("utf-8"))
        for page in pages:
            if page.get("type") == "page" and host in page.get("url", ""):
                return page
        return next((page for page in pages if page.get("type") == "page"), None)

    def _is_authenticated(self, platform: str, page: dict[str, Any]) -> bool:
        url = page.get("url", "")
        if platform == "微信公众号" and "/cgi-bin/" in url and "token=" in url:
            return True
        cookies = self._cdp_call(page, "Network.getAllCookies").get("cookies", [])
        cookie_names = {cookie.get("name") for cookie in cookies}
        return bool(cookie_names & PLATFORM_COOKIE_MARKERS[platform])

    def _detect_account_name(self, platform: str, page: dict[str, Any]) -> str:
        selectors = json.dumps(ACCOUNT_NAME_SELECTORS[platform], ensure_ascii=False)
        expression = f"""
        (() => {{
          const selectors = {selectors};
          for (const selector of selectors) {{
            const node = document.querySelector(selector);
            const text = node?.textContent?.trim();
            if (text && text.length <= 80) return text;
          }}
          const roots = [window.wx?.cgiData, window.cgiData, window.__INITIAL_STATE__, window.__INITIAL_DATA__, window.__NEXT_DATA__];
          const keys = ['nickname', 'nickName', 'nick_name', 'userName', 'username', 'accountName', 'account_name'];
          const seen = new Set();
          const findName = (value, depth = 0) => {{
            if (!value || typeof value !== 'object' || depth > 4 || seen.has(value)) return '';
            seen.add(value);
            for (const key of keys) {{
              const text = value[key];
              if (typeof text === 'string' && text.trim() && text.trim().length <= 80) return text.trim();
            }}
            for (const child of Object.values(value).slice(0, 80)) {{
              const result = findName(child, depth + 1);
              if (result) return result;
            }}
            return '';
          }};
          for (const root of roots) {{
            const name = findName(root);
            if (name) return name;
          }}
          return '';
        }})()
        """
        result = self._cdp_call(
            page,
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
        )
        return str(result.get("result", {}).get("value") or "").strip()

    def _detect_account_identity(self, platform: str, port: int) -> dict[str, str]:
        deadline = time.time() + 12
        detected_name = ""
        external_id = ""
        while time.time() < deadline:
            page = self._platform_page(port, platform)
            if not page:
                time.sleep(1)
                continue
            cookies = self._cdp_call(page, "Network.getAllCookies").get("cookies", [])
            cookie_map = {cookie.get("name"): str(cookie.get("value") or "") for cookie in cookies}
            for cookie_name in PLATFORM_ID_COOKIES[platform]:
                if cookie_map.get(cookie_name):
                    external_id = cookie_map[cookie_name]
                    break
            detected_name = self._detect_account_name(platform, page)
            if detected_name:
                break
            time.sleep(1)
        return {"account_name": detected_name, "external_id": external_id}

    def _collect_platform_data(self, platform: str, page: dict[str, Any]) -> dict[str, Any]:
        works_expression = "[]"
        if platform == "微信公众号":
            works_expression = """
              [...document.querySelectorAll('.weui-desktop-mass-appmsg__bd_container')].map((node) => {
                const number = (selector) => node.querySelector(selector)?.textContent?.trim() || '0';
                const link = node.querySelector('.weui-desktop-mass-appmsg__title');
                return {
                  title: link?.querySelector('span')?.textContent?.trim() || link?.innerText?.trim() || '',
                  publish_url: link?.href || '',
                  content_type: '图文',
                  status: '已发布',
                  reads: number('.appmsg-view .weui-desktop-mass-media__data__inner'),
                  likes: number('.appmsg-like .weui-desktop-mass-media__data__inner'),
                  shares: number('.appmsg-share .weui-desktop-mass-media__data__inner'),
                  recommends: number('.appmsg-haokan .weui-desktop-mass-media__data__inner'),
                  comments: number('.appmsg-comment .weui-desktop-mass-media__data__inner'),
                  favorites: 0,
                  published_at: ''
                };
              }).filter((item) => item.title)
            """
        expression = f"""
        (() => JSON.stringify({{
          title: document.title,
          text: document.body?.innerText || '',
          works: {works_expression}
        }}))()
        """
        result = self._cdp_call(
            page,
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
        )
        raw_value = result.get("result", {}).get("value")
        if not raw_value:
            raise RuntimeError("平台页面未返回可同步数据")
        payload = json.loads(raw_value)
        text = str(payload.get("text") or "")
        if not self._data_page_ready(platform, text):
            raise RuntimeError("平台数据页面仍在加载")
        works = [self._normalize_work(item) for item in payload.get("works") or [] if item.get("title")]
        metrics = self._extract_metrics(platform, text, works)
        identity = {
            "account_name": self._detect_account_name(platform, page),
            "external_id": "",
        }
        cookies = self._cdp_call(page, "Network.getAllCookies").get("cookies", [])
        cookie_map = {cookie.get("name"): str(cookie.get("value") or "") for cookie in cookies}
        for cookie_name in PLATFORM_ID_COOKIES[platform]:
            if cookie_map.get(cookie_name):
                identity["external_id"] = cookie_map[cookie_name]
                break
        if platform == "小红书":
            match = re.search(r"小红书账号\s*[:：]\s*([^\s]+)", text)
            if match:
                identity["external_id"] = match.group(1)
        elif platform == "抖音":
            match = re.search(r"抖音号\s*[:：]\s*([^\s]+)", text)
            if match:
                identity["external_id"] = match.group(1)
        return {
            "source": "browser",
            **identity,
            "metrics": metrics,
            "works": works,
        }

    @staticmethod
    def _data_page_ready(platform: str, text: str) -> bool:
        markers = {
            "微信公众号": ("总用户数", "昨日阅读"),
            "抖音": ("粉丝", "数据中心"),
            "小红书": ("粉丝", "数据"),
            "视频号": ("粉丝", "数据"),
        }.get(platform, ())
        return bool(text.strip()) and all(marker in text for marker in markers)

    @staticmethod
    def _normalize_work(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": str(item.get("title") or "").strip(),
            "content_type": str(item.get("content_type") or "图文"),
            "status": str(item.get("status") or "已发布"),
            "publish_url": str(item.get("publish_url") or ""),
            "published_at": str(item.get("published_at") or ""),
            "reads": parse_metric_number(item.get("reads")),
            "recommends": parse_metric_number(item.get("recommends")),
            "likes": parse_metric_number(item.get("likes")),
            "comments": parse_metric_number(item.get("comments")),
            "shares": parse_metric_number(item.get("shares")),
            "favorites": parse_metric_number(item.get("favorites")),
        }

    @staticmethod
    def _extract_metrics(platform: str, text: str, works: list[dict[str, Any]]) -> dict[str, int]:
        totals = {
            key: sum(int(work.get(key) or 0) for work in works)
            for key in ("reads", "recommends", "likes", "comments", "shares", "favorites")
        }
        if platform == "微信公众号":
            totals["reads"] = _metric_after_label(text, "昨日阅读") or totals["reads"]
            totals["shares"] = _metric_after_label(text, "昨日分享") or totals["shares"]
            fans = _metric_after_label(text, "总用户数")
        elif platform == "抖音":
            fans = _metric_after_label(text, "粉丝")
            totals["reads"] = _metric_after_label(text, "播放量") or totals["reads"]
            totals["likes"] = _metric_after_label(text, "获赞") or totals["likes"]
            totals["comments"] = _metric_after_label(text, "作品评论") or totals["comments"]
            totals["shares"] = _metric_after_label(text, "作品分享") or totals["shares"]
        else:
            fans = _metric_after_label(text, "粉丝", "粉丝数")
            totals["reads"] = _metric_after_label(text, "阅读量", "观看数", "播放量") or totals["reads"]
            totals["recommends"] = _metric_after_label(text, "曝光数", "推荐数") or totals["recommends"]
            totals["likes"] = _metric_after_label(text, "获赞", "点赞数", "点赞") or totals["likes"]
            totals["comments"] = _metric_after_label(text, "评论数", "评论") or totals["comments"]
            totals["shares"] = _metric_after_label(text, "分享数", "分享") or totals["shares"]
            totals["favorites"] = _metric_after_label(text, "收藏数", "收藏") or totals["favorites"]
        return {"fans": fans, **totals}

    @staticmethod
    def _fallback_account_name(platform: str, external_id: str) -> str:
        if external_id:
            suffix = external_id[-8:] if len(external_id) > 8 else external_id
            return f"{platform}账号（ID {suffix}）"
        return f"{platform}账号（信息待完善）"

    def _cdp_call(self, page: dict[str, Any], method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        connection = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=3)
        try:
            connection.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
            while True:
                payload = json.loads(connection.recv())
                if payload.get("id") == 1:
                    return payload.get("result") or {}
        finally:
            connection.close()

    def _fail(self, session_id: str, message: str) -> None:
        with self._lock:
            session = self.sessions[session_id]
            session["status"] = "failed"
            session["message"] = message
            session["error"] = message

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _enable_session_restore(profile_path: Path) -> None:
        preferences_path = profile_path / "Default" / "Preferences"
        preferences_path.parent.mkdir(parents=True, exist_ok=True)
        if preferences_path.exists():
            try:
                preferences = json.loads(preferences_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError("Chrome 配置文件无效，请重新扫码登录") from exc
        else:
            preferences = {}
        if not isinstance(preferences, dict):
            raise RuntimeError("Chrome 配置文件无效，请重新扫码登录")

        profile = preferences.setdefault("profile", {})
        if not isinstance(profile, dict):
            raise RuntimeError("Chrome 配置文件无效，请重新扫码登录")
        session = profile.setdefault("session", {})
        if not isinstance(session, dict):
            raise RuntimeError("Chrome 配置文件无效，请重新扫码登录")
        session["restore_on_startup"] = 1

        temporary_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=preferences_path.parent,
                prefix=".Preferences-",
                delete=False,
            ) as temporary_file:
                temporary_path = temporary_file.name
                json.dump(preferences, temporary_file, ensure_ascii=False, separators=(",", ":"))
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, preferences_path)
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)

    @staticmethod
    def _wait_for_browser_exit(process: subprocess.Popen[Any]) -> bool:
        try:
            process.wait(timeout=3)
            return True
        except subprocess.TimeoutExpired:
            return False

    @staticmethod
    def _close_browser(process: subprocess.Popen[Any], port: int | None) -> None:
        if process.poll() is not None:
            return
        if port:
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/json/version",
                )
                with urllib.request.urlopen(request, timeout=1) as response:
                    version = json.loads(response.read().decode("utf-8"))
                websocket_url = version.get("webSocketDebuggerUrl")
                if websocket_url:
                    connection = websocket.create_connection(websocket_url, timeout=2)
                    connection.send(json.dumps({"id": 1, "method": "Browser.close"}))
                    connection.close()
                    if BrowserAuthManager._wait_for_browser_exit(process):
                        return
            except Exception:
                pass
        if process.poll() is not None:
            return
        process.terminate()
        if BrowserAuthManager._wait_for_browser_exit(process):
            return
        if process.poll() is not None:
            return
        process.kill()
        BrowserAuthManager._wait_for_browser_exit(process)

    @staticmethod
    def _public_account(account: Any) -> dict[str, Any] | None:
        if not isinstance(account, dict):
            return None
        fields = (
            "id",
            "platform",
            "name",
            "account_name",
            "auth_status",
            "monitor_status",
            "sync_status",
            "last_sync_at",
            "last_synced_at",
        )
        return {field: account[field] for field in fields if field in account}

    @staticmethod
    def _public_login_url(login_url: Any) -> str:
        try:
            parsed = urllib.parse.urlsplit(str(login_url or ""))
        except ValueError:
            return ""
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        return urllib.parse.urlunsplit((parsed.scheme, parsed.hostname, parsed.path, "", ""))

    @staticmethod
    def _public_session(session: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": session["session_id"],
            "platform": session["platform"],
            "status": session["status"],
            "message": session["message"],
            "login_url": BrowserAuthManager._public_login_url(session.get("login_url")),
            "account": BrowserAuthManager._public_account(session.get("account")),
            "error": session.get("error", ""),
        }
