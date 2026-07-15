import json
import re
import secrets
import sqlite3
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlparse, urlsplit, urlunsplit

from ..database import Database
from .models import PublishAction, PublishResult


class PublishJobValidationError(RuntimeError):
    pass


REDACTED_BROWSER_CONTEXT = "[已隐藏浏览器上下文]"
ATTEMPT_TOKEN_KEY = "_attempt_token"
URL_PATTERN = re.compile(r"(?i)\b(?:https?|wss?)://[^\s<>\"']+")
AUTHORIZATION_PATTERN = re.compile(
    r"(?i)\bauthorization\s*[:=]\s*(?:(?:bearer|basic)\s+)?[^\s,;]+"
)
BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
COOKIE_PATTERN = re.compile(r"(?im)\bcookies?\s*[:=]\s*.*$")
SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?ix)"
    r"(?<![\w])"
    r"(?:token|code|state|password|passwd|session(?:_?(?:id|cache|state))?|cookies?|"
    r"storage_?state|verification_?code|private_?key|client_?secret|api_?token|"
    r"cookie_?jar|profile_?dir|debug_?info|auth(?:orization)?_?context|"
    r"resume(?:_?handle)?|cdp(?:_?url)?|"
    r"websocket(?:_?debugger_?url|_?url)?|debugger_?url|"
    r"profile(?:_?path)?|remote-debugging-port|port)"
    r"\s*(?::|=|\s)\s*"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
LOCAL_DEBUG_ADDRESS_PATTERN = re.compile(
    r"(?i)\b(?:localhost|127\.0\.0\.1|\[?::1\]?):\d{2,5}\b"
)
PUBLIC_JOB_FIELDS = (
    "id",
    "project_id",
    "account_id",
    "platform",
    "action",
    "status",
    "platform_draft_id",
    "platform_publish_id",
    "platform_url",
    "requires_confirmation",
    "failure_stage",
    "failure_reason",
    "created_at",
    "updated_at",
)
PUBLIC_RESPONSE_FIELDS = (
    "source",
    "event",
    "receipt_id",
    "title",
    "corroboration",
    "confirmation",
    "delivery_status",
    "request_id",
    "platform_draft_id",
    "publish_id",
    "url",
    "report_id",
    "author_name",
)
PUBLIC_RECEIPT_FIELDS = (
    "id",
    "source",
    "event",
    "receipt_id",
    "title",
    "corroboration",
    "confirmation",
    "delivery_status",
    "request_id",
    "platform_draft_id",
    "publish_id",
    "url",
    "report_id",
    "author_name",
)
PUBLIC_RECEIPT_CONTAINERS = ("platform_receipt", "receipt")
UNCERTAIN_IN_PROGRESS_STATUSES = (
    "checking_login",
    "uploading_assets",
    "filling_content",
    "publishing",
)
INTERRUPTED_FAILURE_STAGE = "发布中断"
INTERRUPTED_FAILURE_REASON = (
    "发布流程长时间无进展，无法确认平台是否已完成操作；"
    "请人工核对平台状态后再决定是否重试"
)
CONFIRMATION_FAILURE_STAGE = "人工确认已失效"
CONFIRMATION_EXPIRED_REASON = "运营者确认窗口已过期，平台操作未完成，请重试发布任务"


