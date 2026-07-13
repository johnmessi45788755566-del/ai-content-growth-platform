import json
import urllib.parse
from pathlib import Path
from typing import Any, Mapping

from .browser_gateway import ChromeCDPBrowserGateway, WeChatPublishingError
from .models import PublishAction, PublishResult as PublishingResult


WECHAT_HOME_URL = "https://mp.weixin.qq.com/"


class PublishResult(PublishingResult):
    def __init__(
        self,
        status: str,
        platform_draft_id: str = "",
        platform_publish_id: str = "",
        platform_url: str = "",
        response: dict[str, Any] | None = None,
        requires_confirmation: bool = False,
        platform_draft_url: str = "",
    ):
        super().__init__(
            status=status,
            platform_draft_id=platform_draft_id,
            platform_publish_id=platform_publish_id,
            platform_url=platform_url or platform_draft_url,
            response=response or {},
            requires_confirmation=requires_confirmation,
        )

    @property
    def platform_draft_url(self) -> str:
        return self.platform_url


class WeChatBrowserPublisher:
    def __init__(self, gateway: Any | None = None):
        self.gateway = gateway or ChromeCDPBrowserGateway()

    def publish(
        self,
        payload: dict[str, Any],
        account: Mapping[str, Any],
        action: PublishAction,
    ) -> PublishingResult:
        self._validate_action(action)
        profile_path = self._profile_path(account)
        self._validate_payload(payload)
        session = None
        preserve_session = False
        try:
            session = self.gateway.open_profile(profile_path, WECHAT_HOME_URL)
            self.gateway.require_authenticated(session)
            self.gateway.open_article_editor(session)
            self.gateway.fill_article(session, payload)
            if action == "draft":
                self.gateway.click_save_draft(session)
                outcome = self.gateway.verify_draft(session, payload["title"])
            else:
                self.gateway.click_publish(session)
                outcome = self.gateway.verify_publish_or_confirmation(session, payload["title"])
            result = self._result(outcome, action, session, payload["title"])
            preserve_session = result.requires_confirmation
            return result
        except WeChatPublishingError:
            raise
        except Exception as exc:
            raise WeChatPublishingError(f"微信公众号浏览器发布失败：{exc}") from exc
        finally:
            if session is not None and not preserve_session:
                self.gateway.close(session)

    def resume(self, account: Mapping[str, Any], job_context: Mapping[str, Any]) -> PublishingResult:
        profile_path = self._profile_path(account)
        title = str(job_context.get("title") or "").strip()
        if not title:
            raise WeChatPublishingError("恢复扫码确认缺少文章标题")
        session = None
        preserve_session = False
        try:
            session = self.gateway.resume_profile(profile_path, job_context)
            self.gateway.require_authenticated(session)
            outcome = self.gateway.verify_publish_or_confirmation(session, title)
            result = self._result(outcome, "publish", session, title)
            preserve_session = result.requires_confirmation
            return result
        except WeChatPublishingError:
            raise
        except Exception as exc:
            raise WeChatPublishingError(f"恢复微信公众号扫码确认失败：{exc}") from exc
        finally:
            if session is not None and not preserve_session:
                self.gateway.close(session)

    def _result(
        self,
        outcome: Mapping[str, Any],
        action: PublishAction,
        session: Any,
        title: str,
    ) -> PublishingResult:
        if not isinstance(outcome, Mapping):
            raise WeChatPublishingError("微信页面未返回结构化平台结果")
        status = str(outcome.get("status") or "").strip()
        platform_url = str(outcome.get("platform_url") or "").strip()
        self._require_wechat_url(platform_url)
        raw_response = outcome.get("response")
        if not isinstance(raw_response, Mapping) or not raw_response:
            raise WeChatPublishingError("微信页面未返回非空页面证据")
        response = dict(raw_response)

        if action == "draft":
            if status != "platform_draft":
                raise WeChatPublishingError(f"微信草稿流程返回了无效状态：{status or '空'}")
            draft_id = str(outcome.get("draft_id") or "").strip()
            self._require_evidenced_id(draft_id, response, "草稿 ID")
            return PublishingResult(
                status=status,
                platform_draft_id=draft_id,
                platform_url=platform_url,
                response=response,
            )

        if status == "awaiting_confirmation":
            context = dict(self.gateway.session_context(session))
            context["title"] = title
            response["job_context"] = context
            return PublishingResult(
                status=status,
                platform_url=platform_url,
                response=response,
                requires_confirmation=True,
            )
        if status != "published":
            raise WeChatPublishingError(f"微信直发流程返回了无效状态：{status or '空'}")
        publish_id = str(outcome.get("publish_id") or "").strip()
        self._require_evidenced_id(publish_id, response, "发布 ID")
        return PublishingResult(
            status=status,
            platform_publish_id=publish_id,
            platform_url=platform_url,
            response=response,
        )

    @staticmethod
    def _validate_action(action: str) -> None:
        if action not in {"draft", "publish"}:
            raise WeChatPublishingError("微信发布 action 必须是 draft 或 publish")

    @staticmethod
    def _profile_path(account: Mapping[str, Any]) -> Path:
        if account.get("platform") not in {None, "", "微信公众号"}:
            raise WeChatPublishingError("所选账号不是微信公众号账号")
        if account.get("auth_status") not in {None, "", "已授权"}:
            raise WeChatPublishingError("微信公众号未授权，请重新扫码登录")
        raw_path = str(account.get("profile_path") or "").strip()
        path = Path(raw_path).expanduser() if raw_path else Path()
        if not raw_path or not path.is_dir():
            raise WeChatPublishingError(f"账号浏览器 Profile 不存在：{raw_path or '未配置'}")
        return path

    @staticmethod
    def _validate_payload(payload: Mapping[str, Any]) -> None:
        if not str(payload.get("title") or "").strip():
            raise WeChatPublishingError("微信公众号文章标题不能为空")
        WeChatBrowserPublisher._require_file(payload.get("cover_path"), "封面图")
        blocks = payload.get("blocks") or []
        if not isinstance(blocks, list):
            raise WeChatPublishingError("微信公众号文章 blocks 格式不正确")
        for block in blocks:
            image_path = str(block.get("image_path") or "").strip()
            if image_path:
                WeChatBrowserPublisher._require_file(image_path, "正文配图")

    @staticmethod
    def _require_file(value: Any, label: str) -> None:
        path = Path(str(value or "")).expanduser()
        if not value or not path.is_file():
            raise WeChatPublishingError(f"{label}文件不存在：{path}")

    @staticmethod
    def _require_wechat_url(value: str) -> None:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme != "https" or parsed.hostname != "mp.weixin.qq.com":
            raise WeChatPublishingError("平台结果缺少可验证的微信页面 URL")

    @staticmethod
    def _require_evidenced_id(platform_id: str, response: Mapping[str, Any], label: str) -> None:
        if not platform_id:
            raise WeChatPublishingError(f"微信页面未返回真实{label}")
        if platform_id not in json.dumps(response, ensure_ascii=False, sort_keys=True):
            raise WeChatPublishingError(f"微信页面证据不包含{label}")


# Transitional import compatibility while callers move to WeChatBrowserPublisher.
WeChatDraftPublisher = WeChatBrowserPublisher


__all__ = [
    "PublishResult",
    "WeChatBrowserPublisher",
    "WeChatDraftPublisher",
    "WeChatPublishingError",
]
