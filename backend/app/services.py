import inspect
import json
import re
import threading
import unicodedata
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .ai import OpenAICompatibleClient, TopicRecommendation
from .database import Database
from .publishing.coordinator import (
    UNCERTAIN_IN_PROGRESS_STATUSES,
    PublishCoordinator,
    PublishJobValidationError,
)
from .publishing.router import PlatformPublisherRouter


STAGE_LABELS = {
    "title": ("标题已确认，待生成文章", "article"),
    "article": ("文章已完成，待生成图片", "images"),
    "images": ("图片已完成，待排版", "layout"),
    "layout": ("排版已完成，待发布", "layout"),
    "publish": ("待人工发布", "layout"),
}

STAGE_ORDER = ["title", "article", "images", "layout", "publish"]
TASK_TYPE_WECHAT = "微信公众号图文生成"
TASK_TYPE_WECHAT_LEGACY = "公众号文章创作"
TASK_TYPE_XIAOHONGSHU = "小红书图文生成"
TASK_TYPE_XIAOHONGSHU_RESERVED = "小红书图文生成（预留）"
TASK_TIME_FORMAT = "%Y-%m-%d %H:%M"
PUBLISH_INTERRUPTION_TIMEOUT = timedelta(minutes=15)
CONFIRMATION_HANDLE_TIMEOUT = timedelta(minutes=5)
TERMINAL_PUBLISH_JOB_STATUSES = ("platform_draft", "published", "failed")
WAITING_TASK_RECOVERY_JOB_STATUSES = (
    *UNCERTAIN_IN_PROGRESS_STATUSES,
    *TERMINAL_PUBLISH_JOB_STATUSES,
)
PLATFORMS = ["微信公众号", "视频号", "小红书", "抖音"]
XIAOHONGSHU_MAX_NOTE_LENGTH = 1000
MARKDOWN_ASSET_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(asset:(\d+)\)")
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
ACCOUNT_SYNC_ERROR_SECRET_RE = re.compile(
    r"(token|cookie|credentials|debug_port|authorization|bearer)=\S+",
    re.IGNORECASE,
)
ACCOUNT_SYNC_ERROR_LOCAL_URL_RE = re.compile(r"https?://127\.0\.0\.1:\d+/\S*")
ACCOUNT_SYNC_ERROR_PRIVATE_PATH_RE = re.compile(r"/(?:private/)?[^\s]*browser-profile[^\s]*")
REDACTED_ACCOUNT_DIAGNOSTIC = "[已隐藏账号诊断信息]"
XIAOHONGSHU_SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
PLATFORM_ALIASES = {
    "公众号": "微信公众号",
    "微信公众平台": "微信公众号",
    "微信公众号": "微信公众号",
    "视频号": "视频号",
    "小红书": "小红书",
    "抖音": "抖音",
}
PUBLIC_ACCOUNT_FIELDS = (
    "id",
    "platform",
    "name",
    "external_id",
    "owner",
    "tag",
    "auth_status",
    "login_status",
    "monitor_status",
    "sync_status",
    "sync_error",
    "role",
    "audience",
    "style",
    "taboo",
    "preference",
    "auth_method",
    "last_sync_at",
    "created_at",
    "updated_at",
)


class NotFoundError(RuntimeError):
    pass


class TaskConflictError(RuntimeError):
    pass


class AccountConflictError(RuntimeError):
    pass


