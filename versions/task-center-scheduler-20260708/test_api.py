from pathlib import Path

from fastapi.testclient import TestClient

from app.ai import GeneratedImage, TopicRecommendation
from app.main import create_app


class FakeAIClient:
    def recommend_topics(self, user_prompt: str):
        return [TopicRecommendation(direction="效率颠覆", subtitle="时间对比", titles=["8小时变5分钟"])]

    def generate_article(self, title: str, direction: str):
        return [
            {"type": "intro", "label": "开头钩子", "content": "老板开始关注 AI 记账。"},
            {"type": "pain", "label": "痛点放大", "content": "人工记账慢。"},
            {"type": "solution", "label": "解决路径", "content": "AI 自动处理票据流水。"},
            {"type": "cta", "label": "结尾 CTA", "content": "私信 AI记账 进群。"},
        ]

    def generate_image(self, prompt: str, image_type: str):
        return GeneratedImage(filename=f"{image_type}.svg", content=b"<svg></svg>", content_type="image/svg+xml")


def test_api_content_creation_flow(tmp_path: Path):
    app = create_app(database_path=tmp_path / "content.db", upload_dir=tmp_path / "uploads", ai_client=FakeAIClient())
    client = TestClient(app)

    topic_response = client.post("/api/topic-recommendations", json={"user_prompt": "给老板看"})
    assert topic_response.status_code == 200
    assert topic_response.json()["directions"][0]["titles"] == ["8小时变5分钟"]
    assert topic_response.json()["assistant_message"] == "已根据你的需求生成 1 个选题方向和 1 个标题。"

    project_response = client.post(
        "/api/content-projects",
        json={"title": "8小时变5分钟", "title_direction": "效率颠覆"},
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["id"]

    article_response = client.post(f"/api/content-projects/{project_id}/generate-article")
    assert article_response.status_code == 200
    assert len(article_response.json()["blocks"]) == 4
    assert [block["label"] for block in article_response.json()["blocks"]] == ["引言", "正文", "正文", "正文"]

    edited_article_response = client.patch(
        f"/api/content-projects/{project_id}/article",
        json={
            "blocks": [
                {"block_type": "intro", "label": "开头钩子", "content": "编辑后的开头。", "sort_order": 1},
                {"block_type": "pain", "label": "痛点放大", "content": "编辑后的痛点。", "sort_order": 2},
            ]
        },
    )
    assert edited_article_response.status_code == 200
    assert edited_article_response.json()["blocks"][0]["content"] == "编辑后的开头。"

    image_response = client.post(f"/api/content-projects/{project_id}/generate-images")
    assert image_response.status_code == 200
    assets = image_response.json()["assets"]
    assert len(assets) == 2

    second_image_response = client.post(f"/api/content-projects/{project_id}/generate-images")
    assert second_image_response.status_code == 200
    assets = second_image_response.json()["assets"]
    assert len(assets) == 4

    cover_only_response = client.post(
        f"/api/content-projects/{project_id}/generate-images",
        json={"asset_type": "cover"},
    )
    assert cover_only_response.status_code == 200
    assert len(cover_only_response.json()["generated_assets"]) == 1
    assert cover_only_response.json()["generated_assets"][0]["asset_type"] == "cover"
    assets = cover_only_response.json()["assets"]
    assert len(assets) == 5

    cover_assets = [asset for asset in assets if asset["asset_type"] == "cover"]
    assert len(cover_assets) == 3
    assert sum(asset["selected"] for asset in cover_assets) == 1

    multi_cover_response = client.patch(
        f"/api/content-projects/{project_id}/media-selection",
        json={"selected_asset_ids": [cover_assets[0]["id"], cover_assets[1]["id"]]},
    )
    assert multi_cover_response.status_code == 400
    assert multi_cover_response.json()["detail"] == "只能选择一张封面图"

    selection_response = client.patch(
        f"/api/content-projects/{project_id}/media-selection",
        json={"selected_asset_ids": [assets[0]["id"]]},
    )
    assert selection_response.status_code == 200
    assert [asset["selected"] for asset in selection_response.json()["assets"]].count(1) == 1

    inline_assets = [asset for asset in assets if asset["asset_type"] == "inline"]
    layout_response = client.patch(
        f"/api/content-projects/{project_id}/layout",
        json={
            "blocks": [
                {"section_type": "title", "content": "8小时变5分钟", "media_asset_id": cover_assets[0]["id"], "sort_order": 1},
                {"section_type": "intro", "content": "编辑后的开头。", "media_asset_id": inline_assets[0]["id"], "sort_order": 2},
                {"section_type": "body", "content": "编辑后的痛点。", "media_asset_id": inline_assets[1]["id"], "sort_order": 3},
            ]
        },
    )
    assert layout_response.status_code == 200
    assert layout_response.json()["layout_blocks"][1]["media_asset_id"] == inline_assets[0]["id"]
    assert layout_response.json()["layout_blocks"][2]["media_asset_id"] == inline_assets[1]["id"]

    layout_drafts_response = client.get("/api/drafts")
    assert layout_drafts_response.status_code == 200
    draft = layout_drafts_response.json()["drafts"][0]
    assert draft["project_id"] == project_id
    assert draft["restore_stage"] == "layout"

    draft_detail_response = client.get(f"/api/drafts/{draft['id']}")
    assert draft_detail_response.status_code == 200
    snapshot = draft_detail_response.json()["snapshot"]
    assert snapshot["article_blocks"][0]["content"] == "编辑后的开头。"
    assert snapshot["layout_blocks"][1]["media_asset_id"] == inline_assets[0]["id"]

    delete_draft_response = client.delete(f"/api/drafts/{draft['id']}")
    assert delete_draft_response.status_code == 200
    assert delete_draft_response.json()["deleted"] is True

    deleted_draft_response = client.get(f"/api/drafts/{draft['id']}")
    assert deleted_draft_response.status_code == 404

    empty_drafts_response = client.get("/api/drafts")
    assert empty_drafts_response.status_code == 200
    assert empty_drafts_response.json()["drafts"] == []

    stage_response = client.patch(f"/api/content-projects/{project_id}/stage", json={"stage": "layout"})
    assert stage_response.status_code == 200
    assert stage_response.json()["project"]["current_stage"] == "layout"
    assert len(stage_response.json()["media_assets"]) == 5

    drafts_response = client.get("/api/drafts")
    assert drafts_response.status_code == 200
    assert drafts_response.json()["drafts"][0]["restore_stage"] == "layout"

    article_stage_response = client.patch(f"/api/content-projects/{project_id}/stage", json={"stage": "article"})
    assert article_stage_response.status_code == 200
    assert len(article_stage_response.json()["media_assets"]) == 5

    image_stage_response = client.patch(f"/api/content-projects/{project_id}/stage", json={"stage": "images"})
    assert image_stage_response.status_code == 200
    assert image_stage_response.json()["project"]["current_stage"] == "images"
    assert len(image_stage_response.json()["media_assets"]) == 5

    title_stage_response = client.patch(f"/api/content-projects/{project_id}/stage", json={"stage": "title"})
    assert title_stage_response.status_code == 200
    assert title_stage_response.json()["project"]["current_stage"] == "title"

    drafts_response = client.get("/api/drafts")
    assert drafts_response.status_code == 200
    assert drafts_response.json()["drafts"][0]["restore_stage"] == "title"


def test_api_task_center_flow(tmp_path: Path):
    app = create_app(database_path=tmp_path / "content.db", upload_dir=tmp_path / "uploads", ai_client=FakeAIClient())
    client = TestClient(app)

    empty_response = client.get("/api/tasks")
    assert empty_response.status_code == 200
    assert empty_response.json()["tasks"] == []

    create_response = client.post(
        "/api/tasks",
        json={
            "platform": "公众号",
            "account_name": "AI记账助手",
            "execute_time": "2026-07-08 09:30",
            "task_type": "公众号文章创作",
            "topic": "AI 记账如何降低人工成本",
            "status": "失败",
        },
    )
    assert create_response.status_code == 200
    assert create_response.json()["status"] == "失败"

    list_response = client.get("/api/tasks", params={"platform": "公众号", "q": "人工成本"})
    assert list_response.status_code == 200
    assert len(list_response.json()["tasks"]) == 1

    retry_response = client.post("/api/tasks/retry")
    assert retry_response.status_code == 200
    assert retry_response.json()["updated"] == 1

    retried_response = client.get("/api/tasks", params={"status": "待执行"})
    assert retried_response.status_code == 200
    assert retried_response.json()["tasks"][0]["status"] == "待执行"