class PublishCoordinator:
    def __init__(
        self,
        db: Database,
        publisher: Any,
        project_loader: Callable[[int], dict[str, Any]],
        account_loader: Callable[[int], dict[str, Any]],
        payload_builder: Callable[[dict[str, Any], str], dict[str, Any]],
        confirmation_expired_callback: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.db = db
        self.publisher = publisher
        self.project_loader = project_loader
        self.account_loader = account_loader
        self.payload_builder = payload_builder
        self.confirmation_expired_callback = confirmation_expired_callback

    def create_publish_job(
        self, project_id: int, account_id: int, action: PublishAction
    ) -> dict[str, Any]:
        job = self.prepare_publish_job(project_id, account_id, action)
        if job["status"] != "pending":
            return job
        return self.execute_publish_job(int(job["id"]))

    def prepare_publish_job(
        self, project_id: int, account_id: int, action: PublishAction
    ) -> dict[str, Any]:
        if action not in {"draft", "publish"}:
            raise PublishJobValidationError("发布动作必须是 draft 或 publish")
        existing = self.get_current_publish_job(project_id, account_id, action)
        if existing:
            return existing
        project, account, _ = self._load_inputs(project_id, account_id, action)
        with self.db.transaction() as conn:
            mapped = conn.execute(
                """
                SELECT jobs.id
                FROM publish_job_idempotency AS keys
                JOIN publish_jobs AS jobs ON jobs.id = keys.publish_job_id
                WHERE keys.project_id = ? AND keys.account_id = ? AND keys.action = ?
                """,
                (project_id, account_id, action),
            ).fetchone()
            if mapped:
                job_id = int(mapped["id"])
            else:
                self._require_project_account(project_id, account_id, conn)
                legacy = conn.execute(
                    """
                    SELECT id FROM publish_jobs
                    WHERE project_id = ? AND account_id = ? AND action = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (project_id, account_id, action),
                ).fetchone()
                if legacy:
                    job_id = int(legacy["id"])
                else:
                    try:
                        job_id = int(
                            conn.execute(
                                """
                                INSERT INTO publish_jobs (
                                  project_id, account_id, platform, action, status
                                ) VALUES (?, ?, '微信公众号', ?, 'pending')
                                """,
                                (project["id"], account["id"], action),
                            ).lastrowid
                        )
                    except sqlite3.IntegrityError as exc:
                        if "one account_id" in str(exc):
                            raise PublishJobValidationError(
                                "内容项目已绑定其他平台账号"
                            ) from exc
                        raise
                conn.execute(
                    """
                    INSERT INTO publish_job_idempotency (
                      project_id, account_id, action, publish_job_id
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (project_id, account_id, action, job_id),
                )
        return self.get_publish_job(job_id)

    def execute_publish_job(self, job_id: int) -> dict[str, Any]:
        job = self._get_row(job_id)
        attempt_token = self._claim_execution(job, "pending")
        if attempt_token is None:
            return self.get_publish_job(job_id)
        try:
            _, account, payload = self._load_inputs(
                int(job["project_id"]), int(job["account_id"]), str(job["action"])
            )
        except Exception as exc:
            self._persist_failure(job_id, "加载发布输入", exc, attempt_token)
            return self.get_publish_job(job_id)
        return self._execute_claimed(job, account, payload, attempt_token)

    def get_publish_job(self, job_id: int) -> dict[str, Any]:
        return self._public_job(self._get_row(job_id))

    def get_current_publish_job(
        self, project_id: int, account_id: int, action: PublishAction
    ) -> dict[str, Any] | None:
        if action not in {"draft", "publish"}:
            raise PublishJobValidationError("发布动作必须是 draft 或 publish")
        job = self.db.fetch_one(
            """
            SELECT jobs.*
            FROM publish_job_idempotency AS keys
            JOIN publish_jobs AS jobs ON jobs.id = keys.publish_job_id
            WHERE keys.project_id = ? AND keys.account_id = ? AND keys.action = ?
            """,
            (project_id, account_id, action),
        )
        if not job:
            job = self.db.fetch_one(
                """
                SELECT * FROM publish_jobs
                WHERE project_id = ? AND account_id = ? AND action = ?
                ORDER BY id DESC LIMIT 1
                """,
                (project_id, account_id, action),
            )
        return self._public_job(job) if job else None

    def get_project_publish_job(self, project_id: int) -> dict[str, Any] | None:
        job = self.db.fetch_one(
            """
            SELECT * FROM publish_jobs
            WHERE project_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (project_id,),
        )
        return self._public_job(job) if job else None

    def retry_publish_job(self, job_id: int) -> dict[str, Any]:
        job = self._get_row(job_id)
        if job["status"] != "failed":
            raise PublishJobValidationError("只有失败的发布任务可以重试")
        project, account, payload = self._load_inputs(
            int(job["project_id"]), int(job["account_id"]), str(job["action"])
        )
        return self._execute(
            job_id,
            account,
            payload,
            expected_status="failed",
            clear_previous_result=True,
            observed_job=job,
        )

    def fail_stale_in_progress_job(
        self, job_id: int, stale_before: str
    ) -> dict[str, Any]:
        job = self._get_row(job_id)
        if job["status"] not in UNCERTAIN_IN_PROGRESS_STATUSES:
            return self._public_job(job)
        response_json = json.dumps(
            {ATTEMPT_TOKEN_KEY: self._attempt_token(job)}, ensure_ascii=False
        )
        with self.db.connect() as conn:
            conn.execute(
                f"""
                UPDATE publish_jobs
                SET status = 'failed', platform_draft_id = '', platform_publish_id = '',
                    platform_url = '', response_json = ?, requires_confirmation = 0,
                    failure_stage = ?, failure_reason = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status IN ({','.join('?' for _ in UNCERTAIN_IN_PROGRESS_STATUSES)})
                  AND response_json = ?
                  AND updated_at <= ?
                """,
                (
                    response_json,
                    INTERRUPTED_FAILURE_STAGE,
                    INTERRUPTED_FAILURE_REASON,
                    job_id,
                    *UNCERTAIN_IN_PROGRESS_STATUSES,
                    job["response_json"],
                    stale_before,
                ),
            )
            conn.commit()
        return self.get_publish_job(job_id)

    def fail_awaiting_confirmation(
        self,
        job_id: int,
        reason: str = CONFIRMATION_EXPIRED_REASON,
        expected_attempt_token: str | None = None,
    ) -> dict[str, Any]:
        job = self._get_row(job_id)
        if job["status"] != "awaiting_confirmation":
            return self._public_job(job)
        attempt_token = self._attempt_token(job)
        if expected_attempt_token is not None and attempt_token != expected_attempt_token:
            return self._public_job(job)
        replacement_response = self._attempt_response_json(attempt_token)
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'failed', platform_draft_id = '', platform_publish_id = '',
                    platform_url = '', response_json = ?, requires_confirmation = 0,
                    failure_stage = ?, failure_reason = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'awaiting_confirmation' AND response_json = ?
                """,
                (
                    replacement_response,
                    CONFIRMATION_FAILURE_STAGE,
                    reason,
                    job_id,
                    job["response_json"],
                ),
            )
            conn.commit()
        result = self.get_publish_job(job_id)
        if cursor.rowcount == 1 and self.confirmation_expired_callback:
            self.confirmation_expired_callback(result)
        return result

    def resume_publish_job(self, job_id: int) -> dict[str, Any]:
        job = self._get_row(job_id)
        if job["status"] != "awaiting_confirmation":
            raise PublishJobValidationError("只有待人工确认的发布任务可以恢复")
        account = self.account_loader(int(job["account_id"]))
        attempt_token = self._claim_execution(job, "awaiting_confirmation")
        if attempt_token is None:
            return self.get_publish_job(job_id)
        try:
            context = self._job_context(job)
            result = self.publisher.resume(account, context)
            self._persist_result(job_id, str(job["action"]), result, attempt_token)
        except Exception as exc:
            self._persist_failure(job_id, "恢复人工确认", exc, attempt_token)
        return self.get_publish_job(job_id)

    def _execute(
        self,
        job_id: int,
        account: dict[str, Any],
        payload: dict[str, Any],
        expected_status: str,
        clear_previous_result: bool = False,
        observed_job: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        job = dict(observed_job or self._get_row(job_id))
        attempt_token = self._claim_execution(
            job, expected_status, clear_previous_result
        )
        if attempt_token is None:
            return self.get_publish_job(job_id)
        return self._execute_claimed(job, account, payload, attempt_token)

    def _execute_claimed(
        self,
        job: Mapping[str, Any],
        account: dict[str, Any],
        payload: dict[str, Any],
        attempt_token: str,
    ) -> dict[str, Any]:
        job_id = int(job["id"])
        try:
            publish_with_progress = getattr(
                self.publisher, "publish_with_progress", None
            )
            if callable(publish_with_progress):
                result = publish_with_progress(
                    payload,
                    account,
                    str(job["action"]),
                    lambda status: self._persist_progress(
                        job_id, status, attempt_token
                    ),
                )
            else:
                result = self.publisher.publish(payload, account, str(job["action"]))
            self._persist_result(job_id, str(job["action"]), result, attempt_token)
        except Exception as exc:
            stage = "提交平台草稿" if job["action"] == "draft" else "发布文章"
            self._persist_failure(job_id, stage, exc, attempt_token)
        return self.get_publish_job(job_id)

    def _persist_progress(
        self, job_id: int, status: str, attempt_token: str
    ) -> None:
        if status not in {"filling_content", "uploading_assets", "publishing"}:
            raise PublishJobValidationError("发布进度状态无效")
        response_json = self._attempt_response_json(attempt_token)
        with self.db.connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE publish_jobs
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status IN ({','.join('?' for _ in UNCERTAIN_IN_PROGRESS_STATUSES)})
                  AND response_json = ?
                """,
                (
                    status,
                    job_id,
                    *UNCERTAIN_IN_PROGRESS_STATUSES,
                    response_json,
                ),
            )
            conn.commit()
        if cursor.rowcount != 1:
            raise PublishJobValidationError("发布任务已失去执行权，已中止平台操作")

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

    def _require_project_account(
        self, project_id: int, account_id: int, conn: Any
    ) -> None:
        existing = conn.execute(
            "SELECT account_id FROM publish_jobs WHERE project_id = ? ORDER BY id LIMIT 1",
            (project_id,),
        ).fetchone()
        if existing and int(existing["account_id"]) != account_id:
            raise PublishJobValidationError("内容项目已绑定其他平台账号")

    def _claim_execution(
        self,
        observed_job: Mapping[str, Any],
        expected_status: str,
        clear_previous_result: bool = False,
    ) -> str | None:
        job_id = int(observed_job["id"])
        observed_response_json = str(observed_job.get("response_json") or "{}")
        attempt_token = secrets.token_urlsafe(32)
        attempt_response_json = self._attempt_response_json(attempt_token)
        reset = """
            , platform_draft_id = '', platform_publish_id = '', platform_url = ''
        """ if clear_previous_result else ""
        with self.db.connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE publish_jobs
                SET status = 'checking_login', requires_confirmation = 0,
                    response_json = ?, failure_stage = '', failure_reason = '',
                    updated_at = CURRENT_TIMESTAMP
                    {reset}
                WHERE id = ? AND status = ? AND response_json = ?
                """,
                (attempt_response_json, job_id, expected_status, observed_response_json),
            )
            conn.commit()
            return attempt_token if cursor.rowcount == 1 else None

    def _persist_result(
        self, job_id: int, action: str, result: PublishResult, attempt_token: str
    ) -> None:
        response = result.response if isinstance(result.response, Mapping) else {}
        if result.status == "awaiting_confirmation":
            if action != "publish" or not result.requires_confirmation:
                raise RuntimeError("平台人工确认结果与发布动作不匹配")
            self._require_job_context(response)
        elif action == "draft":
            if result.status != "platform_draft":
                raise RuntimeError("平台未返回可验证的草稿成功结果")
            self._require_platform_evidence(
                result.platform_draft_id, result.platform_url, response, "draft"
            )
        else:
            if result.status != "published":
                raise RuntimeError("平台未返回可验证的发布成功结果")
            self._require_platform_evidence(
                result.platform_publish_id, result.platform_url, response, "publish"
            )
        private_response = dict(response)
        private_response[ATTEMPT_TOKEN_KEY] = attempt_token
        response_json = json.dumps(private_response, ensure_ascii=False)
        with self.db.connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE publish_jobs
                SET status = ?, platform_draft_id = ?, platform_publish_id = ?,
                    platform_url = ?, response_json = ?, requires_confirmation = ?,
                    failure_stage = '', failure_reason = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status IN ({','.join('?' for _ in UNCERTAIN_IN_PROGRESS_STATUSES)})
                  AND response_json = ?
                """,
                (
                    result.status,
                    result.platform_draft_id,
                    result.platform_publish_id,
                    result.platform_url,
                    response_json,
                    int(result.requires_confirmation),
                    job_id,
                    *UNCERTAIN_IN_PROGRESS_STATUSES,
                    self._attempt_response_json(attempt_token),
                ),
            )
            conn.commit()
        if cursor.rowcount == 1 and result.status == "awaiting_confirmation":
            self._bind_confirmation_expiry(
                job_id, self._require_job_context(response), attempt_token
            )

    def _bind_confirmation_expiry(
        self, job_id: int, job_context: Mapping[str, Any], attempt_token: str
    ) -> None:
        binder = getattr(self.publisher, "bind_confirmation_expiry", None)
        if not callable(binder):
            return
        try:
            binder(
                job_context,
                lambda: self.fail_awaiting_confirmation(
                    job_id, expected_attempt_token=attempt_token
                ),
            )
        except Exception:
            self.fail_awaiting_confirmation(
                job_id,
                reason="运营者确认窗口无法继续跟踪，请重试发布任务",
                expected_attempt_token=attempt_token,
            )

    def _persist_failure(
        self, job_id: int, stage: str, exc: Exception, attempt_token: str
    ) -> None:
        job = self._get_row(job_id)
        reason = str(exc).strip() or exc.__class__.__name__
        reason = self._redact_sensitive_text(job, reason)
        response_json = self._attempt_response_json(attempt_token)
        with self.db.connect() as conn:
            conn.execute(
                f"""
                UPDATE publish_jobs
                SET status = 'failed', platform_draft_id = '', platform_publish_id = '',
                    platform_url = '', response_json = ?, requires_confirmation = 0,
                    failure_stage = ?, failure_reason = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status IN ({','.join('?' for _ in UNCERTAIN_IN_PROGRESS_STATUSES)})
                  AND response_json = ?
                """,
                (
                    response_json,
                    stage,
                    reason,
                    job_id,
                    *UNCERTAIN_IN_PROGRESS_STATUSES,
                    response_json,
                ),
            )
            conn.commit()

    def _get_row(self, job_id: int) -> dict[str, Any]:
        job = self.db.fetch_one("SELECT * FROM publish_jobs WHERE id = ?", (job_id,))
        if not job:
            raise LookupError("发布任务不存在")
        return job

    @staticmethod
    def _require_platform_evidence(
        platform_id: str, platform_url: str, response: Mapping[str, Any], action: str
    ) -> None:
        label = "草稿" if action == "draft" else "发布"
        parsed = urlparse(platform_url)
        if not platform_id or parsed.scheme != "https" or parsed.hostname != "mp.weixin.qq.com":
            raise RuntimeError(f"平台未返回可验证的{label}回执")
        expected_event = "draft_created" if action == "draft" else "article_published"
        expected_corroboration = (
            {"draft_saved", "draft_api_response"}
            if action == "draft"
            else {"publish_success", "publish_api_response"}
        )
        if (
            response.get("source") != "wechat_platform"
            or response.get("event") != expected_event
            or str(response.get("receipt_id") or "") != platform_id
            or response.get("corroboration") not in expected_corroboration
        ):
            raise RuntimeError(f"平台返回的{label}证据字段无效或与动作不匹配")

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

    @staticmethod
    def _attempt_response_json(attempt_token: str) -> str:
        return json.dumps({ATTEMPT_TOKEN_KEY: attempt_token}, ensure_ascii=False)

    @staticmethod
    def _attempt_token(job: Mapping[str, Any]) -> str:
        try:
            response = json.loads(str(job.get("response_json") or "{}"))
        except json.JSONDecodeError:
            return ""
        token = response.get(ATTEMPT_TOKEN_KEY) if isinstance(response, Mapping) else ""
        return token if isinstance(token, str) else ""

    def _public_job(self, job: Mapping[str, Any]) -> dict[str, Any]:
        try:
            response = json.loads(str(job.get("response_json") or "{}"))
        except json.JSONDecodeError:
            response = {}
        resource = {key: job[key] for key in PUBLIC_JOB_FIELDS if key in job}
        resource["requires_confirmation"] = bool(resource["requires_confirmation"])
        for key, value in resource.items():
            if isinstance(value, str):
                resource[key] = self._redact_sensitive_text(job, value)
        resource["response"] = self._project_public_response(job, response)
        return resource

    def _redact_sensitive_text(self, job: Mapping[str, Any], value: str) -> str:
        account = self.db.fetch_one(
            "SELECT profile_path FROM platform_accounts WHERE id = ?",
            (int(job["account_id"]),),
        ) or {}
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
                value = value.replace(sensitive, REDACTED_BROWSER_CONTEXT)
        value = AUTHORIZATION_PATTERN.sub(REDACTED_BROWSER_CONTEXT, value)
        value = BEARER_PATTERN.sub(REDACTED_BROWSER_CONTEXT, value)
        value = COOKIE_PATTERN.sub(REDACTED_BROWSER_CONTEXT, value)
        value = URL_PATTERN.sub(self._sanitize_url, value)
        value = SENSITIVE_ASSIGNMENT_PATTERN.sub(REDACTED_BROWSER_CONTEXT, value)
        return LOCAL_DEBUG_ADDRESS_PATTERN.sub(REDACTED_BROWSER_CONTEXT, value)

    @staticmethod
    def _sanitize_url(match: re.Match[str]) -> str:
        value = match.group(0)
        parsed = urlsplit(value)
        if parsed.scheme.lower() in {"ws", "wss"}:
            return REDACTED_BROWSER_CONTEXT
        hostname = parsed.hostname or ""
        netloc = f"[{hostname}]" if ":" in hostname else hostname
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))

    @classmethod
    def _string_values(cls, value: Any) -> list[str]:
        if isinstance(value, Mapping):
            return [item for nested in value.values() for item in cls._string_values(nested)]
        if isinstance(value, list):
            return [item for nested in value for item in cls._string_values(nested)]
        return [value] if isinstance(value, str) else []

    def _project_public_response(
        self, job: Mapping[str, Any], response: Any
    ) -> dict[str, Any]:
        if not isinstance(response, Mapping):
            return {}
        projected = {
            key: self._redact_sensitive_text(job, value) if isinstance(value, str) else value
            for key in PUBLIC_RESPONSE_FIELDS
            if key in response
            and (
                (value := response[key]) is None
                or isinstance(value, (str, int, float, bool))
            )
        }
        for container in PUBLIC_RECEIPT_CONTAINERS:
            receipt = response.get(container)
            if not isinstance(receipt, Mapping):
                continue
            projected[container] = {
                key: self._redact_sensitive_text(job, value)
                if isinstance(value, str)
                else value
                for key in PUBLIC_RECEIPT_FIELDS
                if key in receipt
                and (
                    (value := receipt[key]) is None
                    or isinstance(value, (str, int, float, bool))
                )
            }
        return projected


__all__ = ["PublishCoordinator", "PublishJobValidationError"]
