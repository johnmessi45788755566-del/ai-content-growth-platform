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

    project_response = client.post(
        "/api/content-projects",
        json={"title": "8小时变5分钟", "title_direction": "效率颠覆"},
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["id"]

    article_response = client.post(f"/api/content-projects/{project_id}/generate-article")
    assert article_response.status_code == 200
    assert len(article_response.json()["blocks"]) == 4

    image_response = client.post(f"/api/content-projects/{project_id}/generate-images")
    assert image_response.status_code == 200
    assets = image_response.json()["assets"]
    assert len(assets) == 2

    second_image_response = client.post(f"/api/content-projects/{project_id}/generate-images")
    assert second_image_response.status_code == 200
    assets = second_image_response.json()["assets"]
    assert len(assets) == 4
    cover_assets = [asset for asset in assets if asset["asset_type"] == "cover"]
    assert len(cover_assets) == 2

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

    stage_response = client.patch(f"/api/content-projects/{project_id}/stage", json={"stage": "layout"})
    assert stage_response.status_code == 200
    assert stage_response.json()["project"]["current_stage"] == "layout"
    assert len(stage_response.json()["media_assets"]) == 4

    drafts_response = client.get("/api/drafts")
    assert drafts_response.status_code == 200
    assert drafts_response.json()["drafts"][0]["restore_stage"] == "layout"

    article_stage_response = client.patch(f"/api/content-projects/{project_id}/stage", json={"stage": "article"})
    assert article_stage_response.status_code == 200
    assert len(article_stage_response.json()["media_assets"]) == 4

    image_stage_response = client.patch(f"/api/content-projects/{project_id}/stage", json={"stage": "images"})
    assert image_stage_response.status_code == 200
    assert image_stage_response.json()["project"]["current_stage"] == "images"
    assert len(image_stage_response.json()["media_assets"]) == 4

    title_stage_response = client.patch(f"/api/content-projects/{project_id}/stage", json={"stage": "title"})
    assert title_stage_response.status_code == 200
    assert title_stage_response.json()["project"]["current_stage"] == "title"

    drafts_response = client.get("/api/drafts")
    assert drafts_response.status_code == 200
    assert drafts_response.json()["drafts"][0]["restore_stage"] == "title"
