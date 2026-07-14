import json
import sqlite3
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlparse

from ..database import Database
from .models import PublishAction, PublishResult


class PublishJobValidationError(RuntimeError):
    pass


class PublishCoordinator:
    def __init__(
        self,
        db: Database,
        publisher: Any,
        project_loader: Callable[[int], dict[str, Any]],
        account_loader: Callable[[int], dict[str, Any]],
        payload_builder: Callable[[dict[str, Any], str], dict[str, Any]],
    ):
        self.db = db
        self.publisher = publisher
        self.project_loader = project_loader
        self.account_loader = account_loader
        self.payload_builder = payload_builder

    def create_publish_job(
        self, project_id: int, account_id: int, action: PublishAction
    ) -> dict[str, Any]:
        project, account, payload = self._load_inputs(project_id, account_id, action)
        self._require_project_account(project_id, account_id)
        try:
            job_id = self.db.execute(
                """
                INSERT INTO publish_jobs (project_id, account_id, platform, action, status)
                VALUES (?, ?, '微信公众号', ?, 'pending')
                """,
                (project["id"], account["id"], action),
            )
        except sqlite3.IntegrityError as exc:
            if "one account_id" in str(exc):
                raise PublishJobValidationError("内容项目已绑定其他平台账号") from exc
            raise
        return self._execute(job_id, project, account, payload)

    def get_publish_job(self, job_id: int) -> dict[str, Any]:
        return self._public_job(self._get_row(job_id))

    def retry_publish_job(self, job_id: int) -> dict[str, Any]:
        job = self._get_row(job_id)
        if job["status"] != "failed":
            raise PublishJobValidationError("只有失败的发布任务可以重试")
        project, account, payload = self._load_inputs(
            int(job["project_id"]), int(job["account_id"]), str(job["action"])
        )
        self.db.execute(
            """
            UPDATE publish_jobs
            SET status = 'pending', platform_draft_id = '', platform_publish_id = '',
                platform_url = '', response_json = '{}', requires_confirmation = 0,
                failure_stage = '', failure_reason = '', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (job_id,),
        )
        return self._execute(job_id, project, account, payload)

    def resume_publish_job(self, job_id: int) -> dict[str, Any]:
        job = self._get_row(job_id)
        if job["status"] != "awaiting_confirmation":
            raise PublishJobValidationError("只有待人工确认的发布任务可以恢复")
        account = self.account_loader(int(job["account_id"]))
        context = self._job_context(job)
        self._persist_running(job_id)
        try:
            result = self.publisher.resume(account, context)
            self._persist_result(job_id, str(job["action"]), result)
        except Exception as exc:
            self._persist_failure(job_id, "恢复人工确认", exc)
        return self.get_publish_job(job_id)

    def _execute(
        self,
        job_id: int,
        project: dict[str, Any],
        account: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        job = self._get_row(job_id)
        self._persist_running(job_id)
        try:
            result = self.publisher.publish(payload, account, str(job["action"]))
            self._persist_result(job_id, str(job["action"]), result)
        except Exception as exc:
            stage = "提交平台草稿" if job["action"] == "draft" else "发布文章"
            self._persist_failure(job_id, stage, exc)
        return self.get_publish_job(job_id)

    def _load_inputs(
        self, project_id: int, account_id: int, action: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        if action not in {"draft", "publish"}:
            raise PublishJobValidationError("发布动作必须是 draft 或 publish")
        project = self.project_loader(project_id)
        account = self.account_loader(account_id)
        if project["platform"] != "微信公众号" or account["platform"] != "微信公众号":
            raise PublishJobValidationError("微信公众号以外的平台暂未开放真实发布")
        if account["auth_status"] != "已授权":
            raise PublishJobValidationError("所选账号未授权，请重新登录")
        payload = self.payload_builder(project, account["name"])
        return project, account, payload

    def _require_project_account(self, project_id: int, account_id: int) -> None:
        existing = self.db.fetch_one(
            "SELECT account_id FROM publish_jobs WHERE project_id = ? ORDER BY id LIMIT 1",
            (project_id,),
        )
        if existing and int(existing["account_id"]) != account_id:
            raise PublishJobValidationError("内容项目已绑定其他平台账号")

    def _persist_running(self, job_id: int) -> None:
        self.db.execute(
            """
            UPDATE publish_jobs
            SET status = 'checking_login', failure_stage = '', failure_reason = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (job_id,),
        )

    def _persist_result(self, job_id: int, action: str, result: PublishResult) -> None:
        response = result.response if isinstance(result.response, Mapping) else {}
        response_json = json.dumps(dict(response), ensure_ascii=False)
        if result.status == "awaiting_confirmation":
            if action != "publish" or not result.requires_confirmation:
                raise RuntimeError("平台人工确认结果与发布动作不匹配")
            self._require_job_context(response)
        elif action == "draft":
            if result.status != "platform_draft":
                raise RuntimeError("平台未返回可验证的草稿成功结果")
            self._require_platform_evidence(
                result.platform_draft_id, result.platform_url, response, "草稿"
            )
        else:
            if result.status != "published":
                raise RuntimeError("平台未返回可验证的发布成功结果")
            self._require_platform_evidence(
                result.platform_publish_id, result.platform_url, response, "发布"
            )
        self.db.execute(
            """
            UPDATE publish_jobs
            SET status = ?, platform_draft_id = ?, platform_publish_id = ?,
                platform_url = ?, response_json = ?, requires_confirmation = ?,
                failure_stage = '', failure_reason = '', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                result.status,
                result.platform_draft_id,
                result.platform_publish_id,
                result.platform_url,
                response_json,
                int(result.requires_confirmation),
                job_id,
            ),
        )

    def _persist_failure(self, job_id: int, stage: str, exc: Exception) -> None:
        reason = str(exc).strip() or exc.__class__.__name__
        reason = self._redact_sensitive_text(self._get_row(job_id), reason)
        self.db.execute(
            """
            UPDATE publish_jobs
            SET status = 'failed', requires_confirmation = 0,
                failure_stage = ?, failure_reason = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (stage, reason, job_id),
        )

    def _get_row(self, job_id: int) -> dict[str, Any]:
        job = self.db.fetch_one("SELECT * FROM publish_jobs WHERE id = ?", (job_id,))
        if not job:
            raise LookupError("发布任务不存在")
        return job

    @staticmethod
    def _require_platform_evidence(
        platform_id: str, platform_url: str, response: Mapping[str, Any], label: str
    ) -> None:
        parsed = urlparse(platform_url)
        if not platform_id or parsed.scheme != "https" or parsed.hostname != "mp.weixin.qq.com":
            raise RuntimeError(f"平台未返回可验证的{label}回执")
        if not response:
            raise RuntimeError(f"平台未返回{label}响应证据")

    @staticmethod
    def _require_job_context(response: Mapping[str, Any]) -> dict[str, Any]:
        context = response.get("job_context")
        if not isinstance(context, Mapping) or not context:
            raise RuntimeError("平台未返回可恢复的人工确认上下文")
        return dict(context)

    def _job_context(self, job: Mapping[str, Any]) -> dict[str, Any]:
        try:
            response = json.loads(str(job.get("response_json") or "{}"))
        except json.JSONDecodeError as exc:
            raise PublishJobValidationError("发布任务的恢复上下文已损坏") from exc
        return self._require_job_context(response)

    def _public_job(self, job: Mapping[str, Any]) -> dict[str, Any]:
        try:
            response = json.loads(str(job.get("response_json") or "{}"))
        except json.JSONDecodeError:
            response = {}
        resource = {key: value for key, value in job.items() if key != "response_json"}
        resource["requires_confirmation"] = bool(resource["requires_confirmation"])
        resource["failure_reason"] = self._redact_sensitive_text(
            job, str(resource["failure_reason"])
        )
        resource["response"] = self._redact_browser_context(response)
        return resource

    def _redact_sensitive_text(self, job: Mapping[str, Any], value: str) -> str:
        account = self.account_loader(int(job["account_id"]))
        sensitive_values = [str(account.get("profile_path") or "")]
        try:
            response = json.loads(str(job.get("response_json") or "{}"))
        except json.JSONDecodeError:
            response = {}
        context = response.get("job_context") if isinstance(response, Mapping) else None
        if isinstance(context, Mapping):
            sensitive_values.extend(self._string_values(context))
        for sensitive in sorted(set(sensitive_values), key=len, reverse=True):
            if sensitive:
                value = value.replace(sensitive, "[已隐藏浏览器上下文]")
        return value

    @classmethod
    def _string_values(cls, value: Any) -> list[str]:
        if isinstance(value, Mapping):
            return [item for nested in value.values() for item in cls._string_values(nested)]
        if isinstance(value, list):
            return [item for nested in value for item in cls._string_values(nested)]
        return [value] if isinstance(value, str) else []

    @classmethod
    def _redact_browser_context(cls, value: Any) -> Any:
        sensitive_keys = {
            "browser_context",
            "cookies",
            "debugger_url",
            "job_context",
            "profile_path",
            "resume_handle",
            "session",
            "session_id",
            "token",
            "websocket_url",
        }
        if isinstance(value, Mapping):
            return {
                key: cls._redact_browser_context(item)
                for key, item in value.items()
                if str(key).lower() not in sensitive_keys
            }
        if isinstance(value, list):
            return [cls._redact_browser_context(item) for item in value]
        return value


__all__ = ["PublishCoordinator", "PublishJobValidationError"]
