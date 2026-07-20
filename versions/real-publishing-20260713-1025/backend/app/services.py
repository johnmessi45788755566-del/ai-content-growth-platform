import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .ai import OpenAICompatibleClient, TopicRecommendation
from .database import Database


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
TASK_TYPE_XIAOHONGSHU_RESERVED = "小红书图文生成（预留）"
TASK_TIME_FORMAT = "%Y-%m-%d %H:%M"
PLATFORMS = ["微信公众号", "视频号", "小红书", "抖音"]
PLATFORM_ALIASES = {
    "公众号": "微信公众号",
    "微信公众平台": "微信公众号",
    "微信公众号": "微信公众号",
    "视频号": "视频号",
    "小红书": "小红书",
    "抖音": "抖音",
}


class NotFoundError(RuntimeError):
    pass


class ContentService:
    def __init__(
        self,
        db: Database,
        ai_client: Any | None = None,
        upload_dir: str | Path = "backend/uploads",
        browser_auth_manager: Any | None = None,
    ):
        self.db = db
        self.ai_client = ai_client or OpenAICompatibleClient()
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.browser_auth_manager = browser_auth_manager

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
        blocks = self.ai_client.generate_article(project["title"], project["title_direction"])
        if not blocks:
            raise RuntimeError("模型未返回文章段落")
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
            generated = self.ai_client.generate_image(prompt, asset_type)
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

        self._set_stage(project_id, "images")
        self.autosave(project_id, stage="images", restore_stage="layout")
        return {
            "project": self.get_project(project_id),
            "assets": self.get_media_assets(project_id),
            "generated_assets": assets,
        }

    def save_layout(self, project_id: int, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        self.get_project(project_id)
        self.db.execute("DELETE FROM layout_blocks WHERE project_id = ?", (project_id,))
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

    def start_account_auth(self, platform: str, account_name: str = "") -> dict[str, Any]:
        normalized_platform = _normalize_platform(platform)
        if not self.browser_auth_manager:
            raise RuntimeError("真实浏览器登录服务未配置")
        return self.browser_auth_manager.start(
            normalized_platform,
            account_name,
            self._complete_account_auth,
        )

    def get_account_auth_status(self, session_id: str) -> dict[str, Any]:
        if not self.browser_auth_manager:
            raise RuntimeError("真实浏览器登录服务未配置")
        return self.browser_auth_manager.get_status(session_id)

    def cancel_account_auth(self, session_id: str) -> dict[str, Any]:
        if not self.browser_auth_manager:
            raise RuntimeError("真实浏览器登录服务未配置")
        return self.browser_auth_manager.cancel(session_id)

    def _complete_account_auth(self, result: dict[str, Any]) -> dict[str, Any]:
        normalized_platform = _normalize_platform(result["platform"])
        name = str(result.get("account_name") or f"{normalized_platform}账号").strip()
        external_id = str(result.get("external_id") or "")
        profile_path = str(result.get("profile_path") or "")
        login_url = str(result.get("login_url") or "")
        sync_data = result.get("sync_data")
        sync_error = str(result.get("sync_error") or "")
        existing = None
        if external_id:
            existing = self.db.fetch_one(
                "SELECT id FROM platform_accounts WHERE platform = ? AND external_id = ?",
                (normalized_platform, external_id),
            )
        if not existing and profile_path:
            existing = self.db.fetch_one(
                "SELECT id FROM platform_accounts WHERE profile_path = ?",
                (profile_path,),
            )
        if not existing and "信息待完善" not in name:
            existing = self.db.fetch_one(
                "SELECT id FROM platform_accounts WHERE platform = ? AND name = ?",
                (normalized_platform, name),
            )
        if existing:
            self.db.execute(
                """
                UPDATE platform_accounts
                SET name = ?, external_id = ?, auth_status = '已授权', monitor_status = '监测中', sync_status = '待同步',
                    sync_error = '',
                    auth_method = '真实浏览器扫码', login_url = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (name, external_id, login_url, existing["id"]),
            )
            account_id = int(existing["id"])
        else:
            account_id = self.db.execute(
                """
                INSERT INTO platform_accounts (
                  platform, name, external_id, owner, tag, auth_status, monitor_status, sync_status,
                  role, audience, style, taboo, preference, auth_method, profile_path, login_url
                )
                VALUES (?, ?, ?, '未分配', '', '已授权', '监测中', '待同步', ?, ?, ?, ?, ?,
                        '真实浏览器扫码', ?, ?)
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
                    login_url,
                ),
            )
        if isinstance(sync_data, dict):
            self._persist_account_sync(account_id, sync_data)
        elif sync_error:
            self.db.execute(
                "UPDATE platform_accounts SET sync_status = '同步失败', sync_error = ? WHERE id = ?",
                (sync_error, account_id),
            )
        return self.get_account(account_id)

    def list_accounts(self, platform: str = "", auth_status: str = "", q: str = "") -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if platform and platform != "全部平台":
            clauses.append("platform = ?")
            params.append(_normalize_platform(platform))
        if auth_status and auth_status != "全部授权状态":
            clauses.append("auth_status = ?")
            params.append(auth_status)
        if q:
            clauses.append("(name LIKE ? OR tag LIKE ? OR owner LIKE ?)")
            keyword = f"%{q}%"
            params.extend([keyword, keyword, keyword])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.db.fetch_all(
            f"""
            SELECT id, platform, name, external_id, owner, tag, auth_status, monitor_status, sync_status, sync_error,
                   role, audience, style, taboo, preference, auth_method, qr_url, profile_path, login_url,
                   last_sync_at, created_at, updated_at
            FROM platform_accounts
            {where}
            ORDER BY updated_at DESC, id DESC
            """,
            tuple(params),
        )

    def get_account(self, account_id: int) -> dict[str, Any]:
        account = self.db.fetch_one(
            """
            SELECT id, platform, name, external_id, owner, tag, auth_status, monitor_status, sync_status, sync_error,
                   role, audience, style, taboo, preference, auth_method, qr_url, profile_path, login_url,
                   last_sync_at, created_at, updated_at
            FROM platform_accounts
            WHERE id = ?
            """,
            (account_id,),
        )
        if not account:
            raise NotFoundError("账号不存在")
        return account

    def sync_account(self, account_id: int) -> dict[str, Any]:
        account = self.get_account(account_id)
        if account["auth_status"] != "已授权":
            raise RuntimeError("账号未授权，请先完成扫码登录")
        if not self.browser_auth_manager:
            raise RuntimeError("真实浏览器同步服务未配置")
        self.db.execute(
            "UPDATE platform_accounts SET sync_status = '同步中', sync_error = '' WHERE id = ?",
            (account_id,),
        )
        try:
            data = self.browser_auth_manager.sync(account)
            self._persist_account_sync(account_id, data)
        except Exception as exc:
            error = str(exc) or "平台数据同步失败"
            self.db.execute(
                """
                UPDATE platform_accounts
                SET sync_status = '同步失败', sync_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (error, account_id),
            )
            raise RuntimeError(f"真实数据同步失败：{error}") from exc
        return {"synced": True, "source": data.get("source", "browser"), "account": self.get_account(account_id)}

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
            SET name = ?, external_id = ?, owner = ?, tag = ?, monitor_status = ?, role = ?, audience = ?, style = ?, taboo = ?,
                preference = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                str(payload.get("name") or account["name"]),
                str(payload.get("external_id") or account["external_id"]),
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
        return self.get_account(account_id)

    def delete_account(self, account_id: int) -> dict[str, Any]:
        self.get_account(account_id)
        self.db.execute("DELETE FROM platform_accounts WHERE id = ?", (account_id,))
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
        return self.db.fetch_all(
            f"""
            SELECT id, platform, account_id, account_name, execute_time, task_type, topic, article_requirements, selected_title, title_direction, status,
                   publish_result, manual_takeover_url, execution_mode, repeat_rule, weekly_day, next_run_at, failure_stage, failure_reason,
                   retry_count, content_project_id, publish_package_id, last_run_at, created_at, updated_at
            FROM scheduled_tasks
            {where}
            ORDER BY COALESCE(NULLIF(next_run_at, ''), execute_time) ASC, id DESC
            """,
            tuple(params),
        )

    def get_task(self, task_id: int) -> dict[str, Any]:
        task = self.db.fetch_one("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,))
        if not task:
            raise NotFoundError("任务不存在")
        task["runs"] = self.db.fetch_all(
            """
            SELECT id, task_id, status, started_at, finished_at, failure_stage, failure_reason,
                   content_project_id, publish_package_id, created_at
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
        return task

    def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_task_payload(payload)
        task_id = self.db.execute(
            """
            INSERT INTO scheduled_tasks (
              platform, account_id, account_name, execute_time, task_type, topic, article_requirements, selected_title, title_direction, status,
              execution_mode, repeat_rule, weekly_day, next_run_at, failure_stage, failure_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        self.db.execute(
            """
            UPDATE scheduled_tasks
            SET platform = ?, account_id = ?, account_name = ?, execute_time = ?, task_type = ?, topic = ?, article_requirements = ?,
                selected_title = ?, title_direction = ?, status = '待执行',
                execution_mode = ?, repeat_rule = ?, weekly_day = ?,
                next_run_at = ?, failure_stage = '', failure_reason = '', publish_result = '', manual_takeover_url = '',
                content_project_id = NULL, publish_package_id = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
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
                normalized["execution_mode"],
                normalized["repeat_rule"],
                normalized["weekly_day"],
                normalized["next_run_at"],
                task_id,
            ),
        )
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
        failed_tasks = self.db.fetch_all(f"SELECT id FROM scheduled_tasks WHERE {where}", params)
        for task in failed_tasks:
            self.db.execute(
                """
                UPDATE scheduled_tasks
                SET status = '已重试', retry_count = retry_count + 1, failure_stage = '',
                    failure_reason = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (task["id"],),
            )
            self.execute_task(int(task["id"]))
        return {"updated": len(failed_tasks)}

    def execute_due_tasks(self, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now()
        due_at = _format_task_time(current)
        due_tasks = self.db.fetch_all(
            """
            SELECT id
            FROM scheduled_tasks
            WHERE status = '待执行'
              AND COALESCE(NULLIF(next_run_at, ''), execute_time) <= ?
            ORDER BY COALESCE(NULLIF(next_run_at, ''), execute_time) ASC, id ASC
            """,
            (due_at,),
        )
        for task in due_tasks:
            self.execute_task(int(task["id"]), now=current)
        return {"executed": len(due_tasks)}

    def execute_task(self, task_id: int, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now()
        started_at = _format_task_time(current)
        task = self.get_task(task_id)
        run_id = self.db.execute(
            """
            INSERT INTO task_runs (task_id, status, started_at)
            VALUES (?, '执行中', ?)
            """,
            (task_id, started_at),
        )
        self.db.execute(
            """
            UPDATE scheduled_tasks
            SET status = '执行中', last_run_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (started_at, task_id),
        )
        stage = "检查账号"
        try:
            account = self.get_account(int(task["account_id"])) if task.get("account_id") else None
            if account and account["auth_status"] != "已授权":
                raise RuntimeError("账号未授权，无法执行定时任务")
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
            self.db.execute(
                "UPDATE scheduled_tasks SET content_project_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (project["id"], task_id),
            )
            stage = "生成文章"
            article = self.generate_article(project["id"])
            stage = "生成图片"
            images = self.generate_images(project["id"])
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
            stage = "生成发布包"
            package = self.create_publish_package(
                project["id"],
                platform=task["platform"],
                account_name=task["account_name"],
                account_id=task.get("account_id"),
                status="platform_draft",
            )
            self._finish_task_success(task, run_id, project["id"], package["id"], current)
        except Exception as exc:
            failure_stage = "任务类型未开放" if task["task_type"] == TASK_TYPE_XIAOHONGSHU_RESERVED else stage
            self._finish_task_failure(task_id, run_id, failure_stage, str(exc), current)
        return self.get_task(task_id)

    def _recommend_task_title(self, task: dict[str, Any]) -> dict[str, str]:
        prompt = (
            "这是任务中心定时任务，请模拟内容创作第一步“标题确认”。"
            f"平台：{task['platform']}；账号：{task['account_name']}；"
            f"主题约束：{task['topic']}；"
            f"创作要求：{task.get('article_requirements') or '未填写'}。"
            "请结合主题约束、账号信息和创作要求，给出适合公众号图文的标题建议。"
        )
        recommendations = self.recommend_topics(prompt)
        for recommendation in recommendations:
            for title in recommendation.titles:
                if title:
                    return {"title": title, "direction": recommendation.direction}
        raise RuntimeError("AI 未返回可用标题")

    def _finish_task_success(
        self,
        task: dict[str, Any],
        run_id: int,
        project_id: int,
        package_id: int,
        now: datetime,
    ) -> None:
        finished_at = _format_task_time(now)
        next_run_at = ""
        status = "已生成平台草稿"
        publish_result = "已生成平台草稿"
        if task["execution_mode"] == "repeat":
            status = "待执行"
            next_run_at = self._next_repeat_time(task, now)
        self.db.execute(
            """
            UPDATE scheduled_tasks
            SET status = ?, next_run_at = ?, failure_stage = '', failure_reason = '',
                publish_result = ?, manual_takeover_url = '', content_project_id = ?, publish_package_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, next_run_at, publish_result, project_id, package_id, task["id"]),
        )
        self.db.execute(
            """
            UPDATE task_runs
            SET status = ?, finished_at = ?, content_project_id = ?, publish_package_id = ?
            WHERE id = ?
            """,
            (publish_result, finished_at, project_id, package_id, run_id),
        )

    def _finish_task_failure(
        self,
        task_id: int,
        run_id: int,
        stage: str,
        reason: str,
        now: datetime,
    ) -> None:
        finished_at = _format_task_time(now)
        self.db.execute(
            """
            UPDATE scheduled_tasks
            SET status = '失败', failure_stage = ?, failure_reason = ?,
                manual_takeover_url = 'manual-takeover://task/' || ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (stage, reason, task_id, task_id),
        )
        self.db.execute(
            """
            UPDATE task_runs
            SET status = '失败', finished_at = ?, failure_stage = ?, failure_reason = ?
            WHERE id = ?
            """,
            (finished_at, stage, reason, run_id),
        )

    def _normalize_task_payload(self, payload: dict[str, Any], force_pending: bool = False) -> dict[str, Any]:
        task_type = str(payload.get("task_type", TASK_TYPE_WECHAT)).strip() or TASK_TYPE_WECHAT
        if task_type == TASK_TYPE_WECHAT_LEGACY:
            task_type = TASK_TYPE_WECHAT
        platform = _normalize_platform(str(payload.get("platform", "微信公众号")))
        account_id = payload.get("account_id")
        account_name = str(payload.get("account_name", "")).strip()
        if account_id not in {None, ""}:
            account = self.get_account(int(account_id))
            platform = account["platform"]
            account_name = account["name"]
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
        status: str = "pending_publish",
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        if account_id is not None:
            account = self.get_account(int(account_id))
            if account["auth_status"] != "已授权":
                raise RuntimeError("所选账号未授权，请重新登录")
            platform = account["platform"]
            account_name = account["name"]
        payload = {
            "project": project,
            "article_blocks": project["article_blocks"],
            "media_assets": project["media_assets"],
            "layout_blocks": project["layout_blocks"],
        }
        package_id = self.db.execute(
            """
            INSERT INTO publish_packages (project_id, account_id, platform, account_name, payload_json, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (project_id, account_id, platform, account_name, json.dumps(payload, ensure_ascii=False), status),
        )
        if account_id:
            self._create_work_from_project(int(account_id), platform, account_name, project)
        self._set_stage(project_id, "publish")
        self.autosave(project_id, stage="publish", restore_stage="layout")
        package = self.db.fetch_one("SELECT * FROM publish_packages WHERE id = ?", (package_id,))
        return package or {}

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
        account_name = str(data.get("account_name") or account["name"]).strip()
        external_id = str(data.get("external_id") or account["external_id"]).strip()
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
            SET name = ?, external_id = ?, sync_status = '已同步', sync_error = '', last_sync_at = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (account_name, external_id, _format_task_time(now), account_id),
        )

    def _create_work_from_project(self, account_id: int, platform: str, account_name: str, project: dict[str, Any]) -> None:
        work_id = self.db.execute(
            """
            INSERT INTO platform_works (account_id, platform, account_name, title, content_type, status, published_at)
            VALUES (?, ?, ?, ?, '图文', '已生成平台草稿', ?)
            """,
            (account_id, platform, account_name, project["title"], _format_task_time(datetime.now())),
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
