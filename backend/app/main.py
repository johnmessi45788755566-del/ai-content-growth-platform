import asyncio
import os
from contextlib import asynccontextmanager
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

from .ai import AIConfigurationError, AIResponseError, OpenAICompatibleClient
from .browser_auth import BrowserAuthManager
from .database import Database
from .publishing.coordinator import PublishJobValidationError
from .publishing.router import PlatformPublisherRouter
from .services import AccountConflictError, ContentService, NotFoundError, TaskConflictError


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRUSTED_FRONTEND_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)


def trusted_frontend_origins() -> list[str]:
    configured = os.getenv("TRUSTED_FRONTEND_ORIGINS", "").strip()
    origins = (
        [value.strip().rstrip("/") for value in configured.split(",") if value.strip()]
        if configured
        else list(DEFAULT_TRUSTED_FRONTEND_ORIGINS)
    )
    if not origins or "*" in origins:
        raise RuntimeError("TRUSTED_FRONTEND_ORIGINS 必须配置明确的可信 Origin")
    return list(dict.fromkeys(origins))


class TopicRecommendationRequest(BaseModel):
    user_prompt: str = Field(min_length=1)


class ContentProjectRequest(BaseModel):
    title: str = Field(min_length=1)
    title_direction: str = ""
    platform: str = "微信公众号"


class ArticleBlockRequest(BaseModel):
    block_type: str
    label: str
    content: str
    sort_order: int


class ArticleSaveRequest(BaseModel):
    blocks: list[ArticleBlockRequest]


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
    account_id: int | None = None


class PublishJobRequest(BaseModel):
    project_id: int
    account_id: int
    action: str


class AccountAuthRequest(BaseModel):
    platform: str = Field(min_length=1)
    account_name: str = ""
    account_id: int | None = None


class AccountUpdateRequest(BaseModel):
    name: str | None = None
    external_id: str | None = None
    owner: str = "未分配"
    tag: str = ""
    monitor_status: str = "监测中"
    role: str = ""
    audience: str = ""
    style: str = ""
    taboo: str = ""
    preference: str = ""


class TaskCreateRequest(BaseModel):
    platform: str = "公众号"
    account_id: int | None = None
    account_name: str = Field(min_length=1)
    execute_time: str = Field(min_length=1)
    task_type: str = "微信公众号图文生成"
    topic: str = Field(min_length=1)
    article_requirements: str = ""
    selected_title: str = ""
    title_direction: str = ""
    status: str = "待执行"
    publish_action: Literal["publish", "draft"] = "publish"
    execution_mode: str = "once"
    repeat_rule: str = ""
    weekly_day: int | None = None
    failure_stage: str = ""
    failure_reason: str = ""


class TaskUpdateRequest(BaseModel):
    platform: str = "公众号"
    account_id: int | None = None
    account_name: str = Field(min_length=1)
    execute_time: str = Field(min_length=1)
    task_type: str = "微信公众号图文生成"
    topic: str = Field(min_length=1)
    article_requirements: str = ""
    selected_title: str = ""
    title_direction: str = ""
    publish_action: Literal["publish", "draft"] = "publish"
    execution_mode: str = "once"
    repeat_rule: str = ""
    weekly_day: int | None = None


class TaskRetryRequest(BaseModel):
    task_ids: list[int] = []