class ContentService:
    def __init__(
        self,
        db: Database,
        ai_client: Any | None = None,
        upload_dir: str | Path = "backend/uploads",
        browser_auth_manager: Any | None = None,
        platform_publisher: Any | None = None,
    ):
        self.db = db
        self.ai_client = ai_client or OpenAICompatibleClient()
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.browser_auth_manager = browser_auth_manager
        self.platform_publisher = platform_publisher or PlatformPublisherRouter()
        self.publish_coordinator = PublishCoordinator(
            db=self.db,
            publisher=self.platform_publisher,
            project_loader=self.get_project,
            account_loader=self.get_account,
            payload_builder=self._build_publish_payload,
            confirmation_expired_callback=self._synchronize_linked_waiting_tasks,
        )
        self._account_browser_locks: dict[int, threading.Lock] = {}
        self._account_browser_locks_guard = threading.Lock()
        self._fail_unrecoverable_confirmations_after_restart()

    def _fail_unrecoverable_confirmations_after_restart(self) -> None:
        jobs = self.db.fetch_all(
            "SELECT id FROM publish_jobs WHERE status = 'awaiting_confirmation'"
        )
        for job in jobs:
            self.publish_coordinator.fail_awaiting_confirmation(
                int(job["id"]),
                reason="服务重启后原运营者确认窗口已失效，请重试发布任务",
            )

    def _account_browser_lock(self, account_id: int) -> threading.Lock:
        with self._account_browser_locks_guard:
            lock = self._account_browser_locks.get(account_id)
            if lock is None:
                lock = threading.Lock()
                self._account_browser_locks[account_id] = lock
            return lock

    @contextmanager
    def _account_browser_scope(self, account_id: int) -> Iterator[None]:
        lock = self._account_browser_lock(account_id)
        if not lock.acquire(blocking=False):
            raise RuntimeError("该账号正在执行登录、同步或发布，请稍后再试")
        try:
            yield
        finally:
            lock.release()

    @staticmethod
    def _login_status_from_sync_error(error: str) -> str:
        lowered = error.lower()
        expired_markers = (
            "登录态已失效",
            "重新扫码",
            "未登录",
            "登录失效",
            "扫码登录",
            "redirectreason=401",
            "unauthorized",
        )
        return "离线" if any(marker in lowered or marker in error for marker in expired_markers) else "在线"

    def recover_stale_awaiting_confirmations(
        self, now: datetime | None = None
    ) -> dict[str, int]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is not None:
            current = current.astimezone(timezone.utc).replace(tzinfo=None)
        stale_before = (current - CONFIRMATION_HANDLE_TIMEOUT).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        jobs = self.db.fetch_all(
            """
            SELECT id FROM publish_jobs
            WHERE status = 'awaiting_confirmation' AND updated_at <= ?
            ORDER BY id
            """,
            (stale_before,),
        )
        failed = 0
        for job in jobs:
            result = self.publish_coordinator.fail_awaiting_confirmation(int(job["id"]))
            failed += int(result["status"] == "failed")
        return {"failed": failed}

    def recover_publish_jobs(
        self, now: datetime | None = None
    ) -> dict[str, int]:
        pending_jobs = self.db.fetch_all(
            """
            SELECT jobs.id
            FROM publish_jobs AS jobs
            WHERE jobs.status = 'pending'
              AND NOT EXISTS (
                  SELECT 1
                  FROM scheduled_tasks AS tasks
                  JOIN task_runs AS runs
                    ON runs.task_id = tasks.id
                   AND runs.generation = tasks.generation
                  WHERE tasks.status = '执行中'
                    AND runs.status = '执行中'
                    AND tasks.content_project_id = jobs.project_id
                    AND tasks.account_id = jobs.account_id
                    AND tasks.publish_action = jobs.action
                    AND tasks.publish_job_id IS NULL
                    AND runs.publish_job_id IS NULL
              )
            ORDER BY jobs.id
            """
        )
        recovered = 0
        for job in pending_jobs:
            try:
                result = self.execute_publish_job(int(job["id"]))
                recovered += int(result["status"] != "pending")
            except Exception:
                continue
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.astimezone()
        stale_before = (
            current.astimezone(timezone.utc) - PUBLISH_INTERRUPTION_TIMEOUT
        ).strftime("%Y-%m-%d %H:%M:%S")
        active_jobs = self.db.fetch_all(
            f"""
            SELECT id FROM publish_jobs
            WHERE status IN ({','.join('?' for _ in UNCERTAIN_IN_PROGRESS_STATUSES)})
            ORDER BY id
            """,
            UNCERTAIN_IN_PROGRESS_STATUSES,
        )
        failed = 0
        for job in active_jobs:
            try:
                result = self.publish_coordinator.fail_stale_in_progress_job(
                    int(job["id"]), stale_before
                )
                failed += int(result["status"] == "failed")
            except Exception:
                continue
        return {"recovered": recovered, "failed": failed}

    def recommend_topics(self, user_prompt: str) -> list[TopicRecommendation]:
        return self.ai_client.recommend_topics(user_prompt)

    def create_project(self, title: str, title_direction: str = "", platform: str = "微信公众号") -> dict[str, Any]:
        project_id = self.db.execute(
            """
            INSERT INTO content_projects (title, title_direction, platform, current_stage)
            VALUES (?, ?, ?, 'article')
            """,
            (title, title_direction, platform),
        )
        project = self.get_project(project_id)
        self.autosave(project_id, stage="title", restore_stage="article")
        return project

    def get_project(self, project_id: int) -> dict[str, Any]:
        project = self.db.fetch_one("SELECT * FROM content_projects WHERE id = ?", (project_id,))
        if not project:
            raise NotFoundError("内容项目不存在")
        project["article_blocks"] = self.get_article_blocks(project_id)
        project["media_assets"] = self.get_media_assets(project_id)
        project["layout_blocks"] = self.get_layout_blocks(project_id)
        return project

    def generate_article(self, project_id: int) -> dict[str, Any]:
        project = self.get_project(project_id)
        platform = str(project.get("platform") or "微信公众号")
        direction = self._article_direction_for_platform(project["title_direction"], platform)
        blocks = self._generate_article_blocks(project["title"], direction, platform)
        if not blocks:
            raise RuntimeError("模型未返回文章段落")
        if platform == "小红书" and self._article_blocks_text_length(blocks) > XIAOHONGSHU_MAX_NOTE_LENGTH:
            blocks = self._rewrite_xiaohongshu_article_under_limit(project, blocks)
            if self._article_blocks_text_length(blocks) > XIAOHONGSHU_MAX_NOTE_LENGTH:
                raise RuntimeError("小红书文章正文超过1000字，请缩短后重试")
        self.db.execute("DELETE FROM article_blocks WHERE project_id = ?", (project_id,))
        params = [
            (
                project_id,
                "intro" if index == 0 else "body",
                "引言" if index == 0 else "正文",
                block["content"],
                index + 1,
            )
            for index, block in enumerate(blocks)
        ]
        self.db.execute_many(
            """
            INSERT INTO article_blocks (project_id, block_type, label, content, sort_order)
            VALUES (?, ?, ?, ?, ?)
            """,
            params,
        )
        self._set_stage(project_id, "article")
        self.autosave(project_id, stage="article", restore_stage="images")
        return {"project": self.get_project(project_id), "blocks": self.get_article_blocks(project_id)}

    def _generate_article_blocks(
        self, title: str, direction: str, platform: str
    ) -> list[dict[str, Any]]:
        generator = self.ai_client.generate_article
        try:
            signature = inspect.signature(generator)
        except (TypeError, ValueError):
            return generator(title, direction)
        if "platform" in signature.parameters:
            return generator(title, direction, platform=platform)
        return generator(title, direction)

    @staticmethod
    def _article_direction_for_platform(direction: str, platform: str) -> str:
        base = str(direction or "").strip()
        if platform != "小红书":
            return base
        xhs_requirements = (
            "平台：小红书。硬性条件：文章正文不可超过1000字。"
            "请使用小红书笔记表达，短段落、口语化、经验分享感强；"
            "不要输出 Markdown 标题层级，不要输出“以下是正文”等说明性文字。"
        )
        return f"{base}\n{xhs_requirements}" if base else xhs_requirements

    def _rewrite_xiaohongshu_article_under_limit(
        self, project: dict[str, Any], blocks: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        original = "\n\n".join(
            str(block.get("content") or "").strip()
            for block in blocks
            if str(block.get("content") or "").strip()
        )
        direction = (
            f"{self._article_direction_for_platform(project['title_direction'], '小红书')}\n"
            "请对以下正文进行压缩改写，保留核心信息和行动引导，"
            "压缩改写后的正文总字数不可超过1000字，这是硬性条件。\n"
            f"原正文：{original}"
        )
        return self._generate_article_blocks(project["title"], direction, "小红书")

    @staticmethod
    def _article_blocks_text_length(blocks: list[dict[str, Any]]) -> int:
        return sum(len(str(block.get("content") or "").strip()) for block in blocks)

    def save_article(self, project_id: int, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        self.get_project(project_id)
        self.db.execute("DELETE FROM article_blocks WHERE project_id = ?", (project_id,))
        params = [
            (
                project_id,
                "intro" if index == 0 else "body",
                "引言" if index == 0 else "正文",
                str(block.get("content", "")),
                int(block.get("sort_order", index + 1)),
            )
            for index, block in enumerate(blocks)
        ]
        if params:
            self.db.execute_many(
                """
                INSERT INTO article_blocks (project_id, block_type, label, content, sort_order)
                VALUES (?, ?, ?, ?, ?)
                """,
                params,
            )
        self._set_stage(project_id, "article")
        self.autosave(project_id, stage="article", restore_stage="images")
        return {"project": self.get_project(project_id), "blocks": self.get_article_blocks(project_id)}

    def generate_images(self, project_id: int, asset_type: str | None = None) -> dict[str, Any]:
        project = self.get_project(project_id)
        blocks = project["article_blocks"]
        if not blocks:
            raise RuntimeError("请先生成文章，再生成图片")
        if asset_type and asset_type not in {"cover", "inline"}:
            raise RuntimeError("图片类型不支持")

        body = "\n".join(block["content"] for block in blocks)
        prompts = [
            (
                "cover",
                f"公众号封面图，主题：{project['title']}。内容背景：{body[:500]}。风格：清晰、有传播感、适合公众号首图。",
            ),
            (
                "inline",
                f"公众号正文配图，解释文章核心观点：{project['title']}。内容背景：{body[:500]}。风格：清晰、可读、能辅助理解正文。",
            ),
        ]
        if asset_type:
            prompts = [item for item in prompts if item[0] == asset_type]
        assets = []
        for asset_type, prompt in prompts:
            started_at = datetime.now()
            self._trace_image_generation(
                project["id"],
                asset_type,
                "image_generate_start",
                prompt_length=len(prompt),
            )
            try:
                generated = self.ai_client.generate_image(prompt, asset_type)
            except Exception as exc:
                self._trace_image_generation(
                    project["id"],
                    asset_type,
                    "image_generate_failed",
                    prompt_length=len(prompt),
                    duration_ms=self._elapsed_ms(started_at),
                    error=self._redact_account_diagnostic(self._root_exception_message(exc)),
                )
                raise
            relative_path = Path("uploads") / generated.filename
            file_path = self.upload_dir / generated.filename
            file_path.write_bytes(generated.content)
            url = f"/uploads/{generated.filename}"
            if asset_type == "cover":
                self.db.execute(
                    "UPDATE media_assets SET selected = 0 WHERE project_id = ? AND asset_type = 'cover'",
                    (project_id,),
                )
            asset_id = self.db.execute(
                """
                INSERT INTO media_assets (project_id, asset_type, url, local_path, prompt, selected)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (project_id, asset_type, url, str(relative_path), prompt),
            )
            asset = self.db.fetch_one("SELECT * FROM media_assets WHERE id = ?", (asset_id,))
            if asset:
                assets.append(asset)
            self._trace_image_generation(
                project["id"],
                asset_type,
                "image_generate_success",
                duration_ms=self._elapsed_ms(started_at),
                filename=generated.filename,
                content_type=generated.content_type,
                byte_count=len(generated.content),
            )

        self._set_stage(project_id, "images")
        self.autosave(project_id, stage="images", restore_stage="layout")
        return {
            "project": self.get_project(project_id),
            "assets": self.get_media_assets(project_id),
            "generated_assets": assets,
        }

    def _trace_image_generation(
        self, project_id: int, asset_type: str, event: str, **fields: Any
    ) -> None:
        log_dir = self.upload_dir.parent / "logs" / "tasks"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
                "event": event,
                "project_id": int(project_id),
                "asset_type": asset_type,
                **self._safe_task_log_fields(fields),
            }
            log_path = log_dir / f"image-generation-{datetime.now().strftime('%Y%m%d')}.jsonl"
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            return

    @classmethod
    def _safe_task_log_fields(cls, fields: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key, value in fields.items():
            if isinstance(value, (int, float, bool)) or value is None:
                safe[key] = value
            elif isinstance(value, Path):
                safe[key] = value.name
            else:
                safe[key] = cls._redact_account_diagnostic(str(value))
        return safe

    @staticmethod
    def _elapsed_ms(started_at: datetime) -> int:
        return max(0, int((datetime.now() - started_at).total_seconds() * 1000))

    def save_layout(self, project_id: int, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        self.get_project(project_id)
        self.db.execute("DELETE FROM layout_blocks WHERE project_id = ?", (project_id,))
        layout_title = self._layout_title_from_blocks(blocks)
        if layout_title:
            self.db.execute(
                "UPDATE content_projects SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (layout_title, project_id),
            )
        params = [
            (
                project_id,
                str(block.get("section_type", "body")),
                str(block.get("content", "")),
                block.get("media_asset_id"),
                int(block.get("sort_order", index + 1)),
            )
            for index, block in enumerate(blocks)
        ]
        if params:
            self.db.execute_many(
                """
                INSERT INTO layout_blocks (project_id, section_type, content, media_asset_id, sort_order)
                VALUES (?, ?, ?, ?, ?)
                """,
                params,
            )
        self._set_stage(project_id, "layout")
        self.autosave(project_id, stage="layout", restore_stage="layout")
        return {"project": self.get_project(project_id), "layout_blocks": self.get_layout_blocks(project_id)}

    def select_media_assets(self, project_id: int, selected_ids: list[int]) -> dict[str, Any]:
        self.get_project(project_id)
        if selected_ids:
            placeholders = ",".join("?" for _ in selected_ids)
            selected_assets = self.db.fetch_all(
                f"""
                SELECT id, asset_type
                FROM media_assets
                WHERE project_id = ? AND id IN ({placeholders})
                """,
                (project_id, *selected_ids),
            )
            if sum(1 for asset in selected_assets if asset["asset_type"] == "cover") > 1:
                raise RuntimeError("只能选择一张封面图")
        self.db.execute("UPDATE media_assets SET selected = 0 WHERE project_id = ?", (project_id,))
        for asset_id in selected_ids:
            self.db.execute(
                "UPDATE media_assets SET selected = 1 WHERE project_id = ? AND id = ?",
                (project_id, asset_id),
            )
        self.autosave(project_id, stage="images", restore_stage="layout")
        return {"project": self.get_project(project_id), "assets": self.get_media_assets(project_id)}

    def delete_media_asset(self, project_id: int, asset_id: int) -> dict[str, Any]:
        self.get_project(project_id)
        asset = self.db.fetch_one(
            "SELECT * FROM media_assets WHERE project_id = ? AND id = ?",
            (project_id, asset_id),
        )
        if not asset:
            raise NotFoundError("图片不存在")

        file_path = self._media_file_path(asset)
        if file_path.exists() and file_path.is_file():
            file_path.unlink()

        self.db.execute(
            "UPDATE layout_blocks SET media_asset_id = NULL WHERE project_id = ? AND media_asset_id = ?",
            (project_id, asset_id),
        )
        self.db.execute(
            "DELETE FROM media_assets WHERE project_id = ? AND id = ?",
            (project_id, asset_id),
        )
        self.autosave(project_id, stage="images", restore_stage="layout")
        return {
            "deleted": True,
            "project": self.get_project(project_id),
            "assets": self.get_media_assets(project_id),
        }

    def set_project_stage(self, project_id: int, stage: str) -> dict[str, Any]:
        if stage not in STAGE_ORDER:
            raise RuntimeError("内容阶段不支持")
        self.get_project(project_id)
        self._set_stage(project_id, stage)
        draft = self.autosave(project_id, stage=stage, restore_stage=stage)
        project = self.get_project(project_id)
        return {
            "project": project,
            "article_blocks": project["article_blocks"],
            "media_assets": project["media_assets"],
            "layout_blocks": project["layout_blocks"],
            "draft": draft,
        }

    def autosave(self, project_id: int, stage: str, restore_stage: str | None = None) -> dict[str, Any]:
        project = self.get_project(project_id) if stage != "title" else self._get_project_only(project_id)
        stage_label, default_restore = STAGE_LABELS.get(stage, ("标题已确认", "article"))
        restore = restore_stage or default_restore
        snapshot = {
            "project": project,
            "article_blocks": self.get_article_blocks(project_id),
            "media_assets": self.get_media_assets(project_id),
            "layout_blocks": self.get_layout_blocks(project_id),
        }
        existing = self.db.fetch_one("SELECT id FROM draft_snapshots WHERE project_id = ?", (project_id,))
        if existing:
            self.db.execute(
                """
                UPDATE draft_snapshots
                SET title = ?, platform = ?, stage = ?, restore_stage = ?, snapshot_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE project_id = ?
                """,
                (
                    project["title"],
                    project["platform"],
                    stage_label,
                    restore,
                    json.dumps(snapshot, ensure_ascii=False),
                    project_id,
                ),
            )
        else:
            self.db.execute(
                """
                INSERT INTO draft_snapshots (project_id, title, platform, stage, restore_stage, snapshot_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    project["title"],
                    project["platform"],
                    stage_label,
                    restore,
                    json.dumps(snapshot, ensure_ascii=False),
                ),
            )
        draft = self.db.fetch_one(
            """
            SELECT id, project_id, title, platform, stage, restore_stage, snapshot_json,
                   datetime(updated_at, 'localtime') AS updated_at
            FROM draft_snapshots
            WHERE project_id = ?
            """,
            (project_id,),
        )
        return draft or {}

    def list_drafts(self, start_date: str = "", end_date: str = "") -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if start_date:
            clauses.append("date(datetime(updated_at, 'localtime')) >= date(?)")
            params.append(start_date)
        if end_date:
            clauses.append("date(datetime(updated_at, 'localtime')) <= date(?)")
            params.append(end_date)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.db.fetch_all(
            f"""
            SELECT id, project_id, title, platform, stage, restore_stage,
                   datetime(updated_at, 'localtime') AS updated_at
            FROM draft_snapshots
            {where}
            ORDER BY updated_at DESC, id DESC
            """,
            tuple(params),
        )
    def get_draft(self, draft_id: int) -> dict[str, Any]:
        draft = self.db.fetch_one(
            """
            SELECT id, project_id, title, platform, stage, restore_stage, snapshot_json,
                   datetime(updated_at, 'localtime') AS updated_at
            FROM draft_snapshots
            WHERE id = ?
            """,
            (draft_id,),
        )
        if not draft:
            raise NotFoundError("草稿不存在")
        draft["snapshot"] = json.loads(draft.get("snapshot_json") or "{}")
        return draft

    def delete_draft(self, draft_id: int) -> dict[str, Any]:
        draft = self.db.fetch_one("SELECT id FROM draft_snapshots WHERE id = ?", (draft_id,))
        if not draft:
            raise NotFoundError("草稿不存在")
        self.db.execute("DELETE FROM draft_snapshots WHERE id = ?", (draft_id,))
        return {"deleted": True, "id": draft_id}

    def start_account_auth(
        self,
        platform: str,
        account_name: str = "",
        account_id: int | None = None,
    ) -> dict[str, Any]:
        normalized_platform = _normalize_platform(platform)
        if not self.browser_auth_manager:
            raise RuntimeError("真实浏览器登录服务未配置")
        target_account: dict[str, Any] | None = None
        if account_id is not None:
            target_account = self.get_account(int(account_id))
            if target_account["platform"] != normalized_platform:
                raise RuntimeError("重新登录账号与所选平台不一致")
            account_name = account_name or str(target_account.get("name") or "")
        completion = (
            (lambda result: self._complete_account_auth(result, int(account_id)))
            if account_id is not None
            else self._complete_account_auth
        )
        return self.browser_auth_manager.start(
            normalized_platform,
            account_name,
            completion,
        )

    def get_account_auth_status(self, session_id: str) -> dict[str, Any]:
        if not self.browser_auth_manager:
            raise RuntimeError("真实浏览器登录服务未配置")
        return self.browser_auth_manager.get_status(session_id)

    def cancel_account_auth(self, session_id: str) -> dict[str, Any]:
        if not self.browser_auth_manager:
            raise RuntimeError("真实浏览器登录服务未配置")
        return self.browser_auth_manager.cancel(session_id)

    def _complete_account_auth(
        self,
        result: dict[str, Any],
        expected_account_id: int | None = None,
    ) -> dict[str, Any]:
        normalized_platform = _normalize_platform(result["platform"])
        name = str(result.get("account_name") or f"{normalized_platform}账号").strip()
        external_id = str(result.get("external_id") or "").strip()
        profile_path = str(result.get("profile_path") or "")
        storage_state_path = str(result.get("storage_state_path") or "")
        login_url = str(result.get("login_url") or "")
        publish_preflight_verified = bool(result.get("publish_preflight_verified"))
        sync_data = result.get("sync_data")
        sync_error = str(result.get("sync_error") or "")
        if normalized_platform == "微信公众号" and not external_id:
            raise RuntimeError("微信公众号授权缺少稳定账号 ID，请重新扫码")
        if normalized_platform == "小红书" and self._xiaohongshu_login_url_expired(login_url):
            raise RuntimeError("小红书发布页登录态未通过，请重新扫码登录")
        if normalized_platform == "小红书" and not publish_preflight_verified:
            self._verify_xiaohongshu_publish_login(
                {
                    "platform": normalized_platform,
                    "name": name,
                    "auth_status": "已授权",
                    "profile_path": profile_path,
                }
            )
        existing = None
        if expected_account_id is not None:
            existing = self.db.fetch_one(
                "SELECT id FROM platform_accounts WHERE id = ? AND platform = ?",
                (expected_account_id, normalized_platform),
            )
            if not existing:
                raise RuntimeError("重新登录账号不存在或平台不一致")
        if not existing and external_id:
            existing = self.db.fetch_one(
                "SELECT id FROM platform_accounts WHERE platform = ? AND external_id = ?",
                (normalized_platform, external_id),
            )
        if not existing and not external_id and profile_path:
            existing = self.db.fetch_one(
                "SELECT id FROM platform_accounts WHERE profile_path = ?",
                (profile_path,),
            )
        if not existing and not external_id and "信息待完善" not in name:
            existing = self.db.fetch_one(
                "SELECT id FROM platform_accounts WHERE platform = ? AND name = ?",
                (normalized_platform, name),
            )
        if existing:
            self.db.execute(
                """
                UPDATE platform_accounts
                SET name = ?, external_id = ?, auth_status = '已授权', monitor_status = '监测中', sync_status = '待同步',
                    sync_error = '', login_status = '在线',
                    auth_method = '真实浏览器扫码', profile_path = ?,
                    storage_state_path = ?, storage_state_updated_at = CASE WHEN ? != '' THEN CURRENT_TIMESTAMP ELSE storage_state_updated_at END,
                    login_url = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    name,
                    external_id,
                    profile_path,
                    storage_state_path,
                    storage_state_path,
                    login_url,
                    existing["id"],
                ),
            )
            account_id = int(existing["id"])
        else:
            account_id = self.db.execute(
                """
                INSERT INTO platform_accounts (
                  platform, name, external_id, owner, tag, auth_status, login_status, monitor_status, sync_status,
                  role, audience, style, taboo, preference, auth_method, profile_path,
                  storage_state_path, storage_state_updated_at, login_url
                )
                VALUES (?, ?, ?, '未分配', '', '已授权', '在线', '监测中', '待同步', ?, ?, ?, ?, ?,
                        '真实浏览器扫码', ?, ?, CASE WHEN ? != '' THEN CURRENT_TIMESTAMP ELSE '' END, ?)
                """,
                (
                    normalized_platform,
                    name,
                    external_id,
                    _default_role(normalized_platform),
                    _default_audience(normalized_platform),
                    _default_style(normalized_platform),
                    "避免夸大效果，避免承诺平台收益",
                    "优先使用场景化标题和可验证数据",
                    profile_path,
                    storage_state_path,
                    storage_state_path,
                    login_url,
                ),
            )
        if isinstance(sync_data, dict):
            try:
                self._persist_account_sync(account_id, sync_data)
            except RuntimeError as exc:
                if normalized_platform != "小红书":
                    raise
                self.db.execute(
                    "UPDATE platform_accounts SET login_status = '在线', sync_status = '同步失败', sync_error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (str(exc) or "平台数据同步失败", account_id),
                )
        elif sync_error:
            self.db.execute(
                "UPDATE platform_accounts SET login_status = '在线', sync_status = '同步失败', sync_error = ? WHERE id = ?",
                (sync_error, account_id),
            )
        return self.get_public_account(account_id)

    @staticmethod
    def _xiaohongshu_login_url_expired(login_url: str) -> bool:
        parsed = urllib.parse.urlparse(login_url)
        if parsed.netloc != "creator.xiaohongshu.com":
            return False
        query = urllib.parse.parse_qs(parsed.query)
        return parsed.path.startswith("/login") or query.get("redirectReason") == ["401"]

    def _verify_xiaohongshu_publish_login(self, account: dict[str, Any]) -> None:
        verifier = getattr(self.platform_publisher, "verify_login", None)
        if not callable(verifier):
            return
        try:
            verifier(account)
        except Exception as exc:
            raise RuntimeError("小红书发布页登录态未通过，请重新扫码登录") from exc

    def list_accounts(
        self,
        platform: str = "",
        auth_status: str = "",
        login_status: str = "",
        q: str = "",
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if platform and platform != "全部平台":
            clauses.append("platform = ?")
            params.append(_normalize_platform(platform))
        if auth_status and auth_status != "全部授权状态":
            clauses.append("auth_status = ?")
            params.append(auth_status)
        if login_status and login_status != "全部登录状态":
            clauses.append("login_status = ?")
            params.append(login_status)
        if q:
            clauses.append("(name LIKE ? OR tag LIKE ? OR owner LIKE ?)")
            keyword = f"%{q}%"
            params.extend([keyword, keyword, keyword])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        accounts = self.db.fetch_all(
            f"""
            SELECT id, platform, name, external_id, owner, tag, auth_status, login_status, monitor_status, sync_status, sync_error,
                   role, audience, style, taboo, preference, auth_method, qr_url, profile_path,
                   storage_state_path, storage_state_updated_at, login_url, last_sync_at, created_at, updated_at
            FROM platform_accounts
            {where}
            ORDER BY updated_at DESC, id DESC
            """,
            tuple(params),
        )
        return [self._public_account(account) for account in accounts]

    def get_account(self, account_id: int) -> dict[str, Any]:
        account = self.db.fetch_one(
            """
            SELECT id, platform, name, external_id, owner, tag, auth_status, login_status, monitor_status, sync_status, sync_error,
                   role, audience, style, taboo, preference, auth_method, qr_url, profile_path,
                   storage_state_path, storage_state_updated_at, login_url, last_sync_at, created_at, updated_at
            FROM platform_accounts
            WHERE id = ?
            """,
            (account_id,),
        )
        if not account:
            raise NotFoundError("账号不存在")
        return account

    def get_public_account(self, account_id: int) -> dict[str, Any]:
        return self._public_account(self.get_account(account_id))

    @staticmethod
    def _public_account(account: dict[str, Any]) -> dict[str, Any]:
        resource = {field: account[field] for field in PUBLIC_ACCOUNT_FIELDS if field in account}
        if isinstance(resource.get("sync_error"), str):
            resource["sync_error"] = ContentService._redact_account_diagnostic(
                str(resource["sync_error"])
            )
        resource["has_storage_state"] = bool(str(account.get("storage_state_path") or "").strip())
        if account.get("storage_state_updated_at"):
            resource["storage_state_updated_at"] = account["storage_state_updated_at"]
        return resource

    @staticmethod
    def _redact_account_diagnostic(value: str) -> str:
        value = ACCOUNT_SYNC_ERROR_LOCAL_URL_RE.sub(REDACTED_ACCOUNT_DIAGNOSTIC, value)
        value = ACCOUNT_SYNC_ERROR_PRIVATE_PATH_RE.sub(REDACTED_ACCOUNT_DIAGNOSTIC, value)
        return ACCOUNT_SYNC_ERROR_SECRET_RE.sub(
            lambda match: f"{match.group(1)}={REDACTED_ACCOUNT_DIAGNOSTIC}",
            value,
        )

    def sync_account(self, account_id: int) -> dict[str, Any]:
        account = self.get_account(account_id)
        if account["auth_status"] != "已授权":
            raise RuntimeError("账号未授权，请先完成扫码登录")
        if not self.browser_auth_manager:
            raise RuntimeError("真实浏览器同步服务未配置")
        with self._account_browser_scope(account_id):
            self.db.execute(
                """
                UPDATE platform_accounts
                SET sync_status = '同步中', sync_error = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (account_id,),
            )
            try:
                if account["platform"] == "小红书":
                    self._verify_xiaohongshu_publish_login(account)
                data = self.browser_auth_manager.sync(account)
                self._persist_account_sync(account_id, data)
            except Exception as exc:
                error = str(exc) or "平台数据同步失败"
                self.db.execute(
                    """
                    UPDATE platform_accounts
                    SET login_status = ?, sync_status = '同步失败', sync_error = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (self._login_status_from_sync_error(error), error, account_id),
                )
                raise RuntimeError(f"真实数据同步失败：{error}") from exc
        return {
            "synced": True,
            "source": data.get("source", "browser"),
            "account": self.get_public_account(account_id),
        }

    def sync_due_accounts(self, now: datetime | None = None) -> dict[str, Any]:
        today = (now or datetime.now()).date().isoformat()
        accounts = self.db.fetch_all(
            """
            SELECT id
            FROM platform_accounts
            WHERE auth_status = '已授权'
              AND (last_sync_at = '' OR date(last_sync_at) < date(?))
            """,
            (today,),
        )
        synced = 0
        failed = 0
        for account in accounts:
            try:
                self.sync_account(int(account["id"]))
                synced += 1
            except RuntimeError:
                failed += 1
        return {"synced": synced, "failed": failed}

    def update_account(self, account_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        account = self.get_account(account_id)
        self.db.execute(
            """
            UPDATE platform_accounts
            SET owner = ?, tag = ?, monitor_status = ?, role = ?, audience = ?, style = ?, taboo = ?,
                preference = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                str(payload.get("owner", "未分配")),
                str(payload.get("tag", "")),
                str(payload.get("monitor_status", "监测中")),
                str(payload.get("role", "")),
                str(payload.get("audience", "")),
                str(payload.get("style", "")),
                str(payload.get("taboo", "")),
                str(payload.get("preference", "")),
                account_id,
            ),
        )
        return self.get_public_account(account_id)

    def delete_account(self, account_id: int) -> dict[str, Any]:
        self.get_account(account_id)
        with self.db.transaction() as conn:
            referenced = conn.execute(
                """
                SELECT 1
                WHERE EXISTS (SELECT 1 FROM publish_jobs WHERE account_id = ?)
                   OR EXISTS (SELECT 1 FROM scheduled_tasks WHERE account_id = ?)
                """,
                (account_id, account_id),
            ).fetchone()
            if referenced:
                raise AccountConflictError(
                    "账号已被发布任务或定时任务引用，不能删除"
                )
            conn.execute("DELETE FROM platform_accounts WHERE id = ?", (account_id,))
        return {"deleted": True, "id": account_id}

    def analytics_accounts(self, platform: str = "", q: str = "") -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if platform and platform != "全部平台":
            clauses.append("a.platform = ?")
            params.append(_normalize_platform(platform))
        if q:
            clauses.append("(a.name LIKE ? OR a.tag LIKE ?)")
            keyword = f"%{q}%"
            params.extend([keyword, keyword])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.db.fetch_all(
            f"""
            SELECT a.id AS account_id, a.name AS account_name, a.platform, a.owner, a.tag,
                   a.last_sync_at, COALESCE(m.fans, 0) AS fans, COALESCE(m.reads, 0) AS reads,
                   COALESCE(m.recommends, 0) AS recommends, COALESCE(m.likes, 0) AS likes,
                   COALESCE(m.comments, 0) AS comments, COALESCE(m.shares, 0) AS shares,
                   COALESCE(m.favorites, 0) AS favorites
            FROM platform_accounts a
            LEFT JOIN account_metric_snapshots m ON m.id = (
                SELECT id FROM account_metric_snapshots
                WHERE account_id = a.id
                ORDER BY metric_date DESC, id DESC
                LIMIT 1
            )
            {where}
            ORDER BY a.last_sync_at DESC, a.id DESC
            """,
            tuple(params),
        )

    def analytics_works(self, platform: str = "", q: str = "") -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if platform and platform != "全部平台":
            clauses.append("w.platform = ?")
            params.append(_normalize_platform(platform))
        if q:
            clauses.append("(w.title LIKE ? OR w.account_name LIKE ?)")
            keyword = f"%{q}%"
            params.extend([keyword, keyword])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.db.fetch_all(
            f"""
            SELECT w.id AS work_id, w.account_id, w.platform, w.account_name, w.title, w.content_type,
                   w.status, w.publish_url, w.published_at, w.updated_at,
                   COALESCE(m.reads, 0) AS reads, COALESCE(m.recommends, 0) AS recommends,
                   COALESCE(m.likes, 0) AS likes, COALESCE(m.comments, 0) AS comments,
                   COALESCE(m.shares, 0) AS shares, COALESCE(m.favorites, 0) AS favorites
            FROM platform_works w
            LEFT JOIN work_metric_snapshots m ON m.id = (
                SELECT id FROM work_metric_snapshots
                WHERE work_id = w.id
                ORDER BY metric_date DESC, id DESC
                LIMIT 1
            )
            {where}
            ORDER BY w.updated_at DESC, w.id DESC
            """,
            tuple(params),
        )

    def list_tasks(self, status: str = "", platform: str = "", q: str = "") -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if status and status != "全部状态":
            clauses.append("status = ?")
            params.append(status)
        if platform and platform != "全部平台":
            clauses.append("platform = ?")
            params.append(_normalize_platform(platform))
        if q:
            clauses.append("(topic LIKE ? OR account_name LIKE ?)")
            keyword = f"%{q}%"
            params.extend([keyword, keyword])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        tasks = self.db.fetch_all(
            f"""
            SELECT id, platform, account_id, account_name, execute_time, task_type, topic, article_requirements, selected_title, title_direction, status,
                   publish_action, publish_result, manual_takeover_url, execution_mode, repeat_rule, weekly_day, next_run_at, failure_stage, failure_reason,
                   retry_count, content_project_id, publish_package_id, publish_job_id, last_run_at, created_at, updated_at
            FROM scheduled_tasks
            {where}
            ORDER BY COALESCE(NULLIF(next_run_at, ''), execute_time) ASC, id DESC
            """,
            tuple(params),
        )
        for task in tasks:
            if task.get("publish_job_id"):
                task["publish_job"] = self.get_publish_job(int(task["publish_job_id"]))
        return tasks

    def get_task(self, task_id: int) -> dict[str, Any]:
        task = self.db.fetch_one("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,))
        if not task:
            raise NotFoundError("任务不存在")
        task["runs"] = self.db.fetch_all(
            """
            SELECT id, task_id, status, started_at, finished_at, failure_stage, failure_reason,
                   content_project_id, publish_package_id, publish_job_id, created_at
            FROM task_runs
            WHERE task_id = ?
            ORDER BY id DESC
            """,
            (task_id,),
        )
        if task.get("content_project_id"):
            task["content_project"] = self.get_project(int(task["content_project_id"]))
        if task.get("publish_package_id"):
            task["publish_package"] = self.db.fetch_one(
                "SELECT * FROM publish_packages WHERE id = ?",
                (task["publish_package_id"],),
            )
        if task.get("publish_job_id"):
            task["publish_job"] = self.get_publish_job(int(task["publish_job_id"]))
        return task

    def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_task_payload(payload)
        task_id = self.db.execute(
            """
            INSERT INTO scheduled_tasks (
              platform, account_id, account_name, execute_time, task_type, topic, article_requirements, selected_title, title_direction, status,
              publish_action, execution_mode, repeat_rule, weekly_day, next_run_at, failure_stage, failure_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized["platform"],
                normalized["account_id"],
                normalized["account_name"],
                normalized["execute_time"],
                normalized["task_type"],
                normalized["topic"],
                normalized["article_requirements"],
                normalized["selected_title"],
                normalized["title_direction"],
                normalized["status"],
                normalized["publish_action"],
                normalized["execution_mode"],
                normalized["repeat_rule"],
                normalized["weekly_day"],
                normalized["next_run_at"],
                str(payload.get("failure_stage", "")),
                str(payload.get("failure_reason", "")),
            ),
        )
        return self.get_task(task_id)

    def update_task(self, task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.get_task(task_id)
        normalized = self._normalize_task_payload(payload, force_pending=True)
        with self.db.transaction() as conn:
            current, _, _ = self._resolve_task_publish_job(conn, task_id)
            conflict = current["status"] in {"执行中", "待人工确认"}
            if current.get("publish_job_id"):
                job_row = conn.execute(
                    "SELECT status FROM publish_jobs WHERE id = ?",
                    (current["publish_job_id"],),
                ).fetchone()
                job_status = str(job_row["status"]) if job_row else ""
                if job_status in {
                    "pending",
                    "checking_login",
                    "uploading_assets",
                    "filling_content",
                    "publishing",
                    "awaiting_confirmation",
                    "failed",
                }:
                    conflict = True
                elif job_status in {"published", "platform_draft"}:
                    expected_status = (
                        "已发布" if job_status == "published" else "已生成平台草稿"
                    )
                    task_synchronized = current["status"] == expected_status
                    if current["execution_mode"] == "repeat":
                        task_synchronized = (
                            current["status"] == "待执行"
                            and bool(current["next_run_at"])
                        )
                    run = conn.execute(
                        """
                        SELECT status
                        FROM task_runs
                        WHERE task_id = ? AND publish_job_id = ?
                        ORDER BY id DESC LIMIT 1
                        """,
                        (task_id, current["publish_job_id"]),
                    ).fetchone()
                    package = conn.execute(
                        """
                        SELECT status
                        FROM publish_packages
                        WHERE id = ? AND publish_job_id = ?
                        """,
                        (current["publish_package_id"], current["publish_job_id"]),
                    ).fetchone()
                    if (
                        not task_synchronized
                        or run is None
                        or run["status"] != expected_status
                        or package is None
                        or package["status"] != job_status
                    ):
                        conflict = True
                else:
                    conflict = True
            if conflict:
                raise TaskConflictError(
                    "任务正在发布或等待确认，或存在可恢复/待同步发布结果，暂不可编辑"
                )
            cursor = conn.execute(
                """
                UPDATE scheduled_tasks
                SET platform = ?, account_id = ?, account_name = ?, execute_time = ?, task_type = ?, topic = ?, article_requirements = ?,
                    selected_title = ?, title_direction = ?, status = '待执行',
                    publish_action = ?, execution_mode = ?, repeat_rule = ?, weekly_day = ?,
                    next_run_at = ?, failure_stage = '', failure_reason = '', publish_result = '', manual_takeover_url = '',
                    content_project_id = NULL, publish_package_id = NULL, publish_job_id = NULL,
                    generation = generation + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND generation = ?
                """,
                (
                    normalized["platform"],
                    normalized["account_id"],
                    normalized["account_name"],
                    normalized["execute_time"],
                    normalized["task_type"],
                    normalized["topic"],
                    normalized["article_requirements"],
                    normalized["selected_title"],
                    normalized["title_direction"],
                    normalized["publish_action"],
                    normalized["execution_mode"],
                    normalized["repeat_rule"],
                    normalized["weekly_day"],
                    normalized["next_run_at"],
                    task_id,
                    current["generation"],
                ),
            )
            if cursor.rowcount != 1:
                raise TaskConflictError("任务代次已变化，请刷新后重试")
        return self.get_task(task_id)

    def delete_task(self, task_id: int) -> dict[str, Any]:
        self.get_task(task_id)
        self.db.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
        return {"deleted": True, "id": task_id}

    def retry_failed_tasks(self, task_ids: list[int] | None = None) -> dict[str, Any]:
        params: tuple[Any, ...] = ()
        where = "status = '失败'"
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            where = f"{where} AND id IN ({placeholders})"
            params = tuple(task_ids)
        failed_tasks = self.db.fetch_all(
            f"SELECT id, generation FROM scheduled_tasks WHERE {where}", params
        )
        updated = 0
        for task in failed_tasks:
            _, claimed = self._execute_task(
                int(task["id"]),
                datetime.now(),
                expected_status="失败",
                expected_generation=int(task["generation"]),
                increment_retry=True,
            )
            updated += int(claimed)
        return {"updated": updated}

    def sync_failed_task_to_platform_draft(
        self, task_id: int, now: datetime | None = None
    ) -> dict[str, Any]:
        current = now or datetime.now()
        started_at = _format_task_time(current)
        with self.db.transaction() as conn:
            observed = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if observed is None:
                raise NotFoundError("任务不存在")
            task = dict(observed)
            if task["status"] != "失败":
                raise RuntimeError("只有失败任务可以同步到平台草稿箱")
            if not task.get("content_project_id"):
                raise RuntimeError("该任务还没有生成文章内容，不能同步到平台草稿箱")
            if not task.get("account_id"):
                raise RuntimeError("请选择一个已授权的微信公众号账号")
            cursor = conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = '执行中', last_run_at = ?, next_run_at = '',
                    publish_action = 'draft', publish_result = '',
                    failure_stage = '', failure_reason = '', manual_takeover_url = '',
                    publish_job_id = NULL, retry_count = retry_count + 1,
                    generation = generation + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = '失败' AND generation = ?
                """,
                (started_at, task_id, task["generation"]),
            )
            if cursor.rowcount != 1:
                raise TaskConflictError("任务代次已变化，请刷新后重试")
            run_generation = int(task["generation"]) + 1
            run_id = int(
                conn.execute(
                    """
                    INSERT INTO task_runs (task_id, generation, status, started_at)
                    VALUES (?, ?, '执行中', ?)
                    """,
                    (task_id, run_generation, started_at),
                ).lastrowid
            )
        task = self.get_task(task_id)
        stage = "提交平台草稿"
        project_id = int(task["content_project_id"])
        failed_job_id: int | None = None
        package_id: int | None = (
            int(task["publish_package_id"]) if task.get("publish_package_id") else None
        )
        try:
            account = self._validate_task_publish_target(task)
            project = self.get_project(project_id)
            job = self.prepare_publish_job(project_id, int(account["id"]), "draft")
            failed_job_id = int(job["id"])
            package_id = self._prepare_existing_task_publish_package(
                task, project, account, job
            )
            self._link_task_publish_job(task_id, run_id, failed_job_id, package_id)
            job = self.execute_publish_job(failed_job_id)
            self._postprocess_publish_package(project, account, job)
            self._finish_task_from_publish_job(
                task,
                run_id,
                project_id,
                package_id,
                job,
                current,
            )
        except Exception as exc:
            self._finish_task_failure(
                task_id,
                run_id,
                stage,
                str(exc),
                current,
                package_id,
                failed_job_id,
            )
            raise RuntimeError(str(exc)) from exc
        result = self.get_task(task_id)
        if result["status"] == "失败":
            reason = result.get("failure_reason") or "同步到平台草稿箱失败"
            raise RuntimeError(str(reason))
        return result

    def execute_due_tasks(self, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now()
        due_at = _format_task_time(current)
        recoverable_tasks = self.db.fetch_all(
            f"""
            SELECT id
            FROM scheduled_tasks AS tasks
            WHERE tasks.status = '执行中'
               OR (
                    tasks.status = '待人工确认'
                    AND EXISTS (
                        SELECT 1
                        FROM publish_jobs AS jobs
                        WHERE jobs.id = tasks.publish_job_id
                          AND jobs.status IN ({','.join('?' for _ in WAITING_TASK_RECOVERY_JOB_STATUSES)})
                    )
               )
            ORDER BY id ASC
            """,
            WAITING_TASK_RECOVERY_JOB_STATUSES,
        )
        for task in recoverable_tasks:
            try:
                recovered = self._recover_task_from_publish_job(int(task["id"]), current)
                if not recovered:
                    self._fail_stale_pre_publish_task(int(task["id"]), current)
            except Exception:
                # A terminal platform job stays recoverable; a later scan retries only this transaction.
                continue
        due_tasks = self.db.fetch_all(
            """
            SELECT id, generation
            FROM scheduled_tasks
            WHERE status = '待执行'
              AND COALESCE(NULLIF(next_run_at, ''), execute_time) <= ?
            ORDER BY COALESCE(NULLIF(next_run_at, ''), execute_time) ASC, id ASC
            """,
            (due_at,),
        )
        executed = 0
        for task in due_tasks:
            _, claimed = self._execute_task(
                int(task["id"]),
                current,
                expected_status="待执行",
                expected_generation=int(task["generation"]),
            )
            executed += int(claimed)
        return {"executed": executed}

    def _fail_stale_pre_publish_task(self, task_id: int, current: datetime) -> bool:
        comparable_current = current
        if comparable_current.tzinfo is not None:
            comparable_current = comparable_current.astimezone().replace(tzinfo=None)
        with self.db.transaction() as conn:
            task, run, publish_job_id = self._resolve_task_publish_job(conn, task_id)
            if (
                task["status"] != "执行中"
                or run is None
                or run["status"] != "执行中"
                or publish_job_id is not None
            ):
                return False
            try:
                started_at = datetime.strptime(
                    str(run["started_at"]), TASK_TIME_FORMAT
                )
            except ValueError:
                return False
            if comparable_current - started_at < PUBLISH_INTERRUPTION_TIMEOUT:
                return False
            reason = "内容生成阶段长时间无进展，已安全结束本次执行，可重试任务"
            task_cursor = conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = '失败', failure_stage = '内容生成中断', failure_reason = ?,
                    manual_takeover_url = 'manual-takeover://task/' || id,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = '执行中' AND generation = ?
                  AND publish_job_id IS NULL
                """,
                (reason, task_id, task["generation"]),
            )
            run_cursor = conn.execute(
                """
                UPDATE task_runs
                SET status = '失败', finished_at = ?, failure_stage = '内容生成中断',
                    failure_reason = ?
                WHERE id = ? AND task_id = ? AND status = '执行中'
                  AND generation = ? AND publish_job_id IS NULL
                """,
                (
                    _format_task_time(current),
                    reason,
                    run["id"],
                    task_id,
                    task["generation"],
                ),
            )
            if task_cursor.rowcount != 1 or run_cursor.rowcount != 1:
                raise TaskConflictError("任务代次已变化，请刷新后重试")
            return True

    def execute_task(self, task_id: int, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now()
        if self._recover_task_from_publish_job(task_id, current):
            return self.get_task(task_id)
        detail, _ = self._execute_task(task_id, current)
        return detail

    def _recover_task_from_publish_job(self, task_id: int, current: datetime) -> bool:
        with self.db.transaction() as conn:
            task, run, publish_job_id = self._resolve_task_publish_job(conn, task_id)
            if run is None:
                return False
            status_pair = (str(task["status"]), str(run["status"]))
            if status_pair == ("执行中", "执行中"):
                expected_task_status, expected_run_status = status_pair
            elif (
                status_pair == ("待人工确认", "待人工确认")
                and publish_job_id is not None
            ):
                expected_task_status, expected_run_status = status_pair
            else:
                return False
        if publish_job_id is None:
            if not self._adopt_orphan_publish_job(task, run):
                return False
            with self.db.transaction() as conn:
                task, run, publish_job_id = self._resolve_task_publish_job(conn, task_id)
                if publish_job_id is None or run is None:
                    return False
        job = self.get_publish_job(publish_job_id)
        if (
            expected_task_status == "待人工确认"
            and job["status"] not in WAITING_TASK_RECOVERY_JOB_STATUSES
        ):
            return False
        if job["status"] == "pending":
            job = self.execute_publish_job(publish_job_id)
        elif job["status"] in UNCERTAIN_IN_PROGRESS_STATUSES:
            stale_before_at = current - PUBLISH_INTERRUPTION_TIMEOUT
            if stale_before_at.tzinfo is None:
                stale_before_at = stale_before_at.astimezone()
            stale_before = stale_before_at.astimezone(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            job = self.publish_coordinator.fail_stale_in_progress_job(
                publish_job_id, stale_before
            )
        if job["status"] in UNCERTAIN_IN_PROGRESS_STATUSES:
            return True
        if job["status"] not in {
            "platform_draft",
            "awaiting_confirmation",
            "published",
            "failed",
        }:
            return True
        package = None
        for package_id in {
            task.get("publish_package_id"),
            run.get("publish_package_id"),
        }:
            if package_id is None:
                continue
            package = self.db.fetch_one(
                "SELECT id FROM publish_packages WHERE id = ? AND publish_job_id = ?",
                (package_id, job["id"]),
            )
            if package:
                break
        return self._finish_task_from_publish_job(
            task,
            int(run["id"]),
            int(task.get("content_project_id") or job["project_id"]),
            int(package["id"]) if package else None,
            job,
            current,
            expected_task_status=expected_task_status,
            expected_run_status=expected_run_status,
        )

    def _adopt_orphan_publish_job(
        self, observed_task: dict[str, Any], observed_run: dict[str, Any]
    ) -> bool:
        project_id = observed_task.get("content_project_id")
        account_id = observed_task.get("account_id")
        if project_id is None or account_id is None:
            return False
        project = self.get_project(int(project_id))
        account = self.get_account(int(account_id))
        with self.db.transaction() as conn:
            task, run, publish_job_id = self._resolve_task_publish_job(
                conn, int(observed_task["id"])
            )
            if publish_job_id is not None:
                return True
            if (
                task["status"] != "执行中"
                or run is None
                or run["status"] != "执行中"
                or int(run["id"]) != int(observed_run["id"])
                or int(task.get("content_project_id") or 0) != int(project_id)
            ):
                return False
            orphan_rows = conn.execute(
                """
                SELECT jobs.id
                FROM publish_jobs AS jobs
                WHERE jobs.project_id = ? AND jobs.account_id = ?
                  AND jobs.action = ?
                  AND NOT EXISTS (
                    SELECT 1 FROM scheduled_tasks
                    WHERE scheduled_tasks.publish_job_id = jobs.id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM task_runs
                    WHERE task_runs.publish_job_id = jobs.id
                  )
                ORDER BY jobs.id
                """,
                (project_id, account_id, task["publish_action"]),
            ).fetchall()
            if len(orphan_rows) > 1:
                raise TaskConflictError("任务存在多个未关联的待发布作业")
            if not orphan_rows:
                return False
            publish_job_id = int(orphan_rows[0]["id"])
            package_rows = conn.execute(
                "SELECT id FROM publish_packages WHERE publish_job_id = ? ORDER BY id",
                (publish_job_id,),
            ).fetchall()
            if len(package_rows) > 1:
                raise TaskConflictError("待发布作业存在多个兼容发布包")
            if package_rows:
                package_id = int(package_rows[0]["id"])
            else:
                package = self._persist_publish_package(
                    project,
                    account,
                    {"id": publish_job_id},
                    pending=True,
                    conn=conn,
                )
                package_id = int(package["id"])
            self._link_task_publish_job_in_transaction(
                conn,
                int(task["id"]),
                int(run["id"]),
                publish_job_id,
                package_id,
            )
            return True

    def _prepare_existing_task_publish_package(
        self,
        task: dict[str, Any],
        project: dict[str, Any],
        account: dict[str, Any],
        job: dict[str, Any],
    ) -> int:
        package_id = task.get("publish_package_id")
        if package_id is not None:
            with self.db.transaction() as conn:
                package = conn.execute(
                    "SELECT * FROM publish_packages WHERE id = ? AND project_id = ?",
                    (package_id, project["id"]),
                ).fetchone()
                reusable = False
                if package and (
                    package["publish_job_id"] is None
                    or int(package["publish_job_id"]) == int(job["id"])
                ):
                    package_account_id = package["account_id"]
                    same_account = (
                        package_account_id is None
                        or int(package_account_id) == int(account["id"])
                    )
                    no_platform_result = (
                        not str(package["platform_draft_id"] or "").strip()
                        and not str(package["platform_draft_url"] or "").strip()
                        and not str(package["published_at"] or "").strip()
                        and str(package["platform_response_json"] or "{}").strip() == "{}"
                    )
                    reusable = (
                        same_account
                        and no_platform_result
                        and str(package["status"]) in {"failed", "pending_publish"}
                    )
                if reusable:
                    cursor = conn.execute(
                        """
                        UPDATE publish_packages
                        SET account_id = ?, publish_job_id = ?, platform = ?,
                            account_name = ?, status = 'pending_publish',
                            platform_draft_id = '', platform_draft_url = '',
                            platform_response_json = '{}',
                            failure_stage = '', failure_reason = '',
                            published_at = '', updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND project_id = ?
                          AND (publish_job_id IS NULL OR publish_job_id = ?)
                        """,
                        (
                            account["id"],
                            job["id"],
                            account["platform"],
                            account["name"],
                            package_id,
                            project["id"],
                            job["id"],
                        ),
                    )
                    if cursor.rowcount == 1:
                        return int(package_id)
        package = self._persist_publish_package(project, account, job, pending=True)
        return int(package["id"])

    def _execute_task(
        self,
        task_id: int,
        current: datetime,
        expected_status: str | None = None,
        expected_generation: int | None = None,
        increment_retry: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        started_at = _format_task_time(current)
        run_id = self._claim_task_execution(
            task_id,
            started_at,
            expected_status=expected_status,
            expected_generation=expected_generation,
            increment_retry=increment_retry,
        )
        if run_id is None:
            return self.get_task(task_id), False
        task = self.get_task(task_id)
        stage = "检查账号"
        project_id: int | None = None
        failed_package_id: int | None = None
        failed_job_id: int | None = None
        finalization_started = False
        try:
            if task.get("publish_job_id"):
                failed_job_id = int(task["publish_job_id"])
                job = self.get_publish_job(failed_job_id)
                package = self.db.fetch_one(
                    "SELECT id FROM publish_packages WHERE publish_job_id = ? ORDER BY id DESC LIMIT 1",
                    (failed_job_id,),
                )
                failed_package_id = int(package["id"]) if package else None
                self._link_task_publish_job(
                    task_id, run_id, failed_job_id, failed_package_id
                )
                if job["status"] == "failed":
                    job = self.retry_publish_job(failed_job_id)
                elif job["status"] == "pending":
                    job = self.execute_publish_job(failed_job_id)
                project_id = int(task.get("content_project_id") or job["project_id"])
                finalization_started = True
                self._finish_task_from_publish_job(
                    task,
                    run_id,
                    project_id,
                    failed_package_id,
                    job,
                    current,
                )
                return self.get_task(task_id), True
            stage = "生成标题"
            title_choice = self._recommend_task_title(task)
            task_title = title_choice["title"]
            title_direction = title_choice["direction"]
            self.db.execute(
                """
                UPDATE scheduled_tasks
                SET selected_title = ?, title_direction = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (task_title, title_direction, task_id),
            )
            project = self.create_project(
                title=task_title,
                title_direction=title_direction,
                platform=task["platform"],
            )
            project_id = int(project["id"])
            self.db.execute(
                "UPDATE scheduled_tasks SET content_project_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (project["id"], task_id),
            )
            stage = "生成文章"
            article = self.generate_article(project["id"])
            stage = "生成图片"
            try:
                images = self.generate_images(project["id"])
            except Exception as image_exc:
                images = self._create_task_placeholder_images(project["id"], str(image_exc))
            cover = next((asset for asset in images["assets"] if asset["asset_type"] == "cover" and asset["selected"]), None)
            inline_assets = [asset for asset in images["assets"] if asset["asset_type"] == "inline" and asset["selected"]]
            layout_blocks = [
                {
                    "section_type": "title",
                    "content": project["title"],
                    "media_asset_id": cover["id"] if cover else None,
                    "sort_order": 1,
                }
            ]
            for index, block in enumerate(article["blocks"]):
                media = inline_assets[index]["id"] if index < len(inline_assets) else None
                layout_blocks.append(
                    {
                        "section_type": block["type"],
                        "content": block["content"],
                        "media_asset_id": media,
                        "sort_order": index + 2,
                    }
                )
            stage = "文章排版"
            self.save_layout(project["id"], layout_blocks)
            stage = "提交平台草稿" if task["publish_action"] == "draft" else "发布文章"
            task = self.get_task(task_id)
            account = self._validate_task_publish_target(task)
            job = self.prepare_publish_job(
                project["id"], int(account["id"]), task["publish_action"]
            )
            failed_job_id = int(job["id"])
            package = self._persist_publish_package(project, account, job, pending=True)
            failed_package_id = int(package["id"])
            self._link_task_publish_job(
                task_id, run_id, failed_job_id, failed_package_id
            )
            job = self.execute_publish_job(failed_job_id)
            self._postprocess_publish_package(project, account, job)
            finalization_started = True
            self._finish_task_from_publish_job(task, run_id, project["id"], package["id"], job, current)
        except Exception as exc:
            if failed_job_id is not None:
                job = self.get_publish_job(failed_job_id)
                if failed_package_id is None:
                    package = self.db.fetch_one(
                        "SELECT id FROM publish_packages WHERE publish_job_id = ? ORDER BY id DESC LIMIT 1",
                        (failed_job_id,),
                    )
                    failed_package_id = int(package["id"]) if package else None
                if job["status"] in {"platform_draft", "awaiting_confirmation", "published", "failed"}:
                    if not finalization_started:
                        try:
                            self._finish_task_from_publish_job(
                                task,
                                run_id,
                                int(project_id or job["project_id"]),
                                failed_package_id,
                                job,
                                current,
                            )
                        except Exception:
                            pass
                else:
                    self._finish_task_failure(
                        task_id,
                        run_id,
                        stage,
                        str(exc),
                        current,
                        failed_package_id,
                        failed_job_id,
                    )
            else:
                failure_stage = "任务类型未开放" if task["task_type"] == TASK_TYPE_XIAOHONGSHU_RESERVED else stage
                self._finish_task_failure(task_id, run_id, failure_stage, str(exc), current)
        return self.get_task(task_id), True

    def _create_task_placeholder_images(self, project_id: int, reason: str) -> dict[str, Any]:
        generated_assets = []
        prompt = f"本地兜底图：图片模型调用失败，任务继续执行。失败原因：{reason[:240]}"
        for asset_type in ("cover", "inline"):
            filename = f"{asset_type}-task-fallback-{project_id}.svg"
            relative_path = Path("uploads") / filename
            file_path = self.upload_dir / filename
            label = "Cover fallback" if asset_type == "cover" else "Inline fallback"
            file_path.write_bytes(
                (
                    "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"1200\" height=\"675\" viewBox=\"0 0 1200 675\">"
                    "<rect width=\"1200\" height=\"675\" fill=\"#f5f7fb\"/>"
                    "<rect x=\"80\" y=\"80\" width=\"1040\" height=\"515\" rx=\"28\" fill=\"#ffffff\" stroke=\"#d9e2ef\" stroke-width=\"4\"/>"
                    "<circle cx=\"600\" cy=\"285\" r=\"72\" fill=\"#3861f6\" opacity=\"0.12\"/>"
                    "<path d=\"M548 332h104M600 280v104\" stroke=\"#3861f6\" stroke-width=\"18\" stroke-linecap=\"round\"/>"
                    f"<text x=\"600\" y=\"455\" text-anchor=\"middle\" font-family=\"Arial, sans-serif\" font-size=\"42\" fill=\"#17233d\">{label}</text>"
                    "</svg>"
                ).encode("utf-8")
            )
            if asset_type == "cover":
                self.db.execute(
                    "UPDATE media_assets SET selected = 0 WHERE project_id = ? AND asset_type = 'cover'",
                    (project_id,),
                )
            asset_id = self.db.execute(
                """
                INSERT INTO media_assets (project_id, asset_type, url, local_path, prompt, selected)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (project_id, asset_type, f"/uploads/{filename}", str(relative_path), prompt),
            )
            asset = self.db.fetch_one("SELECT * FROM media_assets WHERE id = ?", (asset_id,))
            if asset:
                generated_assets.append(asset)
        self._set_stage(project_id, "images")
        self.autosave(project_id, stage="images", restore_stage="layout")
        return {
            "project": self.get_project(project_id),
            "assets": self.get_media_assets(project_id),
            "generated_assets": generated_assets,
        }

    def _claim_task_execution(
        self,
        task_id: int,
        started_at: str,
        expected_status: str | None = None,
        expected_generation: int | None = None,
        increment_retry: bool = False,
    ) -> int | None:
        status_clause = "status IN ('待执行', '已重试')"
        where_params: list[Any] = []
        if expected_status is not None:
            status_clause = "status = ?"
            where_params.append(expected_status)
        generation_clause = ""
        if expected_generation is not None:
            generation_clause = " AND generation = ?"
            where_params.append(expected_generation)
        with self.db.transaction() as conn:
            observed_task_row = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if observed_task_row is None:
                raise NotFoundError("任务不存在")
            observed_task = dict(observed_task_row)
            status_matches = (
                observed_task["status"] == expected_status
                if expected_status is not None
                else observed_task["status"] in {"待执行", "已重试"}
            )
            generation_matches = (
                expected_generation is None
                or int(observed_task["generation"]) == expected_generation
            )
            if not status_matches or not generation_matches:
                return None
            task, latest_run, publish_job_id = self._resolve_task_publish_job(conn, task_id)
            clear_previous_generation = publish_job_id is None
            if (
                task["status"] == "待执行"
                and task["execution_mode"] == "repeat"
                and task["next_run_at"]
                and publish_job_id is not None
                and latest_run is not None
            ):
                job_row = conn.execute(
                    "SELECT status FROM publish_jobs WHERE id = ?", (publish_job_id,)
                ).fetchone()
                job_status = str(job_row["status"]) if job_row else ""
                expected_run_status = {
                    "published": "已发布",
                    "platform_draft": "已生成平台草稿",
                }.get(job_status)
                package_ids = {
                    int(package_id)
                    for package_id in (
                        task.get("publish_package_id"),
                        latest_run.get("publish_package_id"),
                    )
                    if package_id is not None
                }
                package_synchronized = any(
                    (
                        package_row := conn.execute(
                            "SELECT status, publish_job_id FROM publish_packages WHERE id = ?",
                            (package_id,),
                        ).fetchone()
                    )
                    and package_row["status"] == job_status
                    and package_row["publish_job_id"] == publish_job_id
                    for package_id in package_ids
                )
                clear_previous_generation = bool(
                    expected_run_status
                    and latest_run["status"] == expected_run_status
                    and package_synchronized
                )
            clear_previous = int(clear_previous_generation)
            cursor = conn.execute(
                f"""
                UPDATE scheduled_tasks
                SET status = '执行中', last_run_at = ?, next_run_at = '',
                    publish_result = CASE WHEN status = '待执行' AND ? THEN '' ELSE publish_result END,
                    content_project_id = CASE WHEN status = '待执行' AND ? THEN NULL ELSE content_project_id END,
                    publish_package_id = CASE WHEN status = '待执行' AND ? THEN NULL ELSE publish_package_id END,
                    publish_job_id = CASE WHEN status = '待执行' AND ? THEN NULL ELSE publish_job_id END,
                    retry_count = retry_count + ?, generation = generation + 1,
                    failure_stage = '', failure_reason = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND {status_clause}{generation_clause}
                """,
                (
                    started_at,
                    clear_previous,
                    clear_previous,
                    clear_previous,
                    clear_previous,
                    int(increment_retry),
                    task_id,
                    *where_params,
                ),
            )
            if cursor.rowcount != 1:
                return None
            run_generation = int(task["generation"]) + 1
            run_id = conn.execute(
                """
                INSERT INTO task_runs (task_id, generation, status, started_at)
                VALUES (?, ?, '执行中', ?)
                """,
                (task_id, run_generation, started_at),
            ).lastrowid
            return int(run_id)

    def _resolve_task_publish_job(
        self, conn: Any, task_id: int
    ) -> tuple[dict[str, Any], dict[str, Any] | None, int | None]:
        task_row = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if task_row is None:
            raise NotFoundError("任务不存在")
        task = dict(task_row)
        latest_run_row = conn.execute(
            """
            SELECT *
            FROM task_runs
            WHERE task_id = ? AND generation = ?
            ORDER BY id DESC LIMIT 1
            """,
            (task_id, task["generation"]),
        ).fetchone()
        latest_run = dict(latest_run_row) if latest_run_row else None
        candidates = {
            int(job_id)
            for job_id in (
                task.get("publish_job_id"),
                latest_run.get("publish_job_id") if latest_run else None,
            )
            if job_id is not None
        }
        package_ids = {
            int(package_id)
            for package_id in (
                task.get("publish_package_id"),
                latest_run.get("publish_package_id") if latest_run else None,
            )
            if package_id is not None
        }
        for package_id in package_ids:
            package_row = conn.execute(
                "SELECT publish_job_id FROM publish_packages WHERE id = ?",
                (package_id,),
            ).fetchone()
            if package_row and package_row["publish_job_id"] is not None:
                candidates.add(int(package_row["publish_job_id"]))
        if len(candidates) > 1:
            raise TaskConflictError("任务存在相互冲突的历史发布作业关联")
        publish_job_id = next(iter(candidates), None)
        if publish_job_id is None:
            return task, latest_run, None
        if conn.execute(
            "SELECT 1 FROM publish_jobs WHERE id = ?", (publish_job_id,)
        ).fetchone() is None:
            raise TaskConflictError("任务关联的历史发布作业不存在")
        if task.get("publish_job_id") is None:
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET publish_job_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND publish_job_id IS NULL
                """,
                (publish_job_id, task_id),
            )
            task["publish_job_id"] = publish_job_id
        if latest_run is not None and latest_run.get("publish_job_id") is None:
            conn.execute(
                "UPDATE task_runs SET publish_job_id = ? WHERE id = ? AND publish_job_id IS NULL",
                (publish_job_id, latest_run["id"]),
            )
            latest_run["publish_job_id"] = publish_job_id
        return task, latest_run, publish_job_id

    def _link_task_publish_job(
        self,
        task_id: int,
        run_id: int,
        publish_job_id: int,
        publish_package_id: int | None = None,
    ) -> None:
        with self.db.transaction() as conn:
            self._link_task_publish_job_in_transaction(
                conn,
                task_id,
                run_id,
                publish_job_id,
                publish_package_id,
            )

    @staticmethod
    def _link_task_publish_job_in_transaction(
        conn: Any,
        task_id: int,
        run_id: int,
        publish_job_id: int,
        publish_package_id: int | None = None,
    ) -> None:
        run_row = conn.execute(
            "SELECT generation FROM task_runs WHERE id = ? AND task_id = ?",
            (run_id, task_id),
        ).fetchone()
        if run_row is None:
            raise RuntimeError("发布作业关联的运行代次不存在")
        generation = int(run_row["generation"])
        task_cursor = conn.execute(
            """
            UPDATE scheduled_tasks
            SET publish_job_id = ?,
                publish_package_id = COALESCE(?, publish_package_id),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = '执行中'
              AND generation = ?
              AND (publish_job_id IS NULL OR publish_job_id = ?)
            """,
            (
                publish_job_id,
                publish_package_id,
                task_id,
                generation,
                publish_job_id,
            ),
        )
        run_cursor = conn.execute(
            """
            UPDATE task_runs
            SET publish_job_id = ?,
                publish_package_id = COALESCE(?, publish_package_id)
            WHERE id = ? AND task_id = ? AND status = '执行中'
              AND generation = ?
              AND (publish_job_id IS NULL OR publish_job_id = ?)
            """,
            (
                publish_job_id,
                publish_package_id,
                run_id,
                task_id,
                generation,
                publish_job_id,
            ),
        )
        if task_cursor.rowcount != 1 or run_cursor.rowcount != 1:
            raise RuntimeError("发布作业关联的任务代次已变化")

    def _recommend_task_title(self, task: dict[str, Any]) -> dict[str, str]:
        platform = str(task["platform"])
        if platform == "小红书":
            platform_instruction = (
                "请结合主题约束、账号信息和创作要求，给出适合小红书图文笔记的标题建议。"
                "后续正文不可超过1000字，这是硬性条件，标题应适配短笔记表达。"
            )
        else:
            platform_instruction = (
                "请结合主题约束、账号信息和创作要求，给出适合公众号图文的标题建议。"
            )
        prompt = (
            "这是任务中心定时任务，请模拟内容创作第一步“标题确认”。"
            f"平台：{platform}；账号：{task['account_name']}；"
            f"主题约束：{task['topic']}；"
            f"创作要求：{task.get('article_requirements') or '未填写'}。"
            f"{platform_instruction}"
        )
        recommendations = self.recommend_topics(prompt)
        for recommendation in recommendations:
            for title in recommendation.titles:
                if title:
                    return {"title": title, "direction": recommendation.direction}
        raise RuntimeError("AI 未返回可用标题")

    def _finish_task_from_publish_job(
        self,
        task: dict[str, Any],
        run_id: int,
        project_id: int,
        package_id: int | None,
        job: dict[str, Any],
        now: datetime,
        expected_task_status: str = "执行中",
        expected_run_status: str = "执行中",
    ) -> bool:
        result_by_status = {
            "published": "已发布",
            "platform_draft": "已生成平台草稿",
            "awaiting_confirmation": "待人工确认",
        }
        job_status = str(job["status"])
        publish_result = result_by_status.get(job_status)
        if job_status != "failed" and not publish_result:
            raise RuntimeError(f"发布任务返回了不可结束的状态：{job['status']}")
        waiting = job_status == "awaiting_confirmation"
        finished_at = "" if waiting else _format_task_time(now)
        next_run_at = ""
        status = "失败" if job_status == "failed" else str(publish_result)
        if job_status != "failed" and task["execution_mode"] == "repeat" and not waiting:
            status = "待执行"
            next_run_at = self._next_repeat_time(task, now)
        failure_stage = str(job["failure_stage"]) if job_status == "failed" else ""
        failure_reason = str(job["failure_reason"]) if job_status == "failed" else ""
        run_status = "失败" if job_status == "failed" else str(publish_result)
        manual_takeover_url = f"manual-takeover://task/{task['id']}" if job_status == "failed" else ""
        with self.db.transaction() as conn:
            task_cursor = conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = ?, next_run_at = ?, failure_stage = ?, failure_reason = ?,
                    publish_result = ?, manual_takeover_url = ?, content_project_id = ?,
                    publish_package_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = ? AND publish_job_id = ? AND generation = ?
                """,
                (
                    status,
                    next_run_at,
                    failure_stage,
                    failure_reason,
                    "" if job_status == "failed" else publish_result,
                    manual_takeover_url,
                    project_id,
                    package_id,
                    task["id"],
                    expected_task_status,
                    job["id"],
                    task["generation"],
                ),
            )
            if task_cursor.rowcount != 1:
                return False
            run_cursor = conn.execute(
                """
                UPDATE task_runs
                SET status = ?, finished_at = ?, failure_stage = ?, failure_reason = ?,
                    content_project_id = ?, publish_package_id = ?
                WHERE id = ? AND task_id = ? AND status = ? AND publish_job_id = ?
                  AND generation = ?
                """,
                (
                    run_status,
                    finished_at,
                    failure_stage,
                    failure_reason,
                    project_id,
                    package_id,
                    run_id,
                    task["id"],
                    expected_run_status,
                    job["id"],
                    task["generation"],
                ),
            )
            if run_cursor.rowcount != 1:
                raise RuntimeError("发布作业关联的运行代次已变化")
            if package_id is not None:
                package_cursor = conn.execute(
                    """
                    UPDATE publish_packages
                    SET status = ?, platform_draft_id = ?, platform_draft_url = ?,
                        platform_response_json = ?, failure_stage = ?, failure_reason = ?,
                        published_at = CASE WHEN ? IN ('published', 'platform_draft') THEN CURRENT_TIMESTAMP ELSE '' END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND publish_job_id = ?
                    """,
                    (
                        job_status,
                        job["platform_draft_id"],
                        job["platform_url"],
                        json.dumps(job.get("response", {}), ensure_ascii=False),
                        failure_stage,
                        failure_reason,
                        job_status,
                        package_id,
                        job["id"],
                    ),
                )
                if package_cursor.rowcount != 1:
                    raise RuntimeError("发布作业关联的兼容发布包已变化")
        return True

    def _finish_task_failure(
        self,
        task_id: int,
        run_id: int,
        stage: str,
        reason: str,
        now: datetime,
        publish_package_id: int | None = None,
        publish_job_id: int | None = None,
    ) -> None:
        finished_at = _format_task_time(now)
        with self.db.transaction() as conn:
            run_row = conn.execute(
                "SELECT generation FROM task_runs WHERE id = ? AND task_id = ?",
                (run_id, task_id),
            ).fetchone()
            if run_row is None:
                raise RuntimeError("任务失败状态写入的运行代次不存在")
            generation = int(run_row["generation"])
            task_cursor = conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = '失败', failure_stage = ?, failure_reason = ?,
                    manual_takeover_url = 'manual-takeover://task/' || ?,
                    publish_package_id = COALESCE(?, publish_package_id),
                    publish_job_id = COALESCE(?, publish_job_id), updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = '执行中' AND generation = ?
                """,
                (
                    stage,
                    reason,
                    task_id,
                    publish_package_id,
                    publish_job_id,
                    task_id,
                    generation,
                ),
            )
            run_cursor = conn.execute(
                """
                UPDATE task_runs
                SET status = '失败', finished_at = ?, failure_stage = ?, failure_reason = ?,
                    publish_package_id = COALESCE(?, publish_package_id),
                    publish_job_id = COALESCE(?, publish_job_id)
                WHERE id = ? AND task_id = ? AND status = '执行中' AND generation = ?
                """,
                (
                    finished_at,
                    stage,
                    reason,
                    publish_package_id,
                    publish_job_id,
                    run_id,
                    task_id,
                    generation,
                ),
            )
            if task_cursor.rowcount != 1 or run_cursor.rowcount != 1:
                raise RuntimeError("任务失败状态写入的运行代次已变化")

    def _normalize_task_payload(self, payload: dict[str, Any], force_pending: bool = False) -> dict[str, Any]:
        task_type = str(payload.get("task_type", TASK_TYPE_WECHAT)).strip() or TASK_TYPE_WECHAT
        if task_type == TASK_TYPE_WECHAT_LEGACY:
            task_type = TASK_TYPE_WECHAT
        platform = _normalize_platform(str(payload.get("platform", "微信公众号")))
        account_id = payload.get("account_id")
        account_name = str(payload.get("account_name", "")).strip()
        if account_id not in {None, ""}:
            account = self.get_account(int(account_id))
            if platform != account["platform"]:
                raise RuntimeError("任务平台与账号平台不一致")
            account_name = account["name"]
        else:
            account = self.db.fetch_one(
                """
                SELECT * FROM platform_accounts
                WHERE platform = ? AND name = ? AND auth_status = '已授权'
                ORDER BY updated_at DESC, id DESC LIMIT 1
                """,
                (platform, account_name),
            )
            if account:
                account_id = int(account["id"])
        if platform == "微信公众号":
            if task_type != TASK_TYPE_WECHAT:
                raise RuntimeError("微信公众号任务类型必须是微信公众号图文生成")
        elif platform == "小红书":
            if task_type == TASK_TYPE_XIAOHONGSHU_RESERVED:
                task_type = TASK_TYPE_XIAOHONGSHU
            if task_type != TASK_TYPE_XIAOHONGSHU:
                raise RuntimeError("小红书任务类型必须是小红书图文生成")
        else:
            raise RuntimeError("V1 定时任务真实发布仅支持微信公众号和小红书图文")
        if account_id in {None, ""}:
            raise RuntimeError(f"请选择一个已授权的{platform}账号")
        account = self.get_account(int(account_id))
        if account["platform"] != platform or account["auth_status"] != "已授权":
            raise RuntimeError(f"请选择一个已授权的{platform}账号")
        publish_action = str(payload.get("publish_action", "publish")).strip() or "publish"
        if publish_action not in {"publish", "draft"}:
            raise RuntimeError("发布动作必须是 publish 或 draft")
        execution_mode = str(payload.get("execution_mode", "once")).strip() or "once"
        repeat_rule = str(payload.get("repeat_rule", "")).strip()
        if execution_mode != "repeat":
            repeat_rule = ""
        if execution_mode == "repeat" and repeat_rule not in {"daily", "weekly"}:
            repeat_rule = "daily"
        weekly_day = payload.get("weekly_day")
        raw_execute_time = str(payload.get("execute_time", "")).strip()
        if execution_mode == "repeat" and _is_clock_time(raw_execute_time):
            execute_time = _format_task_time(_next_repeat_clock_time(raw_execute_time, repeat_rule, weekly_day))
        else:
            execute_time = _format_task_time(_parse_task_time(raw_execute_time))
        if repeat_rule == "weekly":
            weekly_day = int(weekly_day if weekly_day not in {None, ""} else _parse_task_time(execute_time).weekday())
        else:
            weekly_day = None
        status = "待执行" if force_pending else str(payload.get("status", "待执行"))
        return {
            "platform": platform,
            "account_id": int(account_id) if account_id not in {None, ""} else None,
            "account_name": account_name,
            "execute_time": execute_time,
            "task_type": task_type,
            "topic": str(payload.get("topic", "")),
            "article_requirements": str(payload.get("article_requirements", "")),
            "selected_title": str(payload.get("selected_title", "")),
            "title_direction": str(payload.get("title_direction", "")),
            "status": status,
            "publish_action": publish_action,
            "execution_mode": execution_mode,
            "repeat_rule": repeat_rule,
            "weekly_day": weekly_day,
            "next_run_at": execute_time if status == "待执行" else "",
        }

    def _next_repeat_time(self, task: dict[str, Any], now: datetime) -> str:
        base = _parse_task_time(str(task.get("next_run_at") or task["execute_time"]))
        if task["repeat_rule"] == "weekly":
            weekly_day = int(task["weekly_day"] if task["weekly_day"] is not None else base.weekday())
            next_time = base + timedelta(days=(weekly_day - base.weekday()) % 7 or 7)
            while next_time <= now:
                next_time += timedelta(days=7)
            return _format_task_time(next_time)
        next_time = base + timedelta(days=1)
        while next_time <= now:
            next_time += timedelta(days=1)
        return _format_task_time(next_time)

    def create_publish_package(
        self,
        project_id: int,
        platform: str,
        account_name: str,
        account_id: int | None = None,
        action: str = "draft",
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        if _normalize_platform(platform) != "微信公众号":
            raise RuntimeError(f"{platform}真实平台草稿发布适配器尚未接入")
        account = self._resolve_publish_account(platform, account_name, account_id)
        platform = account["platform"]
        account_name = account["name"]
        account_id = int(account["id"])
        job = self.publish_coordinator.create_publish_job(project_id, account_id, action)
        return self._persist_publish_package(project, account, job)

    def _persist_publish_package(
        self,
        project: dict[str, Any],
        account: dict[str, Any],
        job: dict[str, Any],
        pending: bool = False,
        conn: Any | None = None,
    ) -> dict[str, Any]:
        account_id = int(account["id"])
        platform = str(account["platform"])
        account_name = str(account["name"])
        package_status = "pending_publish" if pending else str(job["status"])
        payload = {
            "project": project,
            "article_blocks": project["article_blocks"],
            "media_assets": project["media_assets"],
            "layout_blocks": project["layout_blocks"],
        }
        sql = """
            INSERT INTO publish_packages (
              project_id, account_id, publish_job_id, platform, account_name, payload_json, status,
              platform_draft_id, platform_draft_url, platform_response_json,
              failure_stage, failure_reason, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
        params = (
            project["id"],
            account_id,
            job["id"],
            platform,
            account_name,
            json.dumps({**payload, "publish_job_id": job["id"]}, ensure_ascii=False),
            package_status,
            "" if pending else job["platform_draft_id"],
            "" if pending else job["platform_url"],
            "{}" if pending else json.dumps(job["response"], ensure_ascii=False),
            "" if pending else job["failure_stage"],
            "" if pending else job["failure_reason"],
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if not pending and job["status"] in {"platform_draft", "published"}
            else "",
        )
        if conn is None:
            package_id = self.db.execute(sql, params)
            package = self.db.fetch_one(
                "SELECT * FROM publish_packages WHERE id = ?", (package_id,)
            )
        else:
            package_id = int(conn.execute(sql, params).lastrowid)
            package_row = conn.execute(
                "SELECT * FROM publish_packages WHERE id = ?", (package_id,)
            ).fetchone()
            package = dict(package_row) if package_row else None
        if not pending:
            self._postprocess_publish_package(project, account, job)
        return package or {}

    def _postprocess_publish_package(
        self,
        project: dict[str, Any],
        account: dict[str, Any],
        job: dict[str, Any],
    ) -> None:
        account_id = int(account["id"])
        platform = str(account["platform"])
        account_name = str(account["name"])
        if job["status"] == "platform_draft":
            self._create_work_from_project(
                account_id,
                platform,
                account_name,
                project,
                status="平台草稿",
                publish_url=str(job.get("platform_url") or ""),
            )
        elif job["status"] == "published":
            self._create_work_from_project(
                account_id,
                platform,
                account_name,
                project,
                status="已发布",
                publish_url=str(job.get("platform_url") or ""),
            )
        self._set_stage(int(project["id"]), "publish")
        self.autosave(int(project["id"]), stage="publish", restore_stage="layout")

    def create_publish_job(self, project_id: int, account_id: int, action: str) -> dict[str, Any]:
        job = self.publish_coordinator.create_publish_job(project_id, account_id, action)
        self._postprocess_publish_job_result(job)
        return job

    def prepare_publish_job(self, project_id: int, account_id: int, action: str) -> dict[str, Any]:
        return self.publish_coordinator.prepare_publish_job(project_id, account_id, action)

    def execute_publish_job(self, job_id: int) -> dict[str, Any]:
        pending_job = self.db.fetch_one(
            "SELECT account_id, platform FROM publish_jobs WHERE id = ?",
            (job_id,),
        )
        if not pending_job:
            job = self.publish_coordinator.execute_publish_job(job_id)
            self._postprocess_publish_job_result(job)
            return job
        if pending_job["platform"] != "小红书":
            job = self.publish_coordinator.execute_publish_job(job_id)
            self._postprocess_publish_job_result(job)
            return job
        with self._account_browser_scope(int(pending_job["account_id"])):
            job = self.publish_coordinator.execute_publish_job(job_id)
        self._postprocess_publish_job_result(job)
        return job

    def get_publish_job(self, job_id: int) -> dict[str, Any]:
        try:
            return self.publish_coordinator.get_publish_job(job_id)
        except LookupError as exc:
            raise NotFoundError(str(exc)) from exc

    def get_current_publish_job(
        self, project_id: int, account_id: int, action: str
    ) -> dict[str, Any] | None:
        return self.publish_coordinator.get_current_publish_job(
            project_id, account_id, action
        )

    def get_project_publish_job(self, project_id: int) -> dict[str, Any] | None:
        return self.publish_coordinator.get_project_publish_job(project_id)

    def get_publish_monitoring(self, platform: str | None = None) -> dict[str, Any]:
        return self.publish_coordinator.get_publish_monitoring(platform)

    def retry_publish_job(self, job_id: int) -> dict[str, Any]:
        try:
            try:
                job = self.publish_coordinator.retry_publish_job(job_id)
            except PublishJobValidationError:
                job = self.get_publish_job(job_id)
                linked_failed_task = self.db.fetch_one(
                    "SELECT id FROM scheduled_tasks WHERE publish_job_id = ? AND status = '失败' LIMIT 1",
                    (job_id,),
                )
                if job["status"] not in {
                    "published",
                    "platform_draft",
                    "awaiting_confirmation",
                } or not linked_failed_task:
                    raise
            self._postprocess_publish_job_result(job)
            self._synchronize_linked_tasks(job, "失败", "失败")
            return job
        except LookupError as exc:
            raise NotFoundError(str(exc)) from exc

    def resume_publish_job(self, job_id: int) -> dict[str, Any]:
        try:
            try:
                job = self.publish_coordinator.resume_publish_job(job_id)
            except PublishJobValidationError:
                job = self.get_publish_job(job_id)
                linked_waiting_task = self.db.fetch_one(
                    "SELECT id FROM scheduled_tasks WHERE publish_job_id = ? AND status = '待人工确认' LIMIT 1",
                    (job_id,),
                )
                if job["status"] not in {"published", "platform_draft", "failed"} or not linked_waiting_task:
                    raise
            self._postprocess_publish_job_result(job)
            self._synchronize_linked_waiting_tasks(job)
            return job
        except LookupError as exc:
            raise NotFoundError(str(exc)) from exc

    def _synchronize_linked_waiting_tasks(self, job: dict[str, Any]) -> None:
        self._synchronize_linked_tasks(job, "待人工确认", "待人工确认")

    def _synchronize_linked_tasks(
        self,
        job: dict[str, Any],
        expected_task_status: str,
        expected_run_status: str,
    ) -> None:
        if job["status"] not in {
            "published",
            "platform_draft",
            "awaiting_confirmation",
            "failed",
        }:
            return
        tasks = self.db.fetch_all(
            "SELECT * FROM scheduled_tasks WHERE publish_job_id = ? AND status = ?",
            (job["id"], expected_task_status),
        )
        for task in tasks:
            run = self.db.fetch_one(
                """
                SELECT * FROM task_runs
                WHERE task_id = ? AND publish_job_id = ? AND status = ?
                ORDER BY id DESC LIMIT 1
                """,
                (task["id"], job["id"], expected_run_status),
            )
            if not run:
                continue
            package = self.db.fetch_one(
                "SELECT id FROM publish_packages WHERE publish_job_id = ? ORDER BY id DESC LIMIT 1",
                (job["id"],),
            )
            package_id = int(package["id"]) if package else None
            self._finish_task_from_publish_job(
                task,
                int(run["id"]),
                int(task.get("content_project_id") or job["project_id"]),
                package_id,
                job,
                datetime.now(),
                expected_task_status=expected_task_status,
                expected_run_status=expected_run_status,
            )

    def _validate_task_publish_target(self, task: dict[str, Any]) -> dict[str, Any]:
        if task["platform"] == "微信公众号":
            if task["task_type"] != TASK_TYPE_WECHAT:
                raise RuntimeError("微信公众号任务类型必须是微信公众号图文生成")
        elif task["platform"] == "小红书":
            if task["task_type"] not in {TASK_TYPE_XIAOHONGSHU, TASK_TYPE_XIAOHONGSHU_RESERVED}:
                raise RuntimeError("小红书任务类型必须是小红书图文生成")
        else:
            raise RuntimeError("V1 定时任务真实发布仅支持微信公众号和小红书图文")
        if not task.get("account_id"):
            raise RuntimeError(f"请选择一个已授权的{task['platform']}账号")
        account = self.get_account(int(task["account_id"]))
        if account["platform"] != task["platform"] or account["auth_status"] != "已授权":
            raise RuntimeError(f"请选择一个已授权的{task['platform']}账号")
        if account["platform"] == "小红书":
            login_status = str(account.get("login_status") or "在线")
            if login_status != "在线":
                if str(account.get("profile_path") or "").strip():
                    self._trace_xiaohongshu_publish_preflight(account, "preflight_start")
                    try:
                        self._verify_xiaohongshu_publish_login(account)
                    except Exception as exc:
                        detail = self._redact_account_diagnostic(
                            self._root_exception_message(exc)
                        )
                        self._trace_xiaohongshu_publish_preflight(
                            account,
                            "preflight_failed",
                            error=detail,
                        )
                        self.db.execute(
                            """
                            UPDATE platform_accounts
                            SET login_status = '离线', sync_status = '同步失败',
                                sync_error = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (detail, int(account["id"])),
                        )
                        raise RuntimeError(f"小红书账号离线，发布页预检失败：{detail}") from exc
                    self._trace_xiaohongshu_publish_preflight(account, "preflight_success")
                    self._mark_account_publish_login_online(int(account["id"]))
                    account = self.get_account(int(account["id"]))
                else:
                    raise RuntimeError("小红书账号离线，请重新扫码登录")
        return account

    def _trace_xiaohongshu_publish_preflight(
        self, account: dict[str, Any], event: str, **fields: Any
    ) -> None:
        log_dir = self.upload_dir.parent / "logs" / "auth"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
                "event": event,
                "platform": "小红书",
                "account_id": int(account.get("id") or 0),
                "profile_suffix": self._profile_suffix(account.get("profile_path")),
                "storage_state_suffix": self._profile_suffix(account.get("storage_state_path")),
                "storage_state_exists": Path(str(account.get("storage_state_path") or "")).is_file(),
                "login_status": str(account.get("login_status") or ""),
                "sync_status": str(account.get("sync_status") or ""),
                **{
                    key: self._redact_account_diagnostic(str(value))
                    for key, value in fields.items()
                },
            }
            log_path = log_dir / f"xiaohongshu-preflight-{datetime.now().strftime('%Y%m%d')}.jsonl"
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            return

    @staticmethod
    def _profile_suffix(profile_path: Any) -> str:
        value = str(profile_path or "").strip()
        if not value:
            return "no_profile"
        return value[-8:]

    @staticmethod
    def _root_exception_message(exc: Exception) -> str:
        current: BaseException = exc
        while current.__cause__ is not None:
            current = current.__cause__
        return str(current) or str(exc) or "未知错误"

    def _mark_account_publish_login_online(self, account_id: int) -> None:
        self.db.execute(
            """
            UPDATE platform_accounts
            SET login_status = '在线', sync_status = '待同步', sync_error = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (account_id,),
        )

    def _resolve_publish_account(
        self, platform: str, account_name: str, account_id: int | None
    ) -> dict[str, Any]:
        if account_id is not None:
            return self.get_account(int(account_id))
        normalized_platform = _normalize_platform(platform)
        account = self.db.fetch_one(
            """
            SELECT * FROM platform_accounts
            WHERE platform = ? AND auth_status = '已授权' AND name = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (normalized_platform, account_name),
        )
        if not account:
            raise RuntimeError("请选择一个已授权的平台账号")
        return self.get_account(int(account["id"]))

    def _layout_title_from_blocks(self, blocks: list[dict[str, Any]]) -> str:
        markdown_block = next(
            (
                block
                for block in blocks
                if str(block.get("section_type", "")).strip() == "markdown"
            ),
            None,
        )
        if markdown_block:
            title = self._markdown_title(str(markdown_block.get("content") or ""))
            if title:
                return title
        title_block = next(
            (
                block
                for block in blocks
                if str(block.get("section_type", "")).strip() == "title"
                and str(block.get("content") or "").strip()
            ),
            None,
        )
        return str(title_block.get("content") or "").strip() if title_block else ""

    @staticmethod
    def _markdown_title(markdown: str) -> str:
        for raw_line in markdown.splitlines():
            line = raw_line.strip()
            if line.startswith("# "):
                return line[2:].strip()
        return ""

    def _build_publish_payload(self, project: dict[str, Any], account_name: str) -> dict[str, Any]:
        platform = str(project.get("platform") or "微信公众号")
        if platform == "微信公众号":
            return self._build_wechat_publish_payload(project, account_name)
        if platform == "小红书":
            return self._build_xiaohongshu_publish_payload(project, account_name)
        raise RuntimeError(f"{platform}暂未接入真实发布")

    def _build_xiaohongshu_publish_payload(self, project: dict[str, Any], account_name: str) -> dict[str, Any]:
        assets = {int(asset["id"]): asset for asset in project["media_assets"]}
        markdown_block = next(
            (
                block
                for block in project["layout_blocks"]
                if block["section_type"] == "markdown" and str(block["content"] or "").strip()
            ),
            None,
        )
        if markdown_block:
            title, image_paths, note = self._parse_xhs_markdown_layout(
                str(markdown_block["content"]),
                project["title"],
                assets,
            )
        else:
            title = str(project["title"] or "").strip()
            image_paths = self._xhs_image_paths_from_layout(project, assets)
            note_parts = [
                str(block.get("content") or "").strip()
                for block in project["layout_blocks"]
                if str(block.get("section_type") or "").strip() != "title"
                and str(block.get("content") or "").strip()
            ]
            if not note_parts:
                note_parts = [
                    str(block.get("content") or "").strip()
                    for block in project["article_blocks"]
                    if str(block.get("content") or "").strip()
                ]
            note = "\n\n".join(note_parts)
        if not image_paths:
            image_paths = self._selected_xhs_image_paths(project)
        return {
            "title": title[:20] or str(project["title"] or "").strip()[:20],
            "note": note,
            "tags": self._extract_xhs_tags(note),
            "image_paths": image_paths,
            "account_name": account_name,
        }

    def _parse_xhs_markdown_layout(
        self,
        markdown: str,
        fallback_title: str,
        assets: dict[int, dict[str, Any]],
    ) -> tuple[str, list[str], str]:
        title = ""
        image_paths: list[str] = []
        note_lines: list[str] = []
        for raw_line in markdown.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            image_sources = [match.strip() for match in MARKDOWN_IMAGE_RE.findall(line)]
            for image_source in image_sources:
                asset_match = re.fullmatch(r"asset:(\d+)", image_source)
                if asset_match:
                    asset = assets.get(int(asset_match.group(1)))
                    if asset:
                        image_path = self._xiaohongshu_supported_asset_path(asset)
                        if image_path:
                            image_paths.append(image_path)
                elif not image_source.startswith(("http://", "https://", "data:")):
                    image_path = self._xiaohongshu_supported_local_path(image_source)
                    if image_path:
                        image_paths.append(image_path)
            text = MARKDOWN_IMAGE_RE.sub("", line).strip()
            if not text:
                continue
            if text.startswith("# ") and not title:
                title = text[2:].strip()
                continue
            note_lines.append(self._markdown_line_to_block(text)[1])
        return title or fallback_title, list(dict.fromkeys(image_paths)), "\n\n".join(note_lines)

    def _xhs_image_paths_from_layout(
        self,
        project: dict[str, Any],
        assets: dict[int, dict[str, Any]],
    ) -> list[str]:
        ordered_assets: list[dict[str, Any]] = []
        cover = next(
            (
                asset
                for asset in project["media_assets"]
                if asset["asset_type"] == "cover" and int(asset["selected"]) == 1
            ),
            None,
        )
        if cover:
            ordered_assets.append(cover)
        for block in project["layout_blocks"]:
            asset = assets.get(int(block["media_asset_id"])) if block.get("media_asset_id") else None
            if asset and int(asset["selected"]) == 1 and asset not in ordered_assets:
                ordered_assets.append(asset)
        for asset in project["media_assets"]:
            if int(asset["selected"]) == 1 and asset not in ordered_assets:
                ordered_assets.append(asset)
        image_paths = self._xiaohongshu_image_paths_from_assets(ordered_assets)
        if image_paths:
            return image_paths
        return self._fallback_xiaohongshu_image_paths(project)

    def _selected_xhs_image_paths(self, project: dict[str, Any]) -> list[str]:
        selected = [
            asset
            for asset in project["media_assets"]
            if int(asset["selected"]) == 1
        ]
        selected.sort(key=lambda asset: (0 if asset["asset_type"] == "cover" else 1, int(asset["id"])))
        image_paths = self._xiaohongshu_image_paths_from_assets(selected)
        if image_paths:
            return image_paths
        return self._fallback_xiaohongshu_image_paths(project)

    def _fallback_xiaohongshu_image_paths(self, project: dict[str, Any]) -> list[str]:
        assets = sorted(
            project["media_assets"],
            key=lambda asset: (
                0 if asset["asset_type"] == "cover" else 1,
                0 if int(asset["selected"]) == 1 else 1,
                int(asset["id"]),
            ),
        )
        return self._xiaohongshu_image_paths_from_assets(assets)

    def _xiaohongshu_image_paths_from_assets(self, assets: list[dict[str, Any]]) -> list[str]:
        image_paths: list[str] = []
        for asset in assets:
            image_path = self._xiaohongshu_supported_asset_path(asset)
            if image_path and image_path not in image_paths:
                image_paths.append(image_path)
        return image_paths

    def _xiaohongshu_supported_asset_path(self, asset: dict[str, Any]) -> str:
        return self._xiaohongshu_supported_local_path(str(self._media_file_path(asset)))

    def _xiaohongshu_supported_local_path(self, image_path: str) -> str:
        path = Path(str(image_path)).expanduser()
        if not path.is_absolute() and path.parts and path.parts[0] == "uploads":
            path = self.upload_dir / path.name
        if path.suffix.lower() not in XIAOHONGSHU_SUPPORTED_IMAGE_SUFFIXES:
            return ""
        if not path.is_file():
            return ""
        return str(path)

    @staticmethod
    def _extract_xhs_tags(note: str) -> list[str]:
        tags = []
        for match in re.findall(r"#([\w\u4e00-\u9fff-]{1,30})", note):
            cleaned = match.strip().lstrip("#")
            if cleaned and cleaned not in tags:
                tags.append(cleaned)
        return tags[:10]

    def _build_wechat_publish_payload(self, project: dict[str, Any], account_name: str) -> dict[str, Any]:
        assets = {int(asset["id"]): asset for asset in project["media_assets"]}
        cover = next(
            (
                asset
                for asset in project["media_assets"]
                if asset["asset_type"] == "cover" and int(asset["selected"]) == 1
            ),
            None,
        )
        markdown_block = next(
            (
                block
                for block in project["layout_blocks"]
                if block["section_type"] == "markdown" and str(block["content"] or "").strip()
            ),
            None,
        )
        if markdown_block:
            return self._build_markdown_wechat_publish_payload(
                project,
                account_name,
                str(markdown_block["content"]),
                assets,
                None,
            )

        blocks = []
        pending_intro_image_paths: list[str] = []
        for block in project["layout_blocks"]:
            section_type = str(block["section_type"] or "").strip()
            if section_type == "title":
                continue
            asset = assets.get(int(block["media_asset_id"])) if block.get("media_asset_id") else None
            image_path = ""
            if asset:
                if asset["asset_type"] == "cover":
                    cover = cover or asset
                else:
                    image_path = str(self._media_file_path(asset))
            image_paths: list[str] = []
            if section_type == "intro" and image_path:
                pending_intro_image_paths.append(image_path)
            else:
                if section_type == "body" and pending_intro_image_paths:
                    image_paths.extend(pending_intro_image_paths)
                    pending_intro_image_paths = []
                if image_path:
                    image_paths.append(image_path)
            blocks.append(
                {
                    "type": section_type,
                    "content": block["content"],
                    "image_path": image_paths[0] if image_paths else "",
                    "image_paths": image_paths,
                }
            )
        if not blocks:
            blocks = [
                {"type": block["type"], "content": block["content"], "image_path": "", "image_paths": []}
                for block in project["article_blocks"]
            ]
        digest = next((block["content"] for block in project["article_blocks"] if block["type"] == "intro"), "")
        cover_path = str(self._media_file_path(cover)) if cover else ""
        return {
            "title": project["title"],
            "author": account_name,
            "digest": digest[:120],
            "cover_path": cover_path,
            "cover_candidate_path": cover_path,
            "blocks": blocks,
        }

    def _build_markdown_wechat_publish_payload(
        self,
        project: dict[str, Any],
        account_name: str,
        markdown: str,
        assets: dict[int, dict[str, Any]],
        fallback_cover: dict[str, Any] | None,
    ) -> dict[str, Any]:
        title, cover, blocks = self._parse_markdown_layout(markdown, assets, fallback_cover)
        if not blocks:
            blocks = [
                {"type": block["type"], "content": block["content"], "image_path": "", "image_paths": []}
                for block in project["article_blocks"]
            ]
        digest = next((block["content"] for block in blocks if block["type"] == "intro"), "")
        if not digest:
            digest = next((block["content"] for block in project["article_blocks"] if block["type"] == "intro"), "")
        cover_path = str(self._media_file_path(cover)) if cover else ""
        return {
            "title": title or project["title"],
            "author": account_name,
            "digest": digest[:120],
            "cover_path": cover_path,
            "cover_candidate_path": cover_path,
            "blocks": blocks,
        }

    def _parse_markdown_layout(
        self,
        markdown: str,
        assets: dict[int, dict[str, Any]],
        fallback_cover: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
        title = ""
        cover = fallback_cover
        blocks: list[dict[str, Any]] = []
        pending_image_paths: list[str] = []

        for raw_line in markdown.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            image_sources = [match.strip() for match in MARKDOWN_IMAGE_RE.findall(line)]
            line_without_images = MARKDOWN_IMAGE_RE.sub("", line).strip()
            for image_source in image_sources:
                asset_match = re.fullmatch(r"asset:(\d+)", image_source)
                if asset_match:
                    asset = assets.get(int(asset_match.group(1)))
                    if not asset:
                        continue
                    if asset["asset_type"] == "cover":
                        cover = asset
                        continue
                    image_path = str(self._media_file_path(asset))
                else:
                    image_path = image_source
                if blocks and not line_without_images:
                    blocks[-1].setdefault("image_paths", []).append(image_path)
                    if not blocks[-1].get("image_path"):
                        blocks[-1]["image_path"] = image_path
                else:
                    pending_image_paths.append(image_path)

            if not line_without_images:
                continue

            block_type, content = self._markdown_line_to_block(line_without_images)
            if not content:
                continue
            if block_type == "title":
                title = content
                continue
            blocks.append(
                {
                    "type": block_type,
                    "content": content,
                    "image_path": pending_image_paths[0] if pending_image_paths else "",
                    "image_paths": pending_image_paths,
                }
            )
            pending_image_paths = []

        return title, cover, blocks

    @staticmethod
    def _markdown_line_to_block(line: str) -> tuple[str, str]:
        if line.startswith("# "):
            return "title", line[2:].strip()
        if line.startswith("### "):
            return "heading", line[4:].strip()
        if line.startswith("## "):
            return "heading", line[3:].strip()
        if line.startswith(">"):
            return "intro", line.lstrip(">").strip()
        if line.startswith("- ") or line.startswith("* "):
            return "body", f"• {line[2:].strip()}"
        if re.match(r"^\d+\.\s+", line):
            return "body", re.sub(r"^\d+\.\s+", "", line).strip()
        return "body", line.strip()

    def _media_file_path(self, asset: dict[str, Any]) -> Path:
        path = Path(str(asset["local_path"]))
        if path.is_absolute():
            return path
        if path.parts and path.parts[0] == "uploads":
            return self.upload_dir / path.name
        return self.upload_dir / path

    def get_article_blocks(self, project_id: int) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT id, block_type AS type, label, content, sort_order
            FROM article_blocks
            WHERE project_id = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (project_id,),
        )

    def get_media_assets(self, project_id: int) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT id, asset_type, url, local_path, prompt, selected, created_at
            FROM media_assets
            WHERE project_id = ?
            ORDER BY asset_type ASC, id ASC
            """,
            (project_id,),
        )

    def get_layout_blocks(self, project_id: int) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT id, section_type, content, media_asset_id, sort_order
            FROM layout_blocks
            WHERE project_id = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (project_id,),
        )

    def _get_project_only(self, project_id: int) -> dict[str, Any]:
        project = self.db.fetch_one("SELECT * FROM content_projects WHERE id = ?", (project_id,))
        if not project:
            raise NotFoundError("内容项目不存在")
        return project

    def _set_stage(self, project_id: int, stage: str) -> None:
        self.db.execute(
            "UPDATE content_projects SET current_stage = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (stage, project_id),
        )

    def _persist_account_sync(self, account_id: int, data: dict[str, Any]) -> None:
        if data.get("source") != "browser":
            raise RuntimeError("同步数据来源不是已登录平台")
        account = self.get_account(account_id)
        observed_name = str(data.get("account_name") or "").strip()
        observed_external_id = str(data.get("external_id") or "").strip()
        expected_external_id = str(account.get("external_id") or "").strip()
        stable_id_matches = bool(
            expected_external_id
            and observed_external_id
            and expected_external_id == observed_external_id
        )
        if account["platform"] == "微信公众号" and not stable_id_matches:
            raise RuntimeError("同步页面账号身份与所选账号不一致")
        if expected_external_id and observed_external_id and not stable_id_matches:
            raise RuntimeError("同步页面账号身份与所选账号不一致")
        if not stable_id_matches:
            expected_name = unicodedata.normalize(
                "NFKC", str(account.get("name") or "")
            ).strip()
            live_name = unicodedata.normalize("NFKC", observed_name).strip()
            if not expected_name or not live_name or expected_name != live_name:
                raise RuntimeError("同步页面账号身份与所选账号不一致")
        account_name = observed_name or str(account["name"])
        external_id = observed_external_id or expected_external_id
        now = datetime.now()
        today = datetime.now().date().isoformat()
        metrics = data.get("metrics") or {}
        metric_values = {
            key: max(0, int(metrics.get(key) or 0))
            for key in ("fans", "reads", "recommends", "likes", "comments", "shares", "favorites")
        }
        self.db.execute(
            "DELETE FROM account_metric_snapshots WHERE account_id = ? AND metric_date = ?",
            (account_id, today),
        )
        self.db.execute(
            """
            INSERT INTO account_metric_snapshots (
              account_id, metric_date, fans, reads, recommends, likes, comments, shares, favorites
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                today,
                metric_values["fans"],
                metric_values["reads"],
                metric_values["recommends"],
                metric_values["likes"],
                metric_values["comments"],
                metric_values["shares"],
                metric_values["favorites"],
            ),
        )
        for work in data.get("works") or []:
            title = str(work.get("title") or "").strip()
            if not title:
                continue
            publish_url = str(work.get("publish_url") or "")
            lookup_sql = "SELECT id FROM platform_works WHERE account_id = ? AND publish_url = ?" if publish_url else "SELECT id FROM platform_works WHERE account_id = ? AND title = ?"
            lookup_value = publish_url or title
            existing = self.db.fetch_one(
                lookup_sql,
                (account_id, lookup_value),
            )
            if existing:
                work_id = int(existing["id"])
                self.db.execute(
                    """
                    UPDATE platform_works
                    SET account_name = ?, title = ?, content_type = ?, status = ?, publish_url = ?,
                        published_at = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        account_name,
                        title,
                        str(work.get("content_type") or "图文"),
                        str(work.get("status") or "已发布"),
                        publish_url,
                        str(work.get("published_at") or ""),
                        work_id,
                    ),
                )
            else:
                work_id = self.db.execute(
                    """
                    INSERT INTO platform_works (
                      account_id, platform, account_name, title, content_type, status, publish_url, published_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        account["platform"],
                        account_name,
                        title,
                        str(work.get("content_type") or "图文"),
                        str(work.get("status") or "已发布"),
                        publish_url,
                        str(work.get("published_at") or ""),
                    ),
                )
            self.db.execute(
                "DELETE FROM work_metric_snapshots WHERE work_id = ? AND metric_date = ?",
                (work_id, today),
            )
            self.db.execute(
                """
                INSERT INTO work_metric_snapshots (
                  work_id, metric_date, reads, recommends, likes, comments, shares, favorites
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    work_id,
                    today,
                    max(0, int(work.get("reads") or 0)),
                    max(0, int(work.get("recommends") or 0)),
                    max(0, int(work.get("likes") or 0)),
                    max(0, int(work.get("comments") or 0)),
                    max(0, int(work.get("shares") or 0)),
                    max(0, int(work.get("favorites") or 0)),
                ),
            )
        self.db.execute(
            """
            UPDATE platform_accounts
            SET name = ?, external_id = ?, login_status = '在线', sync_status = '已同步', sync_error = '', last_sync_at = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (account_name, external_id, _format_task_time(now), account_id),
        )

    def _postprocess_publish_job_result(self, job: dict[str, Any]) -> None:
        if job["status"] == "failed":
            self._mark_account_offline_after_publish_login_failure(job)
            return
        if job["status"] not in {"platform_draft", "published"}:
            return
        project = self.get_project(int(job["project_id"]))
        account = self.get_account(int(job["account_id"]))
        self._postprocess_publish_package(project, account, job)

    def _mark_account_offline_after_publish_login_failure(self, job: dict[str, Any]) -> None:
        if job.get("platform") != "小红书":
            return
        reason = str(job.get("failure_reason") or "")
        if not any(marker in reason for marker in ("登录态失效", "重新扫码", "redirectReason=401", "/login")):
            return
        self.db.execute(
            """
            UPDATE platform_accounts
            SET login_status = '离线', sync_status = '同步失败', sync_error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (reason or "小红书登录态失效，请重新扫码登录", int(job["account_id"])),
        )

    def _create_work_from_project(
        self,
        account_id: int,
        platform: str,
        account_name: str,
        project: dict[str, Any],
        status: str = "已生成平台草稿",
        publish_url: str = "",
    ) -> None:
        content_type = "笔记" if platform == "小红书" else "图文"
        existing = self.db.fetch_one(
            """
            SELECT id FROM platform_works
            WHERE account_id = ? AND publish_url = ? AND publish_url <> ''
            LIMIT 1
            """,
            (account_id, publish_url),
        )
        if existing:
            return
        work_id = self.db.execute(
            """
            INSERT INTO platform_works (
              account_id, platform, account_name, title, content_type, status, publish_url, published_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                platform,
                account_name,
                project["title"],
                content_type,
                status,
                publish_url,
                _format_task_time(datetime.now()),
            ),
        )
        self.db.execute(
            """
            INSERT INTO work_metric_snapshots (work_id, metric_date)
            VALUES (?, ?)
            """,
            (work_id, datetime.now().date().isoformat()),
        )


