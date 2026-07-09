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


class NotFoundError(RuntimeError):
    pass


class ContentService:
    def __init__(self, db: Database, ai_client: Any | None = None, upload_dir: str | Path = "backend/uploads"):
        self.db = db
        self.ai_client = ai_client or OpenAICompatibleClient()
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)

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
        draft = self.db.fetch_one("SELECT * FROM draft_snapshots WHERE project_id = ?", (project_id,))
        return draft or {}

    def list_drafts(self, start_date: str = "", end_date: str = "") -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if start_date:
            clauses.append("date(updated_at) >= date(?)")
            params.append(start_date)
        if end_date:
            clauses.append("date(updated_at) <= date(?)")
            params.append(end_date)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.db.fetch_all(
            f"""
            SELECT id, project_id, title, platform, stage, restore_stage, updated_at
            FROM draft_snapshots
            {where}
            ORDER BY updated_at DESC, id DESC
            """,
            tuple(params),
        )

    def get_draft(self, draft_id: int) -> dict[str, Any]:
        draft = self.db.fetch_one("SELECT * FROM draft_snapshots WHERE id = ?", (draft_id,))
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

    def list_tasks(self, status: str = "", platform: str = "", q: str = "") -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if status and status != "全部状态":
            clauses.append("status = ?")
            params.append(status)
        if platform and platform != "全部平台":
            clauses.append("platform = ?")
            params.append(platform)
        if q:
            clauses.append("(topic LIKE ? OR account_name LIKE ?)")
            keyword = f"%{q}%"
            params.extend([keyword, keyword])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.db.fetch_all(
            f"""
            SELECT id, platform, account_name, execute_time, task_type, topic, article_requirements, selected_title, title_direction, status,
                   execution_mode, repeat_rule, weekly_day, next_run_at, failure_stage, failure_reason,
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
              platform, account_name, execute_time, task_type, topic, article_requirements, selected_title, title_direction, status,
              execution_mode, repeat_rule, weekly_day, next_run_at, failure_stage, failure_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized["platform"],
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
            SET platform = ?, account_name = ?, execute_time = ?, task_type = ?, topic = ?, article_requirements = ?,
                selected_title = ?, title_direction = ?, status = '待执行',
                execution_mode = ?, repeat_rule = ?, weekly_day = ?,
                next_run_at = ?, failure_stage = '', failure_reason = '',
                content_project_id = NULL, publish_package_id = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                normalized["platform"],
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
        try:
            if task["task_type"] not in {TASK_TYPE_WECHAT, TASK_TYPE_WECHAT_LEGACY}:
                raise RuntimeError("小红书图文生成暂未开放真实执行")
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
                platform="微信公众号",
            )
            article = self.generate_article(project["id"])
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
            self.save_layout(project["id"], layout_blocks)
            package = self.create_publish_package(project["id"], platform="微信公众号", account_name=task["account_name"])
            self._finish_task_success(task, run_id, project["id"], package["id"], current)
        except Exception as exc:
            stage = "任务类型未开放" if task["task_type"] == TASK_TYPE_XIAOHONGSHU_RESERVED else "执行失败"
            self._finish_task_failure(task_id, run_id, stage, str(exc), current)
        return self.get_task(task_id)

    def _recommend_task_title(self, task: dict[str, Any]) -> dict[str, str]:
        prompt = (
            "这是任务中心定时任务，请模拟内容创作第一步“标题确认”。"
            f"平台：微信公众号；账号：{task['account_name']}；"
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
        status = "成功"
        if task["execution_mode"] == "repeat":
            status = "待执行"
            next_run_at = self._next_repeat_time(task, now)
        self.db.execute(
            """
            UPDATE scheduled_tasks
            SET status = ?, next_run_at = ?, failure_stage = '', failure_reason = '',
                content_project_id = ?, publish_package_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, next_run_at, project_id, package_id, task["id"]),
        )
        self.db.execute(
            """
            UPDATE task_runs
            SET status = '成功', finished_at = ?, content_project_id = ?, publish_package_id = ?
            WHERE id = ?
            """,
            (finished_at, project_id, package_id, run_id),
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
            SET status = '失败', failure_stage = ?, failure_reason = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (stage, reason, task_id),
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
            "platform": str(payload.get("platform", "公众号")),
            "account_name": str(payload.get("account_name", "")),
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

    def create_publish_package(self, project_id: int, platform: str, account_name: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        payload = {
            "project": project,
            "article_blocks": project["article_blocks"],
            "media_assets": project["media_assets"],
            "layout_blocks": project["layout_blocks"],
        }
        package_id = self.db.execute(
            """
            INSERT INTO publish_packages (project_id, platform, account_name, payload_json, status)
            VALUES (?, ?, ?, ?, 'pending_publish')
            """,
            (project_id, platform, account_name, json.dumps(payload, ensure_ascii=False)),
        )
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
