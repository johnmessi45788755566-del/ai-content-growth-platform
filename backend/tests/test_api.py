from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.ai import GeneratedImage, TopicRecommendation
from app.database import Database
from app.main import create_app
from app.publishing.wechat import PublishResult
from app.services import ContentService


class FakeAIClient:
    def __init__(self):
        self.topic_prompts = []

    def recommend_topics(self, user_prompt: str):
        self.topic_prompts.append(user_prompt)
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


class FakePlatformPublisher:
    def __init__(self):
        self.calls = []

    def publish(self, payload, account, action):
        self.calls.append((payload, account, action))
        return PublishResult(
            status="platform_draft",
            platform_draft_id="wechat-draft-media-id",
            platform_url="https://mp.weixin.qq.com/draft/wechat-draft-media-id",
            response={"source": "wechat_platform", "media_id": "wechat-draft-media-id"},
        )

    def resume(self, account, job_context):
        raise AssertionError("draft jobs cannot be resumed")

    def cancel(self, account, job_context):
        return None


def add_authorized_account(service, name: str, platform: str = "微信公众号") -> int:
    return service.db.execute(
        "INSERT INTO platform_accounts (platform, name, auth_status) VALUES (?, ?, ?)",
        (platform, name, "已授权"),
    )


class PublisherWithoutReceipt(FakePlatformPublisher):
    def publish(self, payload, account, action):
        return PublishResult(status="platform_draft")


class PublisherWithSensitiveFailure(FakePlatformPublisher):
    def publish(self, payload, account, action):
        raise RuntimeError(f"浏览器启动失败：{account['profile_path']}")


class ConfirmationPublisher(FakePlatformPublisher):
    def __init__(self):
        super().__init__()
        self.resume_context = None

    def publish(self, payload, account, action):
        return PublishResult(
            status="awaiting_confirmation",
            response={
                "source": "wechat_platform",
                "event": "operator_confirmation_required",
                "job_context": {"resume_handle": "sensitive-browser-handle"},
            },
            requires_confirmation=True,
        )

    def resume(self, account, job_context):
        self.resume_context = job_context
        return PublishResult(
            status="published",
            platform_publish_id="publish-42",
            platform_url="https://mp.weixin.qq.com/s/publish-42",
            response={"source": "wechat_platform", "publish_id": "publish-42"},
        )


def prepare_publish_job_api(tmp_path: Path, publisher, platform: str = "微信公众号"):
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    client = TestClient(app)
    service = app.state.content_service
    account_id = service.db.execute(
        """
        INSERT INTO platform_accounts (platform, name, auth_status, profile_path)
        VALUES (?, ?, ?, ?)
        """,
        (platform, f"{platform}测试账号", "已授权", "/tmp/sensitive-wechat-profile"),
    )
    project = client.post(
        "/api/content-projects",
        json={"title": "发布任务 API 测试", "platform": "微信公众号"},
    ).json()
    article = client.post(f"/api/content-projects/{project['id']}/generate-article").json()
    assets = client.post(f"/api/content-projects/{project['id']}/generate-images").json()["assets"]
    cover = next(asset for asset in assets if asset["asset_type"] == "cover")
    client.patch(
        f"/api/content-projects/{project['id']}/layout",
        json={
            "blocks": [
                {
                    "section_type": "title",
                    "content": project["title"],
                    "media_asset_id": cover["id"],
                    "sort_order": 1,
                },
                {
                    "section_type": "intro",
                    "content": article["blocks"][0]["content"],
                    "media_asset_id": None,
                    "sort_order": 2,
                },
            ]
        },
    )
    return client, project, account_id


def test_create_publish_job_rejects_non_wechat_platform(tmp_path: Path):
    client, project, account_id = prepare_publish_job_api(
        tmp_path, FakePlatformPublisher(), platform="小红书"
    )

    response = client.post(
        "/api/publish-jobs",
        json={"project_id": project["id"], "account_id": account_id, "action": "publish"},
    )

    assert response.status_code == 400
    assert "暂未开放" in response.json()["detail"]


