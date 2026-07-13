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
        account_key = self._account_key(account)
        try:
            session = self.gateway.open_profile(profile_path, WECHAT_HOME_URL)
            self.gateway.require_authenticated(session)
            self.gateway.open_article_editor(session)
            self.gateway.fill_article(session, payload)
            baseline = self.gateway.capture_baseline(session, payload["title"], action)
            if action == "draft":
                self.gateway.click_save_draft(session)
                outcome = self.gateway.verify_draft(session, payload["title"], baseline)
            else:
                self.gateway.click_publish(session)
                outcome = self.gateway.verify_publish_or_confirmation(
                    session, payload["title"], baseline
                )
            result = self._result(
                outcome, action, session, payload["title"], baseline, account_key
            )
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
        resume_handle = self._resume_handle(job_context)
        account_key = self._account_key(account)
        session = None
        preserve_session = False
        try:
            session, title, baseline = self.gateway.resume_profile(
                profile_path, resume_handle, account_key
            )
            self.gateway.require_authenticated(session)
            outcome = self.gateway.verify_publish_or_confirmation(session, title, baseline)
            result = self._result(
                outcome, "publish", session, title, baseline, account_key
            )
            preserve_session = result.requires_confirmation
            return result
        except WeChatPublishingError:
            raise
        except Exception as exc:
            raise WeChatPublishingError(f"恢复微信公众号扫码确认失败：{exc}") from exc
        finally:
            if session is not None and not preserve_session:
                self.gateway.close(session)

    def cancel(self, account: Mapping[str, Any], job_context: Mapping[str, Any]) -> None:
        self._profile_path(account)
        self.gateway.cancel_pending(
            self._resume_handle(job_context), self._account_key(account)
        )

    def _result(
        self,
        outcome: Mapping[str, Any],
        action: PublishAction,
        session: Any,
        title: str,
        baseline: Mapping[str, Any],
        account_key: str,
    ) -> PublishingResult:
        if not isinstance(outcome, Mapping):
            raise WeChatPublishingError("微信页面未返回结构化平台结果")
        status = str(outcome.get("status") or "").strip()
        platform_url = self._public_wechat_url(str(outcome.get("platform_url") or "").strip())
        self._require_matching_baseline(outcome, baseline)

        if action == "draft":
            if status != "platform_draft":
                raise WeChatPublishingError(f"微信草稿流程返回了无效状态：{status or '空'}")
            draft_id = str(outcome.get("draft_id") or "").strip()
            corroboration = self._require_bound_receipt(
                outcome, baseline, title, draft_id, "草稿 ID"
            )
            return PublishingResult(
                status=status,
                platform_draft_id=draft_id,
                platform_url=platform_url,
                response=self._public_success_response(
                    "draft_created", draft_id, title, corroboration
                ),
            )

        if status == "awaiting_confirmation":
            confirmation = outcome.get("confirmation")
            if not isinstance(confirmation, Mapping) or confirmation.get("kind") != "operator_qr":
                raise WeChatPublishingError("微信页面未返回可验证的运营者扫码确认信号")
            resume_handle = self.gateway.retain_confirmation(
                session, account_key, title, baseline
            )
            return PublishingResult(
                status=status,
                platform_url=platform_url,
                response={
                    "source": "wechat_platform",
                    "event": "operator_confirmation_required",
                    "confirmation": "operator_qr",
                    "job_context": {"resume_handle": resume_handle},
                },
                requires_confirmation=True,
            )
        if status != "published":
            raise WeChatPublishingError(f"微信直发流程返回了无效状态：{status or '空'}")
        publish_id = str(outcome.get("publish_id") or "").strip()
        corroboration = self._require_bound_receipt(
            outcome, baseline, title, publish_id, "发布 ID"
        )
        return PublishingResult(
            status=status,
            platform_publish_id=publish_id,
            platform_url=platform_url,
            response=self._public_success_response(
                "article_published", publish_id, title, corroboration
            ),
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
    def _account_key(account: Mapping[str, Any]) -> str:
        account_id = str(account.get("id") or "").strip()
        if not account_id:
            raise WeChatPublishingError("微信公众号账号缺少稳定账号 ID")
        return account_id

    @staticmethod
    def _resume_handle(job_context: Mapping[str, Any]) -> str:
        if not isinstance(job_context, Mapping):
            raise WeChatPublishingError("扫码确认恢复参数格式不正确")
        handle = str(job_context.get("resume_handle") or "").strip()
        if not handle:
            raise WeChatPublishingError("扫码确认恢复参数缺少 resume_handle")
        return handle

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
    def _public_wechat_url(value: str) -> str:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme != "https" or parsed.hostname != "mp.weixin.qq.com":
            raise WeChatPublishingError("平台结果缺少可验证的微信页面 URL")
        return urllib.parse.urlunsplit(("https", "mp.weixin.qq.com", parsed.path or "/", "", ""))

    @staticmethod
    def _require_matching_baseline(
        outcome: Mapping[str, Any], baseline: Mapping[str, Any]
    ) -> None:
        baseline_id = str(baseline.get("baseline_id") or "")
        if not baseline_id or str(outcome.get("baseline_id") or "") != baseline_id:
            raise WeChatPublishingError("微信平台结果与本次操作基线不匹配")

    @staticmethod
    def _require_bound_receipt(
        outcome: Mapping[str, Any],
        baseline: Mapping[str, Any],
        title: str,
        platform_id: str,
        label: str,
    ) -> str:
        if not platform_id:
            raise WeChatPublishingError(f"微信页面未返回真实{label}")
        receipt = outcome.get("receipt")
        if not isinstance(receipt, Mapping) or str(receipt.get("id") or "") != platform_id:
            raise WeChatPublishingError(f"微信平台回执与{label}不一致")
        if str(receipt.get("title") or "").strip() != title.strip():
            raise WeChatPublishingError("微信平台回执未绑定本次文章标题")
        if receipt.get("source") not in {"dom", "api"}:
            raise WeChatPublishingError("微信平台回执不是来自平台 DOM 或 API")
        old_ids = {str(value) for value in baseline.get("receipt_ids") or []}
        if platform_id in old_ids:
            raise WeChatPublishingError(f"微信{label}不是本次操作新产生的回执")
        signal = outcome.get("post_action_signal")
        if not isinstance(signal, Mapping) or not signal.get("observed_after_action"):
            raise WeChatPublishingError("微信平台结果缺少本次操作后的后置信号")
        signal_kind = str(signal.get("kind") or "")
        if signal_kind not in {"success_message", "navigation", "api_response"}:
            raise WeChatPublishingError("微信平台结果缺少可验证的后置信号")
        return signal_kind

    @staticmethod
    def _public_success_response(
        event: str, platform_id: str, title: str, corroboration: str
    ) -> dict[str, Any]:
        return {
            "source": "wechat_platform",
            "event": event,
            "receipt_id": platform_id,
            "title": title,
            "corroboration": corroboration,
        }


# Transitional import compatibility while callers move to WeChatBrowserPublisher.
WeChatDraftPublisher = WeChatBrowserPublisher


__all__ = [
    "PublishResult",
    "WeChatBrowserPublisher",
    "WeChatDraftPublisher",
    "WeChatPublishingError",
]