def _parse_task_time(value: str) -> datetime:
    normalized = value.strip().replace("T", " ")
    if len(normalized) == 16:
        return datetime.strptime(normalized, TASK_TIME_FORMAT)
    parsed = datetime.fromisoformat(normalized)
    return parsed.replace(second=0, microsecond=0)


def _is_clock_time(value: str) -> bool:
    try:
        datetime.strptime(value.strip(), "%H:%M")
    except ValueError:
        return False
    return True


def _next_repeat_clock_time(value: str, repeat_rule: str, weekly_day: Any) -> datetime:
    clock = datetime.strptime(value.strip(), "%H:%M")
    now = datetime.now().replace(second=0, microsecond=0)
    next_time = now.replace(hour=clock.hour, minute=clock.minute)
    if repeat_rule == "weekly":
        target_day = int(weekly_day if weekly_day not in {None, ""} else now.weekday())
        days = (target_day - now.weekday()) % 7
        next_time += timedelta(days=days)
        if next_time <= now:
            next_time += timedelta(days=7)
        return next_time
    if next_time <= now:
        next_time += timedelta(days=1)
    return next_time


def _format_task_time(value: datetime) -> str:
    return value.replace(second=0, microsecond=0).strftime(TASK_TIME_FORMAT)


def _normalize_platform(value: str) -> str:
    platform = PLATFORM_ALIASES.get(value.strip(), value.strip())
    if platform not in PLATFORMS:
        raise RuntimeError("平台暂不支持")
    return platform


def _default_tag(platform: str) -> str:
    return {
        "微信公众号": "图文运营",
        "视频号": "视频内容",
        "小红书": "种草笔记",
        "抖音": "短视频",
    }.get(platform, "新媒体")


def _default_role(platform: str) -> str:
    return {
        "微信公众号": "公众号图文内容号",
        "视频号": "视频号内容账号",
        "小红书": "小红书种草账号",
        "抖音": "抖音短视频账号",
    }.get(platform, "新媒体账号")


def _default_audience(platform: str) -> str:
    return {
        "微信公众号": "企业老板、管理者、运营负责人",
        "视频号": "微信生态内的潜在客户",
        "小红书": "搜索种草用户和决策前用户",
        "抖音": "短视频信息流用户",
    }.get(platform, "目标客户")


def _default_style(platform: str) -> str:
    return {
        "微信公众号": "专业、案例型、易懂",
        "视频号": "口语化、场景化、节奏清晰",
        "小红书": "清单型、强标题、强封面",
        "抖音": "短句、冲突感、前三秒抓人",
    }.get(platform, "清晰、有转化感")
