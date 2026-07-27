from typing import Any, Mapping

from .models import PublishAction, PublishResult
from .wechat import WeChatBrowserPublisher
from .xiaohongshu_patchright import XiaoHongShuPatchrightPublisher


class PlatformPublisherRouter:
    def __init__(self, publishers: Mapping[str, Any] | None = None):
        self.publishers = dict(
            publishers
            or {
                "微信公众号": WeChatBrowserPublisher(),
                "小红书": XiaoHongShuPatchrightPublisher(),
            }
        )
        self._trace_context: tuple[int, str] | None = None

    def set_trace_context(self, job_id: int, trace_path: str) -> None:
        self._trace_context = (job_id, trace_path)
        for publisher in self.publishers.values():
            setter = getattr(publisher, "set_trace_context", None)
            if callable(setter):
                setter(job_id, trace_path)

    def clear_trace_context(self) -> None:
        for publisher in self.publishers.values():
            clearer = getattr(publisher, "clear_trace_context", None)
            if callable(clearer):
                clearer()
        self._trace_context = None

    def publish(
        self,
        payload: dict[str, Any],
        account: Mapping[str, Any],
        action: PublishAction,
    ) -> PublishResult:
        return self._publisher(account).publish(payload, account, action)

    def publish_with_progress(
        self,
        payload: dict[str, Any],
        account: Mapping[str, Any],
        action: PublishAction,
        report_progress: Any,
    ) -> PublishResult:
        publisher = self._publisher(account)
        method = getattr(publisher, "publish_with_progress", None)
        if callable(method):
            return method(payload, account, action, report_progress)
        return publisher.publish(payload, account, action)

    def resume(self, account: Mapping[str, Any], job_context: Mapping[str, Any]) -> PublishResult:
        return self._publisher(account).resume(account, job_context)

    def cancel(self, account: Mapping[str, Any], job_context: Mapping[str, Any]) -> None:
        cancel = getattr(self._publisher(account), "cancel", None)
        if callable(cancel):
            cancel(account, job_context)

    def verify_login(self, account: Mapping[str, Any]) -> dict[str, Any]:
        verifier = getattr(self._publisher(account), "verify_login", None)
        if callable(verifier):
            return verifier(account)
        return {"verified": True}

    def bind_confirmation_expiry(self, job_context: Mapping[str, Any], callback: Any) -> None:
        for publisher in self.publishers.values():
            binder = getattr(publisher, "bind_confirmation_expiry", None)
            if callable(binder):
                binder(job_context, callback)
                return

    def _publisher(self, account: Mapping[str, Any]) -> Any:
        platform = str(account.get("platform") or "")
        publisher = self.publishers.get(platform)
        if not publisher:
            raise RuntimeError(f"{platform or '未知平台'}暂未接入真实发布")
        if self._trace_context:
            setter = getattr(publisher, "set_trace_context", None)
            if callable(setter):
                setter(*self._trace_context)
        return publisher


__all__ = ["PlatformPublisherRouter"]