def test_create_publish_job_rejects_switching_project_account(tmp_path: Path):
    client, project, first_account_id = prepare_publish_job_api(
        tmp_path, FakePlatformPublisher()
    )
    first = client.post(
        "/api/publish-jobs",
        json={
            "project_id": project["id"],
            "account_id": first_account_id,
            "action": "draft",
        },
    )
    second_account_id = add_authorized_account(
        client.app.state.content_service, "另一个公众号账号"
    )

    response = client.post(
        "/api/publish-jobs",
        json={
            "project_id": project["id"],
            "account_id": second_account_id,
            "action": "draft",
        },
    )

    assert first.status_code == 200
    assert response.status_code == 400
    assert "已绑定" in response.json()["detail"]


def test_publish_job_requires_platform_observed_success(tmp_path: Path):
    client, project, account_id = prepare_publish_job_api(tmp_path, PublisherWithoutReceipt())

    response = client.post(
        "/api/publish-jobs",
        json={"project_id": project["id"], "account_id": account_id, "action": "draft"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["failure_stage"] == "提交平台草稿"
    assert "平台" in response.json()["failure_reason"]


def test_failed_publish_job_does_not_expose_browser_profile_path(tmp_path: Path):
    client, project, account_id = prepare_publish_job_api(
        tmp_path, PublisherWithSensitiveFailure()
    )

    response = client.post(
        "/api/publish-jobs",
        json={"project_id": project["id"], "account_id": account_id, "action": "draft"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "/tmp/sensitive-wechat-profile" not in response.text


def test_publish_job_get_retry_resume_and_redacts_browser_context(tmp_path: Path):
    publisher = ConfirmationPublisher()
    client, project, account_id = prepare_publish_job_api(tmp_path, publisher)
    created = client.post(
        "/api/publish-jobs",
        json={"project_id": project["id"], "account_id": account_id, "action": "publish"},
    )

    assert created.status_code == 200
    job = created.json()
    assert job["status"] == "awaiting_confirmation"
    assert job["requires_confirmation"] is True
    assert "sensitive-browser-handle" not in created.text
    fetched = client.get(f"/api/publish-jobs/{job['id']}")
    assert fetched.status_code == 200
    assert "sensitive-browser-handle" not in fetched.text

    resumed = client.post(f"/api/publish-jobs/{job['id']}/resume")

    assert resumed.status_code == 200
    assert resumed.json()["status"] == "published"
    assert resumed.json()["platform_publish_id"] == "publish-42"
    assert publisher.resume_context == {"resume_handle": "sensitive-browser-handle"}


def test_failed_publish_job_can_retry_as_same_resource(tmp_path: Path):
    class RetryPublisher(PublisherWithoutReceipt):
        def __init__(self):
            self.attempts = 0

        def publish(self, payload, account, action):
            self.attempts += 1
            if self.attempts == 1:
                return super().publish(payload, account, action)
            return FakePlatformPublisher().publish(payload, account, action)

    publisher = RetryPublisher()
    client, project, account_id = prepare_publish_job_api(tmp_path, publisher)
    failed = client.post(
        "/api/publish-jobs",
        json={"project_id": project["id"], "account_id": account_id, "action": "draft"},
    ).json()

    retried = client.post(f"/api/publish-jobs/{failed['id']}/retry")

    assert retried.status_code == 200
    assert retried.json()["id"] == failed["id"]
    assert retried.json()["status"] == "platform_draft"


def test_publish_package_compatibility_endpoint_uses_draft_job(tmp_path: Path):
    client, project, account_id = prepare_publish_job_api(tmp_path, FakePlatformPublisher())

    response = client.post(
        f"/api/content-projects/{project['id']}/publish-package",
        json={
            "platform": "微信公众号",
            "account_name": "过期的前端账号名",
            "account_id": account_id,
        },
    )

    assert response.status_code == 200
    package = response.json()
    assert package["status"] == "platform_draft"
    assert package["platform_draft_id"] == "wechat-draft-media-id"
    job = client.app.state.content_service.get_publish_job(package["publish_job_id"])
    assert job["action"] == "draft"
    assert job["account_id"] == account_id


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

    retried_response = client.get("/api/tasks", params={"status": "已生成平台草稿"})
    assert retried_response.status_code == 200
    assert retried_response.json()["tasks"][0]["status"] == "已生成平台草稿"
    assert retried_response.json()["tasks"][0]["content_project_id"]


def test_accounts_auth_sync_and_analytics_flow(tmp_path: Path):
    app = create_app(database_path=tmp_path / "content.db", upload_dir=tmp_path / "uploads", ai_client=FakeAIClient())
    client = TestClient(app)

    auth_response = client.post(
        "/api/accounts/auth/start",
        json={"platform": "微信公众号", "account_name": "AI记账助手"},
    )
    assert auth_response.status_code == 200
    account = auth_response.json()["account"]
    assert account["platform"] == "微信公众号"
    assert account["auth_status"] == "已授权"
    assert auth_response.json()["qr_url"]

    accounts_response = client.get("/api/accounts", params={"platform": "微信公众号"})
    assert accounts_response.status_code == 200
    assert accounts_response.json()["accounts"][0]["name"] == "AI记账助手"

    sync_response = client.post(f"/api/accounts/{account['id']}/sync")
    assert sync_response.status_code == 200
    assert sync_response.json()["synced"] is True

    account_analytics_response = client.get("/api/analytics/accounts", params={"range": "30d"})
    assert account_analytics_response.status_code == 200
    account_rows = account_analytics_response.json()["accounts"]
    assert account_rows[0]["account_name"] == "AI记账助手"
    assert account_rows[0]["reads"] > 0

    work_analytics_response = client.get("/api/analytics/works", params={"range": "30d"})
    assert work_analytics_response.status_code == 200
    works = work_analytics_response.json()["works"]
    assert works[0]["account_name"] == "AI记账助手"
    assert works[0]["reads"] > 0


def test_task_can_bind_authorized_account_and_create_platform_draft(tmp_path: Path):
    app = create_app(database_path=tmp_path / "content.db", upload_dir=tmp_path / "uploads", ai_client=FakeAIClient())
    client = TestClient(app)
    account = client.post(
        "/api/accounts/auth/start",
        json={"platform": "微信公众号", "account_name": "AI记账助手"},
    ).json()["account"]

    task_response = client.post(
        "/api/tasks",
        json={
            "platform": "微信公众号",
            "account_id": account["id"],
            "account_name": "AI记账助手",
            "execute_time": "2026-07-08 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "AI 记账如何降低人工成本",
        },
    )
    assert task_response.status_code == 200
    assert task_response.json()["account_id"] == account["id"]

    retry_response = client.post("/api/tasks/retry", json={"task_ids": []})
    assert retry_response.status_code == 200

    app.state.content_service.execute_due_tasks(datetime(2026, 7, 8, 9, 31))
    detail_response = client.get(f"/api/tasks/{task_response.json()['id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["status"] == "已生成平台草稿"
    assert detail["publish_package"]["status"] == "platform_draft"
    assert detail["publish_package"]["account_name"] == "AI记账助手"


def make_task_service(tmp_path: Path) -> ContentService:
    db = Database(tmp_path / "content.db")
    db.init_schema()
    return ContentService(db=db, ai_client=FakeAIClient(), upload_dir=tmp_path / "uploads")


def test_due_wechat_task_generates_content_package_and_draft(tmp_path: Path):
    service = make_task_service(tmp_path)
    task = service.create_task(
        {
            "platform": "公众号",
            "account_name": "AI记账助手",
            "execute_time": "2026-07-08 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "AI 记账如何降低人工成本",
            "execution_mode": "once",
        }
    )

    result = service.execute_due_tasks(datetime(2026, 7, 8, 9, 31))

    assert result["executed"] == 1
    detail = service.get_task(task["id"])
    assert detail["status"] == "已生成平台草稿"
    assert detail["content_project_id"]
    assert detail["publish_package_id"]
    assert detail["selected_title"] == "8小时变5分钟"
    assert detail["content_project"]["title"] == "8小时变5分钟"
    assert detail["content_project"]["title_direction"] == "效率颠覆"
    assert detail["failure_reason"] == ""
    assert detail["runs"][0]["status"] == "已生成平台草稿"
    assert service.list_drafts()[0]["project_id"] == detail["content_project_id"]


def test_due_wechat_task_falls_back_to_topic_when_title_not_selected(tmp_path: Path):
    service = make_task_service(tmp_path)
    task = service.create_task(
        {
            "platform": "公众号",
            "account_name": "AI记账助手",
            "execute_time": "2026-07-08 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "AI记账",
        }
    )

    service.execute_due_tasks(datetime(2026, 7, 8, 9, 31))

    detail = service.get_task(task["id"])
    assert detail["status"] == "已生成平台草稿"
    assert detail["content_project"]["title"] == "8小时变5分钟"


def test_due_task_generates_title_from_topic_account_and_creation_requirements(tmp_path: Path):
    db = Database(tmp_path / "content.db")
    db.init_schema()
    ai_client = FakeAIClient()
    service = ContentService(db=db, ai_client=ai_client, upload_dir=tmp_path / "uploads")
    task = service.create_task(
        {
            "platform": "公众号",
            "account_name": "AI记账助手",
            "execute_time": "2026-07-08 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "AI记账",
            "article_requirements": "面向小微企业老板，加入老板熬夜对账的例子。",
        }
    )

    service.execute_due_tasks(datetime(2026, 7, 8, 9, 31))

    detail = service.get_task(task["id"])
    assert detail["article_requirements"] == "面向小微企业老板，加入老板熬夜对账的例子。"
    assert detail["selected_title"] == "8小时变5分钟"
    assert detail["title_direction"] == "效率颠覆"
    assert detail["content_project"]["title"] == "8小时变5分钟"
    assert "AI记账" in ai_client.topic_prompts[-1]
    assert "AI记账助手" in ai_client.topic_prompts[-1]
    assert "面向小微企业老板，加入老板熬夜对账的例子。" in ai_client.topic_prompts[-1]


def test_repeating_tasks_reschedule_daily_and_weekly(tmp_path: Path):
    service = make_task_service(tmp_path)
    daily = service.create_task(
        {
            "platform": "公众号",
            "account_name": "AI记账助手",
            "execute_time": "2026-07-08 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "每天生成一篇",
            "execution_mode": "repeat",
            "repeat_rule": "daily",
        }
    )
    weekly = service.create_task(
        {
            "platform": "公众号",
            "account_name": "小微账房",
            "execute_time": "2026-07-08 13:00",
            "task_type": "微信公众号图文生成",
            "topic": "每周生成一篇",
            "execution_mode": "repeat",
            "repeat_rule": "weekly",
            "weekly_day": 2,
        }
    )

    result = service.execute_due_tasks(datetime(2026, 7, 8, 13, 1))

    assert result["executed"] == 2
    daily_detail = service.get_task(daily["id"])
    weekly_detail = service.get_task(weekly["id"])
    assert daily_detail["status"] == "待执行"
    assert daily_detail["next_run_at"] == "2026-07-09 09:30"
    assert weekly_detail["status"] == "待执行"
    assert weekly_detail["next_run_at"] == "2026-07-15 13:00"


def test_api_task_edit_delete_detail_and_selected_retry(tmp_path: Path):
    app = create_app(database_path=tmp_path / "content.db", upload_dir=tmp_path / "uploads", ai_client=FakeAIClient())
    client = TestClient(app)

    first_failed = client.post(
        "/api/tasks",
        json={
            "platform": "公众号",
            "account_name": "AI记账助手",
            "execute_time": "2026-07-08 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "失败任务 1",
            "status": "失败",
            "failure_stage": "生成失败",
            "failure_reason": "模型超时",
        },
    ).json()
    second_failed = client.post(
        "/api/tasks",
        json={
            "platform": "公众号",
            "account_name": "小微账房",
            "execute_time": "2026-07-08 10:30",
            "task_type": "微信公众号图文生成",
            "topic": "失败任务 2",
            "status": "失败",
            "failure_stage": "接口失败",
            "failure_reason": "接口返回错误",
        },
    ).json()
    pending = client.post(
        "/api/tasks",
        json={
            "platform": "小红书",
            "account_name": "财务自动化笔记",
            "execute_time": "2026-07-09 20:00",
            "task_type": "小红书图文生成（预留）",
            "topic": "预留任务",
            "execution_mode": "repeat",
            "repeat_rule": "daily",
        },
    ).json()

    update_response = client.patch(
        f"/api/tasks/{pending['id']}",
        json={
            "platform": "公众号",
            "account_name": "老板懂财务",
            "execute_time": "2026-07-10 08:00",
            "task_type": "微信公众号图文生成",
            "topic": "编辑后的任务",
            "execution_mode": "repeat",
            "repeat_rule": "weekly",
            "weekly_day": 4,
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["topic"] == "编辑后的任务"
    assert update_response.json()["next_run_at"] == "2026-07-10 08:00"

    detail_response = client.get(f"/api/tasks/{first_failed['id']}")
    assert detail_response.status_code == 200
    assert detail_response.json()["failure_reason"] == "模型超时"
    assert detail_response.json()["runs"] == []

    retry_response = client.post("/api/tasks/retry", json={"task_ids": [first_failed["id"]]})
    assert retry_response.status_code == 200
    assert retry_response.json()["updated"] == 1
    retried_detail = client.get(f"/api/tasks/{first_failed['id']}").json()
    untouched_detail = client.get(f"/api/tasks/{second_failed['id']}").json()
    assert retried_detail["status"] == "已生成平台草稿"
    assert retried_detail["retry_count"] == 1
    assert retried_detail["content_project_id"]
    assert untouched_detail["status"] == "失败"

    delete_response = client.delete(f"/api/tasks/{pending['id']}")
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True
    missing_response = client.get(f"/api/tasks/{pending['id']}")
    assert missing_response.status_code == 404


def test_api_task_accepts_datetime_local_input(tmp_path: Path):
    app = create_app(database_path=tmp_path / "content.db", upload_dir=tmp_path / "uploads", ai_client=FakeAIClient())
    client = TestClient(app)

    create_response = client.post(
        "/api/tasks",
        json={
            "platform": "公众号",
            "account_name": "AI记账助手",
            "execute_time": "2026-07-08T09:30",
            "task_type": "微信公众号图文生成",
            "topic": "日期选择器创建任务",
        },
    )

    assert create_response.status_code == 200
    assert create_response.json()["execute_time"] == "2026-07-08 09:30"
    assert create_response.json()["next_run_at"] == "2026-07-08 09:30"


def test_api_repeat_task_accepts_time_only_input(tmp_path: Path):
    app = create_app(database_path=tmp_path / "content.db", upload_dir=tmp_path / "uploads", ai_client=FakeAIClient())
    client = TestClient(app)

    create_response = client.post(
        "/api/tasks",
        json={
            "platform": "公众号",
            "account_name": "AI记账助手",
            "execute_time": "09:30",
            "task_type": "微信公众号图文生成",
            "topic": "每天定时生成",
            "execution_mode": "repeat",
            "repeat_rule": "daily",
        },
    )

    assert create_response.status_code == 200
    assert create_response.json()["execute_time"].endswith(" 09:30")
    assert create_response.json()["next_run_at"].endswith(" 09:30")


def test_reserved_xiaohongshu_task_can_create_platform_draft_for_internal_rpa_flow(tmp_path: Path):
    service = make_task_service(tmp_path)
    task = service.create_task(
        {
            "platform": "小红书",
            "account_name": "财务自动化笔记",
            "execute_time": "2026-07-08 09:30",
            "task_type": "小红书图文生成（预留）",
            "topic": "小红书预留任务",
        }
    )

    result = service.execute_due_tasks(datetime(2026, 7, 8, 9, 31))

    assert result["executed"] == 1
    detail = service.get_task(task["id"])
    assert detail["status"] == "已生成平台草稿"
    assert detail["failure_stage"] == ""
    assert detail["content_project_id"]