def create_app(
    database_path: str | Path | None = None,
    upload_dir: str | Path | None = None,
    ai_client: Any | None = None,
    browser_auth_manager: Any | None = None,
    platform_publisher: Any | None = None,
    scheduler_enabled: bool = True,
    trusted_origins: list[str] | None = None,
) -> FastAPI:
    if load_dotenv:
        load_dotenv(BACKEND_ROOT / ".env")

    db = Database(database_path or BACKEND_ROOT / "data" / "content.db")
    db.init_schema()
    uploads = Path(upload_dir or BACKEND_ROOT / "uploads")
    browser_auth = browser_auth_manager or BrowserAuthManager(BACKEND_ROOT / "data" / "browser_profiles")
    service = ContentService(
        db=db,
        ai_client=ai_client or OpenAICompatibleClient(),
        upload_dir=uploads,
        browser_auth_manager=browser_auth,
        platform_publisher=platform_publisher or PlatformPublisherRouter(),
    )

    def run_scheduler_maintenance() -> None:
        try:
            service.recover_stale_awaiting_confirmations()
        except Exception:
            pass
        try:
            service.recover_publish_jobs()
        except Exception:
            pass
        try:
            service.sync_due_accounts()
        except Exception:
            pass
        try:
            service.execute_due_tasks()
        except Exception:
            pass

    async def task_scheduler_loop() -> None:
        while True:
            try:
                await asyncio.to_thread(run_scheduler_maintenance)
            except Exception:
                pass
            await asyncio.sleep(30)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if scheduler_enabled:
            app.state.task_scheduler = asyncio.create_task(task_scheduler_loop())
        yield
        task = app.state.task_scheduler
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        browser_auth.close_all()

    allowed_origins = (
        trusted_frontend_origins()
        if trusted_origins is None
        else list(
            dict.fromkeys(
                origin.strip().rstrip("/")
                for origin in trusted_origins
                if origin.strip()
            )
        )
    )
    if not allowed_origins or "*" in allowed_origins:
        raise RuntimeError("TRUSTED_FRONTEND_ORIGINS 必须配置明确的可信 Origin")
    app = FastAPI(title="AI 内容创作后端", version="0.1.0", lifespan=lifespan)
    app.state.content_service = service
    app.state.task_scheduler = None
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def enforce_trusted_write_origin(request: Request, call_next: Any):
        origin = request.headers.get("origin", "").rstrip("/")
        if (
            request.url.path.startswith("/api/")
            and request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and origin
            and origin not in allowed_origins
        ):
            return JSONResponse(status_code=403, content={"detail": "请求来源不受信任"})
        return await call_next(request)
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

    @app.patch("/api/content-projects/{project_id}/article")
    def save_article(project_id: int, payload: ArticleSaveRequest) -> dict[str, Any]:
        try:
            blocks = [block.model_dump() for block in payload.blocks]
            return service.save_article(project_id, blocks)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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

    @app.delete("/api/content-projects/{project_id}/media-assets/{asset_id}")
    def delete_media_asset(project_id: int, asset_id: int) -> dict[str, Any]:
        try:
            return service.delete_media_asset(project_id, asset_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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

    @app.get("/api/drafts/{draft_id}")
    def draft_detail(draft_id: int) -> dict[str, Any]:
        try:
            return service.get_draft(draft_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/drafts/{draft_id}")
    def delete_draft(draft_id: int) -> dict[str, Any]:
        try:
            return service.delete_draft(draft_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/accounts")
    def list_accounts(
        platform: str = "",
        auth_status: str = "",
        login_status: str = "",
        q: str = "",
    ) -> dict[str, Any]:
        try:
            return {
                "accounts": service.list_accounts(
                    platform=platform,
                    auth_status=auth_status,
                    login_status=login_status,
                    q=q,
                )
            }
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/accounts/auth/start")
    def start_account_auth(payload: AccountAuthRequest) -> dict[str, Any]:
        try:
            return service.start_account_auth(
                payload.platform,
                payload.account_name,
                payload.account_id,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/accounts/auth/{session_id}")
    def get_account_auth_status(session_id: str) -> dict[str, Any]:
        try:
            return service.get_account_auth_status(session_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/accounts/auth/{session_id}/cancel")
    def cancel_account_auth(session_id: str) -> dict[str, Any]:
        try:
            return service.cancel_account_auth(session_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/accounts/{account_id}")
    def get_account(account_id: int) -> dict[str, Any]:
        try:
            return service.get_public_account(account_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/accounts/{account_id}/sync")
    def sync_account(account_id: int) -> dict[str, Any]:
        try:
            return service.sync_account(account_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.patch("/api/accounts/{account_id}")
    def update_account(account_id: int, payload: AccountUpdateRequest) -> dict[str, Any]:
        try:
            return service.update_account(account_id, payload.model_dump())
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/accounts/{account_id}")
    def delete_account(account_id: int) -> dict[str, Any]:
        try:
            return service.delete_account(account_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except AccountConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/analytics/accounts")
    def analytics_accounts(platform: str = "", q: str = "", range: str = "30d") -> dict[str, Any]:
        try:
            return {"range": range, "accounts": service.analytics_accounts(platform=platform, q=q)}
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/analytics/works")
    def analytics_works(platform: str = "", q: str = "", range: str = "30d") -> dict[str, Any]:
        try:
            return {"range": range, "works": service.analytics_works(platform=platform, q=q)}
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/tasks")
    def list_tasks(status: str = "", platform: str = "", q: str = "") -> dict[str, Any]:
        return {"tasks": service.list_tasks(status=status, platform=platform, q=q)}

    @app.post("/api/tasks")
    def create_task(payload: TaskCreateRequest) -> dict[str, Any]:
        try:
            return service.create_task(payload.model_dump())
        except (NotFoundError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: int) -> dict[str, Any]:
        try:
            return service.get_task(task_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/tasks/{task_id}")
    def update_task(task_id: int, payload: TaskUpdateRequest) -> dict[str, Any]:
        try:
            return service.update_task(task_id, payload.model_dump())
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/tasks/{task_id}")
    def delete_task(task_id: int) -> dict[str, Any]:
        try:
            return service.delete_task(task_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/tasks/retry")
    def retry_tasks(payload: TaskRetryRequest | None = None) -> dict[str, Any]:
        return service.retry_failed_tasks(payload.task_ids if payload else None)

    @app.post("/api/tasks/{task_id}/sync-draft")
    def sync_failed_task_to_platform_draft(task_id: int) -> dict[str, Any]:
        try:
            return service.sync_failed_task_to_platform_draft(task_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/publish-jobs")
    def create_publish_job(payload: PublishJobRequest) -> dict[str, Any]:
        try:
            return service.create_publish_job(payload.project_id, payload.account_id, payload.action)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PublishJobValidationError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/publish-jobs/current")
    def get_current_publish_job(
        project_id: int, account_id: int, action: Literal["draft", "publish"]
    ) -> dict[str, Any]:
        try:
            return {
                "job": service.get_current_publish_job(
                    project_id, account_id, action
                )
            }
        except PublishJobValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/publish-jobs/project")
    def get_project_publish_job(project_id: int) -> dict[str, Any]:
        return {"job": service.get_project_publish_job(project_id)}

    @app.get("/api/publish-jobs/monitoring")
    def get_publish_monitoring(platform: str | None = None) -> dict[str, Any]:
        return service.get_publish_monitoring(platform)

    @app.get("/api/publish-jobs/{job_id}")
    def get_publish_job(job_id: int) -> dict[str, Any]:
        try:
            return service.get_publish_job(job_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/publish-jobs/{job_id}/retry")
    def retry_publish_job(job_id: int) -> dict[str, Any]:
        try:
            return service.retry_publish_job(job_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PublishJobValidationError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/publish-jobs/{job_id}/resume")
    def resume_publish_job(job_id: int) -> dict[str, Any]:
        try:
            return service.resume_publish_job(job_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PublishJobValidationError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/content-projects/{project_id}/publish-package")
    def publish_package(project_id: int, payload: PublishPackageRequest) -> dict[str, Any]:
        try:
            return service.create_publish_package(
                project_id, payload.platform, payload.account_name, payload.account_id
            )
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


app = create_app()
