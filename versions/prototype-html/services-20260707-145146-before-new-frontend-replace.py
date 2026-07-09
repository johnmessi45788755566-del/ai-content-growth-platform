import json
from pathlib import Path
from typing import Any

from .ai import OpenAICompatibleClient, TopicRecommendation
from .database import Database


STAGE_LABELS = {
    "title": ("标题已确认，待生成文章", "article"),
    "article": ("文章已完成，待生成图片", "images"),
    "images": ("图片已完成，待排版", "layout"),
    "layout": ("排版已完成，待发布", "publish"),
    "publish": ("待人工发布", "publish"),
}

STAGE_ORDER = ["title", "article", "images", "layout", "publish"]


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
            (project_id, block["type"], block["label"], block["content"], index + 1)
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
                f"公众号封面图，主题：{project['title']}。内容背景：{body[:500]}。风格：企业服务、简洁、专业、适合小微企业老板。",
            ),
            (
                "inline",
                f"公众号正文配图，解释文章核心观点：{project['title']}。内容背景：{body[:500]}。风格：流程图感、清晰、可读。",
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
        self.autosave(project_id, stage="layout", restore_stage="publish")
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
        self.autosave(project_id, stage="publish", restore_stage="publish")
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
