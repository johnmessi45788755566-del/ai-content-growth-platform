from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

from .ai import AIConfigurationError, AIResponseError, OpenAICompatibleClient
from .database import Database
from .services import ContentService, NotFoundError


BACKEND_ROOT = Path(__file__).resolve().parents[1]


class TopicRecommendationRequest(BaseModel):
    user_prompt: str = Field(min_length=1)


class ContentProjectRequest(BaseModel):
    title: str = Field(min_length=1)
    title_direction: str = ""
    platform: str = "微信公众号"


class LayoutBlockRequest(BaseModel):
    section_type: str
    content: str = ""
    media_asset_id: int | None = None
    sort_order: int


class LayoutSaveRequest(BaseModel):
    blocks: list[LayoutBlockRequest]


class AutosaveRequest(BaseModel):
    stage: str
    restore_stage: str | None = None


class StageUpdateRequest(BaseModel):
    stage: str


class ImageGenerationRequest(BaseModel):
    asset_type: str | None = None


class MediaSelectionRequest(BaseModel):
    selected_asset_ids: list[int]


class PublishPackageRequest(BaseModel):
    platform: str = "微信公众号"
    account_name: str = "AI 记账工具公众号"


def create_app(
    database_path: str | Path | None = None,
    upload_dir: str | Path | None = None,
    ai_client: Any | None = None,
) -> FastAPI:
    if load_dotenv:
        load_dotenv(BACKEND_ROOT / ".env")

    db = Database(database_path or BACKEND_ROOT / "data" / "content.db")
    db.init_schema()
    uploads = Path(upload_dir or BACKEND_ROOT / "uploads")
    service = ContentService(db=db, ai_client=ai_client or OpenAICompatibleClient(), upload_dir=uploads)

    app = FastAPI(title="AI 内容创作后端", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    uploads.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=uploads), name="uploads")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/topic-recommendations")
    def topic_recommendations(payload: TopicRecommendationRequest) -> dict[str, Any]:
        try:
            directions = service.recommend_topics(payload.user_prompt)
        except (AIConfigurationError, AIResponseError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        title_count = sum(len(direction.titles) for direction in directions)
        return {
            "directions": [direction.__dict__ for direction in directions],
            "assistant_message": f"已根据你的需求生成 {len(directions)} 个选题方向和 {title_count} 个标题。",
        }

    @app.post("/api/content-projects")
    def create_project(payload: ContentProjectRequest) -> dict[str, Any]:
        return service.create_project(payload.title, payload.title_direction, payload.platform)

    @app.get("/api/content-projects/{project_id}")
    def get_project(project_id: int) -> dict[str, Any]:
        try:
            return service.get_project(project_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/content-projects/{project_id}/generate-article")
    def generate_article(project_id: int) -> dict[str, Any]:
        try:
            return service.generate_article(project_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (AIConfigurationError, AIResponseError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/content-projects/{project_id}/generate-images")
    def generate_images(project_id: int, payload: ImageGenerationRequest | None = None) -> dict[str, Any]:
        try:
            return service.generate_images(project_id, payload.asset_type if payload else None)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (AIConfigurationError, AIResponseError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.patch("/api/content-projects/{project_id}/layout")
    def save_layout(project_id: int, payload: LayoutSaveRequest) -> dict[str, Any]:
        try:
            blocks = [block.model_dump() for block in payload.blocks]
            return service.save_layout(project_id, blocks)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/content-projects/{project_id}/autosave")
    def autosave(project_id: int, payload: AutosaveRequest) -> dict[str, Any]:
        try:
            return service.autosave(project_id, payload.stage, payload.restore_stage)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch("/api/content-projects/{project_id}/media-selection")
    def media_selection(project_id: int, payload: MediaSelectionRequest) -> dict[str, Any]:
        try:
            return service.select_media_assets(project_id, payload.selected_asset_ids)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/content-projects/{project_id}/stage")
    def update_stage(project_id: int, payload: StageUpdateRequest) -> dict[str, Any]:
        try:
            return service.set_project_stage(project_id, payload.stage)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/drafts")
    def drafts(start_date: str = "", end_date: str = "") -> dict[str, Any]:
        return {"drafts": service.list_drafts(start_date, end_date)}

    @app.post("/api/content-projects/{project_id}/publish-package")
    def publish_package(project_id: int, payload: PublishPackageRequest) -> dict[str, Any]:
        try:
            return service.create_publish_package(project_id, payload.platform, payload.account_name)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app


app = create_app()
