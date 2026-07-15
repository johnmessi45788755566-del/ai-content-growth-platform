import json
import inspect
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.services as services_module
from app.ai import GeneratedImage, TopicRecommendation
from app.database import Database
from app.main import create_app
from app.publishing.wechat import PublishResult
from app.services import ContentService, TaskConflictError


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


class FailingImageAIClient(FakeAIClient):
    def generate_image(self, prompt: str, image_type: str):
        raise RuntimeError("图片模型账户欠费")


class FixedServiceDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 7, 14, 9, 31)
        return value.replace(tzinfo=tz) if tz else value


class FakeBrowserAuthManager:
    def __init__(self):
        self.sessions = {}

    def start(self, platform, account_name, on_success):
        account = on_success(
            {
                "platform": platform,
                "account_name": account_name or f"{platform}测试账号",
                "external_id": "real-platform-id",
                "profile_path": f"/tmp/browser-profiles/{platform}",
                "login_url": "https://example.com/logged-in",
            }
        )
        session = {
            "session_id": "fake-session",
            "platform": platform,
            "status": "success",
            "message": "登录成功",
            "login_url": "https://example.com/login",
            "account": account,
            "error": "",
        }
        self.sessions["fake-session"] = session
        return session

    def get_status(self, session_id):
        return self.sessions[session_id]

    def cancel(self, session_id):
        self.sessions[session_id]["status"] = "cancelled"
        return self.sessions[session_id]

    def sync(self, account):
        return {
            "source": "browser",
            "account_name": account["name"],
            "external_id": account["external_id"],
            "metrics": {
                "fans": 321,
                "reads": 654,
                "recommends": 21,
                "likes": 34,
                "comments": 5,
                "shares": 8,
                "favorites": 13,
            },
            "works": [
                {
                    "title": "真实平台作品",
                    "content_type": "图文",
                    "status": "已发布",
                    "publish_url": "https://example.com/real-work",
                    "published_at": "2026-07-09 12:00",
                    "reads": 144,
                    "recommends": 12,
                    "likes": 9,
                    "comments": 2,
                    "shares": 3,
                    "favorites": 4,
                }
            ],
        }

    def close_all(self):
        return None


class FakePlatformPublisher:
    def __init__(self):
        self.calls = []

    def publish(self, payload, account, action):
        self.calls.append((payload, account, action))
        if action == "publish":
            return PublishResult(
                status="published",
                platform_publish_id="wechat-publish-id",
                platform_url="https://mp.weixin.qq.com/s/wechat-publish-id",
                response={
                    "source": "wechat_platform",
                    "event": "article_published",
                    "receipt_id": "wechat-publish-id",
                    "corroboration": "publish_success",
                },
            )
        return PublishResult(
            status="platform_draft",
            platform_draft_id="wechat-draft-media-id",
            platform_url="https://mp.weixin.qq.com/draft/wechat-draft-media-id",
            response={
                "source": "wechat_platform",
                "event": "draft_created",
                "receipt_id": "wechat-draft-media-id",
                "corroboration": "draft_saved",
            },
        )

    def resume(self, account, job_context):
        raise AssertionError("draft jobs cannot be resumed")

    def cancel(self, account, job_context):
        return None


class FailingPlatformPublisher:
    def publish(self, payload, account, action):
        raise RuntimeError("微信接口错误 40164：IP 不在白名单")

    def resume(self, account, job_context):
        raise RuntimeError("微信接口错误 40164：IP 不在白名单")

    def cancel(self, account, job_context):
        return None


def add_authorized_account(service, name: str, platform: str = "微信公众号") -> int:
    return service.db.execute(
        "INSERT INTO platform_accounts (platform, name, auth_status) VALUES (?, ?, ?)",
        (platform, name, "已授权"),
    )


def leave_task_publish_job_in_history(service, detail, source: str, status: str) -> None:
    job_id = detail["publish_job_id"]
    package_id = detail["publish_package_id"]
    run_id = detail["runs"][0]["id"]
    with service.db.transaction() as conn:
        conn.execute(
            """
            UPDATE scheduled_tasks
            SET status = ?, publish_job_id = NULL, publish_package_id = NULL
            WHERE id = ?
            """,
            (status, detail["id"]),
        )
        conn.execute(
            """
            UPDATE task_runs
            SET status = ?, finished_at = CASE WHEN ? = '执行中' THEN '' ELSE finished_at END,
                publish_job_id = ?, publish_package_id = ?
            WHERE id = ?
            """,
            (
                status,
                status,
                job_id if source == "run" else None,
                package_id if source == "package" else None,
                run_id,
            ),
        )
        conn.execute(
            """
            UPDATE publish_packages
            SET status = 'pending_publish', publish_job_id = ?
            WHERE id = ?
            """,
            (job_id if source == "package" else None, package_id),
        )


def prepare_task_with_historical_publish_link(
    tmp_path: Path, source: str, status: str, name: str
):
    publisher = FakePlatformPublisher()
    app = create_app(
        database_path=tmp_path / f"historical-{name}-{source}.db",
        upload_dir=tmp_path / f"uploads-{name}-{source}",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    client = TestClient(app)
    service = app.state.content_service
    account_id = add_authorized_account(service, f"历史关联-{name}-{source}")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": f"历史关联-{name}-{source}",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "历史发布作业必须原位恢复",
        }
    )
    finished = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    leave_task_publish_job_in_history(service, finished, source, status)
    return client, service, publisher, account_id, finished


def prepare_task_interrupted_with_pending_job(tmp_path: Path, name: str):
    class SimulatedProcessExit(BaseException):
        pass

    publisher = FakePlatformPublisher()
    app = create_app(
        database_path=tmp_path / f"interrupted-pending-{name}.db",
        upload_dir=tmp_path / f"uploads-{name}",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    service = app.state.content_service
    account_id = add_authorized_account(service, f"中断恢复-{name}")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": f"中断恢复-{name}",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": f"中断恢复-{name}",
        }
    )
    original_link = service._link_task_publish_job

    def exit_after_link(*args, **kwargs):
        original_link(*args, **kwargs)
        raise SimulatedProcessExit

    service._link_task_publish_job = exit_after_link
    try:
        with pytest.raises(SimulatedProcessExit):
            service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    finally:
        service._link_task_publish_job = original_link

    interrupted = service.get_task(task["id"])
    assert interrupted["status"] == "执行中"
    assert interrupted["runs"][0]["status"] == "执行中"
    assert interrupted["publish_job"]["status"] == "pending"
    assert interrupted["publish_package"]["status"] == "pending_publish"
    assert publisher.calls == []
    return service, publisher, task, interrupted


class PublisherWithoutReceipt(FakePlatformPublisher):
    def publish(self, payload, account, action):
        return PublishResult(status="platform_draft")


class PublisherWithSensitiveFailure(FakePlatformPublisher):
    def publish(self, payload, account, action):
        raise RuntimeError(
            "浏览器启动失败："
            f"{account['profile_path']} "
            "https://mp.weixin.qq.com/cgi-bin/home?t=home/index&token=url-secret#fragment "
            "webSocketDebuggerUrl=ws://127.0.0.1:9222/devtools/browser/socket-secret "
            "CDP_URL=http://127.0.0.1:9222/json?token=cdp-secret "
            "resumeHandle=resume-secret CODE=code-secret state=state-secret "
            "SeSsIoN=session-secret SESSION_ID=session-id-secret "
            "Authorization: Bearer authorization-secret PaSsWoRd=password-secret "
            "--remote-debugging-port=9222 "
            "Cookies=first=cookie-secret; second=cookie-secondary-secret"
        )


class MutableEvidencePublisher(FakePlatformPublisher):
    def __init__(self):
        super().__init__()
        self.override = {}

    def publish(self, payload, account, action):
        if action == "draft":
            response = {
                "source": "wechat_platform",
                "event": "draft_created",
                "receipt_id": "evidence-draft",
                "corroboration": "draft_saved",
                **self.override,
            }
            return PublishResult(
                status="platform_draft",
                platform_draft_id="evidence-draft",
                platform_url="https://mp.weixin.qq.com/draft/evidence-draft",
                response=response,
            )
        response = {
            "source": "wechat_platform",
            "event": "article_published",
            "receipt_id": "evidence-publish",
            "corroboration": "publish_success",
            **self.override,
        }
        return PublishResult(
            status="published",
            platform_publish_id="evidence-publish",
            platform_url="https://mp.weixin.qq.com/s/evidence-publish",
            response=response,
        )


class PublisherWithSensitivePublicResponse(FakePlatformPublisher):
    def publish(self, payload, account, action):
        return PublishResult(
            status="awaiting_confirmation",
            platform_url=(
                "https://mp.weixin.qq.com/cgi-bin/home?token=top-level-query-secret"
                "#top-level-fragment-secret"
            ),
            response={
                "source": "wechat_platform",
                "event": "operator_confirmation_required",
                "message": (
                    "SeSsIoN=session-value-secret SESSION_ID=session-id-value-secret "
                    "Authorization: Bearer authorization-value-secret "
                    "PASSWORD=password-value-secret "
                    "Cookies=first=cookie-value-secret; second=cookie-secondary-value-secret"
                ),
                "detail": {
                    "summary": (
                        "ToKeN=token-value-secret CODE=code-value-secret "
                        "StAtE=state-value-secret PROFILE_PATH=/tmp/value-profile-secret"
                    ),
                    "trace": [
                        "CDP_URL=http://127.0.0.1:9333/json?token=cdp-value-secret",
                        "Debugger_URL=http://localhost:9333/devtools/debug-value-secret",
                        "webSocketDebuggerUrl=ws://127.0.0.1:9333/devtools/websocket-value-secret",
                        "https://example.com/path?token=query-value-secret#fragment-value-secret PORT=9333",
                    ],
                },
                "job_context": {"resume_handle": "public-response-resume-secret"},
            },
            requires_confirmation=True,
        )


class PublisherWithNestedSecretKeys(FakePlatformPublisher):
    secret_values = (
        "resume-context-secret",
        "resumecontext-secret",
        "credentials-secret",
        "credential-secret",
        "api-key-secret",
        "apikey-secret",
        "access-token-secret",
        "accesstoken-secret",
        "refresh-token-secret",
        "cookie-key-secret",
        "token-key-secret",
        "secret-key-secret",
        "profile-path-secret",
        "debug-port-secret",
        "client-secret-variant",
        "api-token-variant",
        "cookie-jar-variant",
        "profile-dir-variant",
        "debug-info-variant",
        "password-hint-variant",
        "auth-context-variant",
        "session-cache-variant",
        "port-binding-variant",
        "storage-state-variant",
        "verification-code-variant",
        "private-key-variant",
        "raw-storage-state-secret",
        "raw-client-secret",
        "raw-api-token",
        "raw-session-secret",
        "compact-storage-state-secret",
        "compact-verification-code-secret",
        "compact-private-key-secret",
        "sessiontoken-value-secret",
        "cookieheader-value-secret",
        "clientsecretvalue-value-secret",
        "privatekeypem-value-secret",
        "portnumber-value-secret",
        "raw-sessiontoken-secret",
        "raw-cookieheader-secret",
        "raw-clientsecretvalue-secret",
        "raw-privatekeypem-secret",
        "raw-portnumber-secret",
    )

    def publish(self, payload, account, action):
        return PublishResult(
            status="published",
            platform_publish_id="nested-secret-publish-id",
            platform_url="https://mp.weixin.qq.com/s/nested-secret-publish-id",
            response={
                "source": "wechat_platform",
                "event": "article_published",
                "receipt_id": "nested-secret-publish-id",
                "title": "ordinary-title",
                "corroboration": "publish_success",
                "confirmation": "accepted",
                "delivery_status": "delivered",
                "request_id": "ordinary-request-id",
                "platform_receipt": {
                    "delivery_status": "accepted",
                    "request_id": "ordinary-platform-receipt",
                    "cookieheader": self.secret_values[34],
                },
                "platform_draft_id": "ordinary-platform-draft-id",
                "publish_id": "ordinary-publish-id",
                "url": (
                    "https://mp.weixin.qq.com/s/ordinary-publish-id"
                    f"?sessiontoken={self.secret_values[38]}#private"
                ),
                "receipt": {
                    "id": "ordinary-receipt-id",
                    "source": "api",
                    "title": "ordinary-receipt-title",
                    "request_id": "ordinary-receipt-request-id",
                    "clientsecretvalue": self.secret_values[35],
                },
                "report_id": "ordinary-report-id",
                "author_name": "ordinary-author-name",
                "sessiontoken": self.secret_values[33],
                "unknown_credentials": {
                    "clientsecretvalue": self.secret_values[35],
                    "privatekeypem": self.secret_values[36],
                    "portnumber": self.secret_values[37],
                },
                "nested": [
                    {
                        "resume_context": {"handle": self.secret_values[0]},
                        "resumeContext": self.secret_values[1],
                        "credentials": {"username": self.secret_values[2]},
                        "credential": self.secret_values[3],
                    },
                    {
                        "api_key": self.secret_values[4],
                        "apiKey": self.secret_values[5],
                        "access_token": self.secret_values[6],
                        "accessToken": self.secret_values[7],
                        "refresh_token": self.secret_values[8],
                    },
                    {
                        "cookie": self.secret_values[9],
                        "token": self.secret_values[10],
                        "secret": self.secret_values[11],
                        "profile path": self.secret_values[12],
                        "debug port": self.secret_values[13],
                    },
                    {
                        "client_secret": self.secret_values[14],
                        "api_token": self.secret_values[15],
                        "cookie_jar": self.secret_values[16],
                        "profile_dir": self.secret_values[17],
                        "debug_info": self.secret_values[18],
                    },
                    {
                        "password_hint": self.secret_values[19],
                        "auth_context": self.secret_values[20],
                        "session_cache": self.secret_values[21],
                        "port_binding": self.secret_values[22],
                    },
                    {
                        "storage_state": self.secret_values[23],
                        "verification_code": self.secret_values[24],
                        "private_key": self.secret_values[25],
                        "storagestate": self.secret_values[30],
                        "verificationcode": self.secret_values[31],
                        "privatekey": self.secret_values[32],
                    },
                ],
                "raw_values": [
                    f"storage_state={self.secret_values[26]}",
                    f"client_secret={self.secret_values[27]}",
                    f"api_token={self.secret_values[28]}",
                    f"session-cache {self.secret_values[29]}",
                    f"sessiontoken={self.secret_values[38]}",
                    f"cookieheader={self.secret_values[39]}",
                    f"clientsecretvalue={self.secret_values[40]}",
                    f"privatekeypem={self.secret_values[41]}",
                    f"portnumber={self.secret_values[42]}",
                ],
            },
        )


class ConfirmationPublisher(FakePlatformPublisher):
    def __init__(self):
        super().__init__()
        self.resume_context = None
        self.resume_calls = 0

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
        self.resume_calls += 1
        self.resume_context = job_context
        return PublishResult(
            status="published",
            platform_publish_id="publish-42",
            platform_url="https://mp.weixin.qq.com/s/publish-42",
            response={
                "source": "wechat_platform",
                "event": "article_published",
                "receipt_id": "publish-42",
                "corroboration": "publish_success",
            },
        )


def prepare_task_interrupted_during_resume(
    tmp_path: Path, name: str, execution_mode: str = "once"
):
    class SimulatedProcessExit(BaseException):
        pass

    class InterruptedResumePublisher(ConfirmationPublisher):
        def resume(self, account, job_context):
            self.resume_calls += 1
            raise SimulatedProcessExit

    database_path = tmp_path / f"interrupted-resume-{name}.db"
    upload_dir = tmp_path / f"interrupted-resume-uploads-{name}"
    publisher = InterruptedResumePublisher()
    app = create_app(
        database_path=database_path,
        upload_dir=upload_dir,
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    service = app.state.content_service
    account_id = add_authorized_account(service, f"恢复中断-{name}")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": f"恢复中断-{name}",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": f"人工确认恢复中断-{name}",
            "execution_mode": execution_mode,
            "repeat_rule": "daily" if execution_mode == "repeat" else "",
        }
    )
    waiting = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    assert waiting["status"] == "待人工确认"
    assert waiting["runs"][0]["status"] == "待人工确认"

    with pytest.raises(SimulatedProcessExit):
        service.resume_publish_job(waiting["publish_job_id"])

    interrupted = service.get_task(task["id"])
    assert interrupted["status"] == "待人工确认"
    assert interrupted["runs"][0]["status"] == "待人工确认"
    assert interrupted["publish_package"]["status"] == "awaiting_confirmation"
    assert interrupted["publish_job"]["status"] == "checking_login"
    assert publisher.resume_calls == 1
    return database_path, upload_dir, task, interrupted


class TaskRetryPublisher(FakePlatformPublisher):
    def __init__(self, retry_outcome: str):
        super().__init__()
        self.retry_outcome = retry_outcome

    def publish(self, payload, account, action):
        self.calls.append((payload, account, action))
        if len(self.calls) == 1:
            raise RuntimeError("initial platform failure")
        if self.retry_outcome == "failed":
            raise RuntimeError("retry platform failure")
        if self.retry_outcome == "awaiting_confirmation":
            return PublishResult(
                status="awaiting_confirmation",
                response={
                    "source": "wechat_platform",
                    "event": "operator_confirmation_required",
                    "job_context": {"resume_handle": "task-retry-resume-handle"},
                },
                requires_confirmation=True,
            )
        return PublishResult(
            status="published",
            platform_publish_id="task-retry-publish-id",
            platform_url="https://mp.weixin.qq.com/s/task-retry-publish-id",
            response={
                "source": "wechat_platform",
                "event": "article_published",
                "receipt_id": "task-retry-publish-id",
                "corroboration": "publish_success",
            },
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


def test_publish_job_rejects_weak_or_action_mismatched_platform_evidence(tmp_path: Path):
    publisher = MutableEvidencePublisher()
    client, project, account_id = prepare_publish_job_api(tmp_path, publisher)

    cases = [
        ("draft", {"source": "local"}),
        ("draft", {"event": "article_published"}),
        ("draft", {"receipt_id": "different-draft"}),
        ("draft", {"corroboration": "publish_success"}),
        ("publish", {"source": "local"}),
        ("publish", {"event": "draft_created"}),
        ("publish", {"receipt_id": "different-publish"}),
        ("publish", {"corroboration": "draft_api_response"}),
    ]
    for action, override in cases:
        publisher.override = override
        response = client.post(
            "/api/publish-jobs",
            json={"project_id": project["id"], "account_id": account_id, "action": action},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "failed"
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
    forbidden = [
        "/tmp/sensitive-wechat-profile",
        "url-secret",
        "fragment",
        "socket-secret",
        "cdp-secret",
        "resume-secret",
        "code-secret",
        "state-secret",
        "session-secret",
        "session-id-secret",
        "cookie-secret",
        "cookie-secondary-secret",
        "authorization-secret",
        "password-secret",
        "9222",
    ]
    persisted = client.app.state.content_service.db.fetch_one(
        "SELECT failure_reason FROM publish_jobs WHERE id = ?",
        (response.json()["id"],),
    )
    assert persisted
    for secret in forbidden:
        assert secret not in response.text
        assert secret not in persisted["failure_reason"]
    assert "https://mp.weixin.qq.com/cgi-bin/home" in persisted["failure_reason"]
    assert "?" not in persisted["failure_reason"]
    assert "#" not in persisted["failure_reason"]


def test_public_publish_response_recursively_redacts_sensitive_string_values(tmp_path: Path):
    client, project, account_id = prepare_publish_job_api(
        tmp_path, PublisherWithSensitivePublicResponse()
    )

    created = client.post(
        "/api/publish-jobs",
        json={"project_id": project["id"], "account_id": account_id, "action": "publish"},
    )
    fetched = client.get(f"/api/publish-jobs/{created.json()['id']}")

    assert created.status_code == fetched.status_code == 200
    assert created.json()["status"] == "awaiting_confirmation"
    forbidden = [
        "session-value-secret",
        "session-id-value-secret",
        "cookie-value-secret",
        "cookie-secondary-value-secret",
        "authorization-value-secret",
        "password-value-secret",
        "token-value-secret",
        "code-value-secret",
        "state-value-secret",
        "/tmp/value-profile-secret",
        "cdp-value-secret",
        "debug-value-secret",
        "websocket-value-secret",
        "query-value-secret",
        "fragment-value-secret",
        "top-level-query-secret",
        "top-level-fragment-secret",
        "public-response-resume-secret",
        "9333",
        "_attempt_token",
    ]
    for secret in forbidden:
        assert secret not in created.text
        assert secret not in fetched.text
    assert created.json()["response"] == {
        "source": "wechat_platform",
        "event": "operator_confirmation_required",
    }


def test_public_publish_job_drops_nested_normalized_secret_keys(tmp_path: Path):
    publisher = PublisherWithNestedSecretKeys()
    client, project, account_id = prepare_publish_job_api(tmp_path, publisher)

    created = client.post(
        "/api/publish-jobs",
        json={"project_id": project["id"], "account_id": account_id, "action": "publish"},
    )
    fetched = client.get(f"/api/publish-jobs/{created.json()['id']}")

    assert created.status_code == fetched.status_code == 200
    private_response = client.app.state.content_service.db.fetch_one(
        "SELECT response_json FROM publish_jobs WHERE id = ?",
        (created.json()["id"],),
    )["response_json"]
    for secret in publisher.secret_values:
        assert secret in private_response
    for response in (created, fetched):
        public_job = response.json()
        assert public_job["response"] == {
            "source": "wechat_platform",
            "event": "article_published",
            "receipt_id": "nested-secret-publish-id",
            "title": "ordinary-title",
            "corroboration": "publish_success",
            "confirmation": "accepted",
            "delivery_status": "delivered",
            "request_id": "ordinary-request-id",
            "platform_receipt": {
                "delivery_status": "accepted",
                "request_id": "ordinary-platform-receipt",
            },
            "platform_draft_id": "ordinary-platform-draft-id",
            "publish_id": "ordinary-publish-id",
            "url": "https://mp.weixin.qq.com/s/ordinary-publish-id",
            "receipt": {
                "id": "ordinary-receipt-id",
                "source": "api",
                "title": "ordinary-receipt-title",
                "request_id": "ordinary-receipt-request-id",
            },
            "report_id": "ordinary-report-id",
            "author_name": "ordinary-author-name",
        }
        for secret in publisher.secret_values:
            assert secret not in response.text


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


def test_damaged_resume_context_becomes_failed_resource(tmp_path: Path):
    publisher = ConfirmationPublisher()
    client, project, account_id = prepare_publish_job_api(tmp_path, publisher)
    job = client.post(
        "/api/publish-jobs",
        json={"project_id": project["id"], "account_id": account_id, "action": "publish"},
    ).json()
    client.app.state.content_service.db.execute(
        "UPDATE publish_jobs SET response_json = ? WHERE id = ?",
        ('{"source":"wechat_platform"}', job["id"]),
    )

    response = client.post(f"/api/publish-jobs/{job['id']}/resume")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["failure_stage"] == "恢复人工确认"
    assert publisher.resume_calls == 0
    persisted = client.app.state.content_service.db.fetch_one(
        "SELECT status, response_json FROM publish_jobs WHERE id = ?", (job["id"],)
    )
    assert persisted["status"] == "failed"
    private_response = json.loads(persisted["response_json"])
    assert len(private_response["_attempt_token"]) >= 32
    assert private_response["_attempt_token"] not in response.text


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


def test_create_publish_job_and_refresh_current_resource_are_idempotent(tmp_path: Path):
    publisher = FakePlatformPublisher()
    client, project, account_id = prepare_publish_job_api(tmp_path, publisher)
    payload = {
        "project_id": project["id"],
        "account_id": account_id,
        "action": "draft",
    }

    first = client.post("/api/publish-jobs", json=payload)
    replayed = client.post("/api/publish-jobs", json=payload)
    refreshed = client.get(
        "/api/publish-jobs/current",
        params=payload,
    )

    assert first.status_code == replayed.status_code == refreshed.status_code == 200
    assert replayed.json()["id"] == first.json()["id"]
    assert refreshed.json()["job"]["id"] == first.json()["id"]
    assert refreshed.json()["job"]["status"] == "platform_draft"
    assert len(publisher.calls) == 1
    assert "response_json" not in refreshed.text
    assert "profile_path" not in refreshed.text


def test_concurrent_publish_posts_load_before_claim_and_publish_once(tmp_path: Path):
    class LoadMarkerPublisher(FakePlatformPublisher):
        def __init__(self):
            super().__init__()
            self.load_markers = []

        def publish(self, payload, account, action):
            self.load_markers.append(payload["load_marker"])
            return super().publish(payload, account, action)

    publisher = LoadMarkerPublisher()
    client, project, account_id = prepare_publish_job_api(tmp_path, publisher)
    service = client.app.state.content_service
    pending = service.prepare_publish_job(project["id"], account_id, "draft")
    original_builder = service.publish_coordinator.payload_builder
    first_load_started = threading.Event()
    release_first_load = threading.Event()
    load_calls = 0
    load_lock = threading.Lock()

    def controlled_builder(project_row, account_name):
        nonlocal load_calls
        with load_lock:
            load_calls += 1
            call_number = load_calls
        payload = dict(original_builder(project_row, account_name))
        payload["load_marker"] = call_number
        if call_number == 1:
            first_load_started.set()
            assert release_first_load.wait(timeout=5)
        return payload

    service.publish_coordinator.payload_builder = controlled_builder
    request_payload = {
        "project_id": project["id"],
        "account_id": account_id,
        "action": "draft",
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        slow_post = executor.submit(
            client.post, "/api/publish-jobs", json=request_payload
        )
        assert first_load_started.wait(timeout=5)
        fast_post = executor.submit(
            client.post, "/api/publish-jobs", json=request_payload
        )
        try:
            fast_response = fast_post.result(timeout=5)
        finally:
            release_first_load.set()
        responses = [slow_post.result(timeout=5), fast_response]

    assert fast_response.json()["status"] == "platform_draft"
    assert [response.status_code for response in responses] == [200, 200]
    assert {response.json()["id"] for response in responses} == {pending["id"]}
    assert {response.json()["status"] for response in responses} == {
        "platform_draft"
    }
    assert publisher.load_markers == [2]
    assert len(publisher.calls) == 1


def test_current_publish_job_returns_null_for_an_action_not_started(tmp_path: Path):
    client, project, account_id = prepare_publish_job_api(
        tmp_path, FakePlatformPublisher()
    )

    response = client.get(
        "/api/publish-jobs/current",
        params={
            "project_id": project["id"],
            "account_id": account_id,
            "action": "publish",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"job": None}


def test_project_publish_job_returns_latest_bound_job_with_public_fields_only(
    tmp_path: Path,
):
    client, project, account_id = prepare_publish_job_api(
        tmp_path, FakePlatformPublisher()
    )
    empty = client.get(
        "/api/publish-jobs/project", params={"project_id": project["id"]}
    )
    draft = client.post(
        "/api/publish-jobs",
        json={
            "project_id": project["id"],
            "account_id": account_id,
            "action": "draft",
        },
    )
    published = client.post(
        "/api/publish-jobs",
        json={
            "project_id": project["id"],
            "account_id": account_id,
            "action": "publish",
        },
    )

    response = client.get(
        "/api/publish-jobs/project", params={"project_id": project["id"]}
    )

    assert empty.status_code == 200
    assert empty.json() == {"job": None}
    assert draft.status_code == published.status_code == response.status_code == 200
    assert response.json()["job"] == published.json()
    assert set(response.json()["job"]) == {
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
        "response",
    }
    assert "response_json" not in response.text
    assert "profile_path" not in response.text


@pytest.mark.parametrize(
    (
        "retry_outcome",
        "expected_task_status",
        "expected_run_status",
        "expected_package_status",
        "expected_next_run",
        "expected_failure",
    ),
    [
        ("published", "待执行", "已发布", "published", "2026-07-15 09:30", ""),
        (
            "awaiting_confirmation",
            "待人工确认",
            "待人工确认",
            "awaiting_confirmation",
            "",
            "",
        ),
        ("failed", "失败", "失败", "failed", "", "retry platform failure"),
    ],
)
def test_task_linked_publish_job_retry_api_synchronizes_local_state(
    tmp_path: Path,
    monkeypatch,
    retry_outcome: str,
    expected_task_status: str,
    expected_run_status: str,
    expected_package_status: str,
    expected_next_run: str,
    expected_failure: str,
):
    monkeypatch.setattr(services_module, "datetime", FixedServiceDatetime)
    publisher = TaskRetryPublisher(retry_outcome)
    app = create_app(
        database_path=tmp_path / f"retry-{retry_outcome}.db",
        upload_dir=tmp_path / f"uploads-{retry_outcome}",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    client = TestClient(app)
    account_id = add_authorized_account(app.state.content_service, f"retry-{retry_outcome}-account")
    task = client.post(
        "/api/tasks",
        json={
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": f"retry-{retry_outcome}-account",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "retry API 跨层同步",
            "execution_mode": "repeat",
            "repeat_rule": "daily",
        },
    ).json()
    failed = app.state.content_service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    assert failed["status"] == "失败"

    retried = client.post(f"/api/publish-jobs/{failed['publish_job_id']}/retry")
    detail = client.get(f"/api/tasks/{task['id']}").json()

    assert retried.status_code == 200
    assert retried.json()["status"] == expected_package_status
    assert detail["status"] == expected_task_status
    assert detail["runs"][0]["status"] == expected_run_status
    assert detail["publish_package"]["status"] == expected_package_status
    assert detail["next_run_at"] == expected_next_run
    assert detail["publish_job_id"] == failed["publish_job_id"]
    assert len(publisher.calls) == 2
    if expected_failure:
        assert expected_failure in detail["failure_reason"]
        assert detail["runs"][0]["failure_reason"] == detail["failure_reason"]
        assert detail["publish_package"]["failure_reason"] == detail["failure_reason"]
    else:
        assert detail["failure_reason"] == ""
        assert detail["runs"][0]["failure_reason"] == ""
        assert detail["publish_package"]["failure_reason"] == ""
    if retry_outcome == "awaiting_confirmation":
        assert detail["runs"][0]["finished_at"] == ""


def test_task_linked_retry_api_cannot_overwrite_new_generation(tmp_path: Path):
    publisher = TaskRetryPublisher("published")
    app = create_app(
        database_path=tmp_path / "stale-retry.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    client = TestClient(app)
    service = app.state.content_service
    account_id = add_authorized_account(service, "retry CAS account")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "retry CAS account",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "retry CAS",
        }
    )
    failed = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    old_package_id = failed["publish_package_id"]
    entered_finish = threading.Event()
    release_finish = threading.Event()
    original_finish = service._finish_task_from_publish_job

    def delayed_finish(*args, **kwargs):
        if kwargs.get("expected_task_status") == "失败":
            entered_finish.set()
            assert release_finish.wait(timeout=5)
        return original_finish(*args, **kwargs)

    service._finish_task_from_publish_job = delayed_finish
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, f"/api/publish-jobs/{failed['publish_job_id']}/retry")
        assert entered_finish.wait(timeout=5)
        new_job_id = service.db.execute(
            """
            INSERT INTO publish_jobs (project_id, account_id, platform, action, status)
            VALUES (?, ?, '微信公众号', 'publish', 'pending')
            """,
            (failed["content_project_id"], account_id),
        )
        new_run_id = service.db.execute(
            "INSERT INTO task_runs (task_id, status, started_at, publish_job_id) VALUES (?, '执行中', ?, ?)",
            (task["id"], "2026-07-14 10:00", new_job_id),
        )
        service.db.execute(
            """
            UPDATE scheduled_tasks
            SET status = '执行中', publish_result = '', publish_job_id = ?, publish_package_id = NULL
            WHERE id = ?
            """,
            (new_job_id, task["id"]),
        )
        release_finish.set()
        response = future.result(timeout=5)

    current = service.get_task(task["id"])
    old_package = service.db.fetch_one(
        "SELECT status FROM publish_packages WHERE id = ?", (old_package_id,)
    )
    assert response.status_code == 200
    assert response.json()["status"] == "published"
    assert current["status"] == "执行中"
    assert current["publish_job_id"] == new_job_id
    assert current["runs"][0]["id"] == new_run_id
    assert current["runs"][0]["status"] == "执行中"
    assert old_package["status"] == "failed"
    assert len(publisher.calls) == 2


def test_task_linked_retry_api_retries_local_sync_without_republishing(tmp_path: Path):
    publisher = TaskRetryPublisher("published")
    app = create_app(
        database_path=tmp_path / "retry-local-sync.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    client = TestClient(app)
    service = app.state.content_service
    account_id = add_authorized_account(service, "retry local sync account")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "retry local sync account",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "retry local sync",
        }
    )
    failed = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    run_id = failed["runs"][0]["id"]
    service.db.execute(
        f"""
        CREATE TRIGGER fail_retry_task_run_sync
        BEFORE UPDATE ON task_runs
        WHEN OLD.id = {int(run_id)} AND NEW.status = '已发布'
        BEGIN
          SELECT RAISE(ABORT, 'forced retry local sync failure');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="forced retry local sync failure"):
        client.post(f"/api/publish-jobs/{failed['publish_job_id']}/retry")

    rolled_back = service.get_task(task["id"])
    assert rolled_back["publish_job"]["status"] == "published"
    assert rolled_back["status"] == "失败"
    assert rolled_back["runs"][0]["status"] == "失败"
    assert rolled_back["publish_package"]["status"] == "failed"

    service.db.execute("DROP TRIGGER fail_retry_task_run_sync")
    retried = client.post(f"/api/publish-jobs/{failed['publish_job_id']}/retry")
    recovered = service.get_task(task["id"])

    assert retried.status_code == 200
    assert retried.json()["status"] == "published"
    assert recovered["status"] == "已发布"
    assert recovered["runs"][0]["status"] == "已发布"
    assert recovered["publish_package"]["status"] == "published"
    assert len(publisher.calls) == 2


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


def test_publish_package_failure_returns_synchronized_job_resource(tmp_path: Path):
    client, project, account_id = prepare_publish_job_api(tmp_path, PublisherWithoutReceipt())

    response = client.post(
        f"/api/content-projects/{project['id']}/publish-package",
        json={
            "platform": "微信公众号",
            "account_name": "微信公众号测试账号",
            "account_id": account_id,
        },
    )

    assert response.status_code == 200
    package = response.json()
    job = client.app.state.content_service.get_publish_job(package["publish_job_id"])
    assert package["status"] == job["status"] == "failed"
    assert package["failure_stage"] == job["failure_stage"] == "提交平台草稿"
    assert package["failure_reason"] == job["failure_reason"]
    assert package["project_id"] == job["project_id"] == project["id"]
    assert package["account_id"] == job["account_id"] == account_id


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


def test_draft_updated_at_is_returned_in_local_time(tmp_path: Path):
    db = Database(tmp_path / "content.db")
    db.init_schema()
    project_id = db.execute(
        "INSERT INTO content_projects (title, platform, current_stage) VALUES ('时区测试', '微信公众号', 'article')"
    )
    db.execute(
        """
        INSERT INTO draft_snapshots (project_id, title, platform, stage, restore_stage, snapshot_json, updated_at)
        VALUES (?, '时区测试', '微信公众号', '文章已完成，待生成图片', 'images', '{}', '2026-07-10 08:30:10')
        """,
        (project_id,),
    )
    service = ContentService(db=db, ai_client=FakeAIClient(), upload_dir=tmp_path / "uploads")

    drafts = service.list_drafts(start_date="2026-07-10", end_date="2026-07-10")

    assert drafts[0]["updated_at"] == "2026-07-10 16:30:10"


def test_legacy_publish_packages_table_gets_updated_at_column(tmp_path: Path):
    db = Database(tmp_path / "content.db")
    with db.connect() as conn:
        conn.execute(
            """
            CREATE TABLE publish_packages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              project_id INTEGER NOT NULL,
              platform TEXT NOT NULL,
              account_name TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending_publish',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO publish_packages (project_id, platform, account_name, payload_json) VALUES (1, '微信公众号', '旧账号', '{}')"
        )

    db.init_schema()

    columns = {row["name"] for row in db.fetch_all("PRAGMA table_info(publish_packages)")}
    assert "updated_at" in columns


def test_api_task_center_flow(tmp_path: Path):
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=FakePlatformPublisher(),
    )
    client = TestClient(app)
    add_authorized_account(app.state.content_service, "AI记账助手")

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

    retried_response = client.get("/api/tasks", params={"status": "已发布"})
    assert retried_response.status_code == 200
    assert retried_response.json()["tasks"][0]["status"] == "已发布"
    assert retried_response.json()["tasks"][0]["content_project_id"]


def test_accounts_auth_sync_and_analytics_flow(tmp_path: Path):
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        browser_auth_manager=FakeBrowserAuthManager(),
    )
    client = TestClient(app)

    auth_response = client.post(
        "/api/accounts/auth/start",
        json={"platform": "微信公众号", "account_name": "AI记账助手"},
    )
    assert auth_response.status_code == 200
    assert auth_response.json()["status"] == "success"
    assert auth_response.json()["session_id"] == "fake-session"
    account = auth_response.json()["account"]
    assert account["platform"] == "微信公众号"
    assert account["auth_status"] == "已授权"
    assert account["auth_method"] == "真实浏览器扫码"
    assert "profile_path" not in account
    assert app.state.content_service.get_account(account["id"])["profile_path"]
    assert account["external_id"] == "real-platform-id"
    assert account["owner"] == "未分配"
    assert account["tag"] == ""

    accounts_response = client.get("/api/accounts", params={"platform": "微信公众号"})
    assert accounts_response.status_code == 200
    assert accounts_response.json()["accounts"][0]["name"] == "AI记账助手"

    sync_response = client.post(f"/api/accounts/{account['id']}/sync")
    assert sync_response.status_code == 200
    assert sync_response.json()["synced"] is True
    assert sync_response.json()["source"] == "browser"

    account_analytics_response = client.get("/api/analytics/accounts", params={"range": "30d"})
    assert account_analytics_response.status_code == 200
    account_rows = account_analytics_response.json()["accounts"]
    assert account_rows[0]["account_name"] == "AI记账助手"
    assert account_rows[0]["fans"] == 321
    assert account_rows[0]["reads"] == 654

    work_analytics_response = client.get("/api/analytics/works", params={"range": "30d"})
    assert work_analytics_response.status_code == 200
    works = work_analytics_response.json()["works"]
    assert works[0]["account_name"] == "AI记账助手"
    assert works[0]["title"] == "真实平台作品"
    assert works[0]["reads"] == 144


def test_account_auth_api_returns_waiting_then_poll_success_and_timeout(tmp_path: Path):
    class SequencedBrowserAuthManager:
        def __init__(self):
            self.sessions = {}
            self.on_success = None
            self.counter = 0

        def start(self, platform, account_name, on_success):
            self.counter += 1
            session_id = f"session-{self.counter}"
            self.on_success = on_success
            self.sessions[session_id] = {
                "session_id": session_id,
                "platform": platform,
                "status": "waiting",
                "message": "等待扫码登录",
                "login_url": "https://mp.weixin.qq.com/",
                "account": None,
                "error": "",
            }
            return dict(self.sessions[session_id])

        def succeed(self, session_id):
            account = self.on_success(
                {
                    "platform": "微信公众号",
                    "account_name": "轮询成功账号",
                    "external_id": "poll-success-bizuin",
                    "profile_path": "/tmp/poll-success-profile",
                    "login_url": "https://mp.weixin.qq.com/cgi-bin/home?token=private",
                }
            )
            self.sessions[session_id].update(
                status="success", message="登录成功", account=account
            )

        def timeout(self, session_id):
            self.sessions[session_id].update(
                status="failed",
                message="登录超时，请重新发起登录",
                error="登录超时，请重新发起登录",
            )

        def get_status(self, session_id):
            return dict(self.sessions[session_id])

        def cancel(self, session_id):
            self.sessions[session_id]["status"] = "cancelled"
            return dict(self.sessions[session_id])

        def close_all(self):
            return None

    browser_auth = SequencedBrowserAuthManager()
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        browser_auth_manager=browser_auth,
        scheduler_enabled=False,
    )
    client = TestClient(app)

    waiting = client.post(
        "/api/accounts/auth/start", json={"platform": "微信公众号"}
    )
    assert waiting.status_code == 200
    assert waiting.json()["status"] == "waiting"
    assert waiting.json()["account"] is None

    browser_auth.succeed(waiting.json()["session_id"])
    succeeded = client.get(f"/api/accounts/auth/{waiting.json()['session_id']}")
    assert succeeded.status_code == 200
    assert succeeded.json()["status"] == "success"
    assert succeeded.json()["account"]["name"] == "轮询成功账号"
    assert "profile_path" not in succeeded.text
    assert "token=private" not in succeeded.text

    second = client.post(
        "/api/accounts/auth/start", json={"platform": "微信公众号"}
    ).json()
    browser_auth.timeout(second["session_id"])
    timed_out = client.get(f"/api/accounts/auth/{second['session_id']}")
    assert timed_out.status_code == 200
    assert timed_out.json()["status"] == "failed"
    assert "登录超时" in timed_out.json()["error"]


def test_account_reauthorization_updates_profile_and_stable_identity_fields(tmp_path: Path):
    db = Database(tmp_path / "content.db")
    db.init_schema()
    service = ContentService(
        db=db,
        ai_client=FakeAIClient(),
        upload_dir=tmp_path / "uploads",
        platform_publisher=FakePlatformPublisher(),
    )
    first = service._complete_account_auth(
        {
            "platform": "微信公众号",
            "account_name": "旧账号名",
            "external_id": "stable-bizuin",
            "profile_path": "/tmp/old-profile",
            "login_url": "https://mp.weixin.qq.com/cgi-bin/home?token=old",
        }
    )

    second = service._complete_account_auth(
        {
            "platform": "微信公众号",
            "account_name": "真实账号名",
            "external_id": "stable-bizuin",
            "profile_path": "/tmp/new-profile",
            "login_url": "https://mp.weixin.qq.com/cgi-bin/home?token=new",
        }
    )

    private_account = service.get_account(first["id"])
    assert second["id"] == first["id"]
    assert second["name"] == "真实账号名"
    assert second["external_id"] == "stable-bizuin"
    assert private_account["profile_path"] == "/tmp/new-profile"
    assert "token=new" in private_account["login_url"]


def test_account_auth_does_not_merge_different_stable_ids_with_same_name(tmp_path: Path):
    db = Database(tmp_path / "content.db")
    db.init_schema()
    service = ContentService(
        db=db,
        ai_client=FakeAIClient(),
        upload_dir=tmp_path / "uploads",
        platform_publisher=FakePlatformPublisher(),
    )
    first = service._complete_account_auth(
        {
            "platform": "微信公众号",
            "account_name": "同名公众号",
            "external_id": "first-bizuin",
            "profile_path": "/tmp/first-profile",
            "login_url": "https://mp.weixin.qq.com/",
        }
    )

    second = service._complete_account_auth(
        {
            "platform": "微信公众号",
            "account_name": "同名公众号",
            "external_id": "second-bizuin",
            "profile_path": "/tmp/second-profile",
            "login_url": "https://mp.weixin.qq.com/",
        }
    )

    assert second["id"] != first["id"]
    assert service.get_account(first["id"])["external_id"] == "first-bizuin"
    assert service.get_account(second["id"])["external_id"] == "second-bizuin"


@pytest.mark.parametrize("missing_external_id", ["", "   "])
def test_account_reauthorization_without_stable_id_fails_without_overwriting_existing_account(
    tmp_path: Path, missing_external_id: str,
):
    db = Database(tmp_path / "content.db")
    db.init_schema()
    service = ContentService(
        db=db,
        ai_client=FakeAIClient(),
        upload_dir=tmp_path / "uploads",
        platform_publisher=FakePlatformPublisher(),
    )
    existing = service._complete_account_auth(
        {
            "platform": "微信公众号",
            "account_name": "同名公众号",
            "external_id": "stable-bizuin",
            "profile_path": "/tmp/original-profile",
            "login_url": "https://mp.weixin.qq.com/cgi-bin/home?token=original",
        }
    )

    with pytest.raises(RuntimeError, match="稳定账号 ID.*重新扫码"):
        service._complete_account_auth(
            {
                "platform": "微信公众号",
                "account_name": "同名公众号",
                "external_id": missing_external_id,
                "profile_path": "/tmp/replacement-profile",
                "login_url": "https://mp.weixin.qq.com/cgi-bin/home?token=replacement",
            }
        )

    unchanged = service.get_account(existing["id"])
    assert unchanged["external_id"] == "stable-bizuin"
    assert unchanged["profile_path"] == "/tmp/original-profile"
    assert "token=original" in unchanged["login_url"]
    assert db.fetch_one(
        "SELECT COUNT(*) AS count FROM platform_accounts WHERE platform = '微信公众号'"
    )["count"] == 1


def test_account_management_update_cannot_overwrite_authenticated_identity(tmp_path: Path):
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        browser_auth_manager=FakeBrowserAuthManager(),
        scheduler_enabled=False,
    )
    client = TestClient(app)
    account = client.post(
        "/api/accounts/auth/start",
        json={"platform": "微信公众号", "account_name": "真实身份账号"},
    ).json()["account"]

    updated = client.patch(
        f"/api/accounts/{account['id']}",
        json={
            "name": "伪造账号名",
            "external_id": "forged-bizuin",
            "owner": "新运营人",
            "tag": "新标签",
        },
    )

    assert updated.status_code == 200
    assert updated.json()["name"] == "真实身份账号"
    assert updated.json()["external_id"] == "real-platform-id"
    assert updated.json()["owner"] == "新运营人"
    assert updated.json()["tag"] == "新标签"


def test_account_sync_rejects_wrong_stable_identity_before_writing_metrics(tmp_path: Path):
    db = Database(tmp_path / "content.db")
    db.init_schema()
    service = ContentService(
        db=db,
        ai_client=FakeAIClient(),
        upload_dir=tmp_path / "uploads",
        platform_publisher=FakePlatformPublisher(),
    )
    account_id = db.execute(
        """
        INSERT INTO platform_accounts (platform, name, external_id, auth_status)
        VALUES ('微信公众号', '所选公众号', 'selected-bizuin', '已授权')
        """
    )

    with pytest.raises(RuntimeError, match="账号身份与所选账号不一致"):
        service._persist_account_sync(
            account_id,
            {
                "source": "browser",
                "account_name": "所选公众号",
                "external_id": "wrong-bizuin",
                "metrics": {"fans": 999},
                "works": [],
            },
        )

    account = service.get_account(account_id)
    assert account["external_id"] == "selected-bizuin"
    assert db.fetch_one(
        "SELECT COUNT(*) AS count FROM account_metric_snapshots WHERE account_id = ?",
        (account_id,),
    )["count"] == 0


@pytest.mark.parametrize(
    ("stored_external_id", "observed_external_id"),
    [("selected-bizuin", ""), ("", "observed-bizuin"), ("", "")],
)
def test_wechat_account_sync_rejects_missing_stable_identity_even_when_name_matches(
    tmp_path: Path, stored_external_id: str, observed_external_id: str
):
    db = Database(tmp_path / "content.db")
    db.init_schema()
    service = ContentService(
        db=db,
        ai_client=FakeAIClient(),
        upload_dir=tmp_path / "uploads",
        platform_publisher=FakePlatformPublisher(),
    )
    account_id = db.execute(
        """
        INSERT INTO platform_accounts (platform, name, external_id, auth_status)
        VALUES ('微信公众号', '所选公众号', ?, '已授权')
        """,
        (stored_external_id,),
    )

    with pytest.raises(RuntimeError, match="账号身份与所选账号不一致"):
        service._persist_account_sync(
            account_id,
            {
                "source": "browser",
                "account_name": "所选公众号",
                "external_id": observed_external_id,
                "metrics": {"fans": 999},
                "works": [],
            },
        )

    assert db.fetch_one(
        "SELECT COUNT(*) AS count FROM account_metric_snapshots WHERE account_id = ?",
        (account_id,),
    )["count"] == 0


def test_api_write_rejects_untrusted_origin_but_allows_trusted_and_originless_calls(tmp_path: Path):
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        scheduler_enabled=False,
    )
    client = TestClient(app)
    payload = {"title": "Origin 防护", "platform": "微信公众号"}

    malicious = client.post(
        "/api/content-projects",
        json=payload,
        headers={"Origin": "https://evil.example"},
    )
    trusted = client.post(
        "/api/content-projects",
        json=payload,
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    internal = client.post("/api/content-projects", json=payload)

    assert malicious.status_code == 403
    assert malicious.json()["detail"] == "请求来源不受信任"
    assert trusted.status_code == 200
    assert trusted.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert internal.status_code == 200


def test_app_rejects_wildcard_trusted_origin_configuration(tmp_path: Path):
    with pytest.raises(RuntimeError, match="明确的可信 Origin"):
        create_app(
            database_path=tmp_path / "content.db",
            upload_dir=tmp_path / "uploads",
            ai_client=FakeAIClient(),
            scheduler_enabled=False,
            trusted_origins=["*"],
        )


def test_scheduler_dispatches_blocking_maintenance_to_worker_thread():
    source = inspect.getsource(create_app)
    maintenance_source = source[
        source.index("def run_scheduler_maintenance") : source.index(
            "async def task_scheduler_loop"
        )
    ]
    scheduler_source = source[
        source.index("async def task_scheduler_loop") : source.index("@asynccontextmanager")
    ]

    assert "service.recover_publish_jobs()" in maintenance_source
    assert "await asyncio.to_thread" in scheduler_source
    assert "service.sync_due_accounts()" not in scheduler_source
    assert "service.execute_due_tasks()" not in scheduler_source


def test_scheduler_maintenance_continues_after_publish_recovery_step_fails(tmp_path: Path):
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        browser_auth_manager=FakeBrowserAuthManager(),
        scheduler_enabled=True,
    )
    service = app.state.content_service
    calls = []
    due_tasks_called = threading.Event()

    service.recover_stale_awaiting_confirmations = lambda: calls.append("confirmations")

    def fail_publish_recovery():
        calls.append("publish_jobs")
        raise RuntimeError("broken pending job")

    service.recover_publish_jobs = fail_publish_recovery
    service.sync_due_accounts = lambda: calls.append("accounts")

    def execute_due_tasks():
        calls.append("due_tasks")
        due_tasks_called.set()

    service.execute_due_tasks = execute_due_tasks

    with TestClient(app):
        assert due_tasks_called.wait(timeout=2)

    assert calls[:4] == ["confirmations", "publish_jobs", "accounts", "due_tasks"]


def test_referenced_accounts_cannot_be_deleted_but_unreferenced_account_can(tmp_path: Path):
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        scheduler_enabled=False,
    )
    client = TestClient(app)
    service = app.state.content_service
    task_account_id = add_authorized_account(service, "任务引用账号")
    job_account_id = add_authorized_account(service, "发布引用账号")
    free_account_id = add_authorized_account(service, "无引用账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": task_account_id,
            "account_name": "任务引用账号",
            "execute_time": "2026-07-16 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "账号删除引用保护",
        }
    )
    project = service.create_project("发布历史引用保护", platform="微信公众号")
    job_id = service.db.execute(
        """
        INSERT INTO publish_jobs (project_id, account_id, platform, action, status)
        VALUES (?, ?, '微信公众号', 'draft', 'pending')
        """,
        (project["id"], job_account_id),
    )

    task_conflict = client.delete(f"/api/accounts/{task_account_id}")
    job_conflict = client.delete(f"/api/accounts/{job_account_id}")
    deleted = client.delete(f"/api/accounts/{free_account_id}")

    assert task_conflict.status_code == 409
    assert job_conflict.status_code == 409
    assert "引用" in task_conflict.json()["detail"]
    assert "引用" in job_conflict.json()["detail"]
    assert service.get_task(task["id"])["account_id"] == task_account_id
    assert service.get_publish_job(job_id)["account_id"] == job_account_id
    assert service.get_public_account(task_account_id)["name"] == "任务引用账号"
    assert service.get_public_account(job_account_id)["name"] == "发布引用账号"
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": free_account_id}


def test_public_account_endpoints_use_safe_whitelist_but_sync_receives_private_record(tmp_path: Path):
    class CapturingBrowserAuthManager(FakeBrowserAuthManager):
        def __init__(self):
            super().__init__()
            self.synced_account = None

        def sync(self, account):
            self.synced_account = dict(account)
            return super().sync(account)

    browser_auth = CapturingBrowserAuthManager()
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        browser_auth_manager=browser_auth,
        scheduler_enabled=False,
    )
    client = TestClient(app)
    private_values = (
        "/private/browser-profile/account-secret",
        "access-token-secret",
        "cookie-session-secret",
        "debug-port-9444",
        "credentials-secret",
    )
    account_id = app.state.content_service.db.execute(
        """
        INSERT INTO platform_accounts (
          platform, name, external_id, auth_status, profile_path, login_url, qr_url, sync_error
        ) VALUES ('微信公众号', '公开白名单账号', 'safe-external-id', '已授权', ?, ?, ?, ?)
        """,
        (
            private_values[0],
            f"http://127.0.0.1:9444/login?token={private_values[1]}&cookie={private_values[2]}",
            f"credentials={private_values[4]}",
            f"debug_port={private_values[3]} token={private_values[1]}",
        ),
    )

    listed = client.get("/api/accounts")
    detail = client.get(f"/api/accounts/{account_id}")
    synced = client.post(f"/api/accounts/{account_id}/sync")

    assert listed.status_code == detail.status_code == synced.status_code == 200
    public_fields = {
        "id",
        "platform",
        "name",
        "external_id",
        "owner",
        "tag",
        "auth_status",
        "monitor_status",
        "sync_status",
        "role",
        "audience",
        "style",
        "taboo",
        "preference",
        "auth_method",
        "last_sync_at",
        "created_at",
        "updated_at",
    }
    public_accounts = (
        listed.json()["accounts"][0],
        detail.json(),
        synced.json()["account"],
    )
    for response, account in zip((listed, detail, synced), public_accounts):
        assert set(account) == public_fields
        assert account["id"] == account_id
        assert account["external_id"] == "safe-external-id"
        for private_value in private_values:
            assert private_value not in response.text
        for private_field in ("profile_path", "login_url", "qr_url", "sync_error"):
            assert private_field not in account
    assert browser_auth.synced_account["profile_path"] == private_values[0]
    assert private_values[1] in browser_auth.synced_account["login_url"]


def test_publish_package_uses_latest_authorized_account_by_id(tmp_path: Path):
    publisher = FakePlatformPublisher()
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        browser_auth_manager=FakeBrowserAuthManager(),
        platform_publisher=publisher,
    )
    client = TestClient(app)
    account = client.post(
        "/api/accounts/auth/start",
        json={"platform": "微信公众号", "account_name": "真实授权账号"},
    ).json()["account"]
    project = client.post(
        "/api/content-projects",
        json={"title": "发布账号绑定测试", "platform": "微信公众号"},
    ).json()
    article = client.post(f"/api/content-projects/{project['id']}/generate-article").json()
    assets = client.post(f"/api/content-projects/{project['id']}/generate-images").json()["assets"]
    cover = next(asset for asset in assets if asset["asset_type"] == "cover")
    inline = next(asset for asset in assets if asset["asset_type"] == "inline")
    client.patch(
        f"/api/content-projects/{project['id']}/layout",
        json={
            "blocks": [
                {"section_type": "title", "content": project["title"], "media_asset_id": cover["id"], "sort_order": 1},
                {"section_type": "intro", "content": article["blocks"][0]["content"], "media_asset_id": None, "sort_order": 2},
                {"section_type": "body", "content": article["blocks"][1]["content"], "media_asset_id": inline["id"], "sort_order": 3},
            ]
        },
    )

    response = client.post(
        f"/api/content-projects/{project['id']}/publish-package",
        json={
            "platform": "微信公众号",
            "account_id": account["id"],
            "account_name": "前端缓存的旧账号名",
        },
    )

    assert response.status_code == 200
    assert response.json()["account_id"] == account["id"]
    assert response.json()["account_name"] == "真实授权账号"
    assert response.json()["status"] == "platform_draft"
    assert response.json()["platform_draft_id"] == "wechat-draft-media-id"
    assert publisher.calls[0][1]["id"] == account["id"]
    assert publisher.calls[0][2] == "draft"


def test_task_platform_failure_copies_sanitized_publish_job_failure(tmp_path: Path):
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        browser_auth_manager=FakeBrowserAuthManager(),
        platform_publisher=PublisherWithSensitiveFailure(),
    )
    client = TestClient(app)
    account = client.post(
        "/api/accounts/auth/start",
        json={"platform": "微信公众号", "account_name": "理想说AI"},
    ).json()["account"]
    task = client.post(
        "/api/tasks",
        json={
            "platform": "微信公众号",
            "account_id": account["id"],
            "account_name": "理想说AI",
            "execute_time": "2026-07-13 10:13",
            "task_type": "微信公众号图文生成",
            "topic": "财务部是公司一笔不小的开支",
        },
    ).json()

    app.state.content_service.execute_due_tasks(datetime(2026, 7, 13, 10, 13))

    detail = client.get(f"/api/tasks/{task['id']}").json()
    assert detail["status"] == "失败"
    assert detail["failure_stage"] == "发布文章"
    assert "已隐藏浏览器上下文" in detail["failure_reason"]
    assert "sensitive-wechat-profile" not in detail["failure_reason"]
    assert detail["publish_job"]["status"] == "failed"
    assert detail["publish_package"]["status"] == "failed"
    assert detail["publish_package"]["platform_draft_id"] == ""


def test_task_defaults_to_direct_publish_and_returns_public_publish_job(tmp_path: Path):
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        browser_auth_manager=FakeBrowserAuthManager(),
        platform_publisher=FakePlatformPublisher(),
    )
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
    assert task_response.json()["publish_action"] == "publish"

    retry_response = client.post("/api/tasks/retry", json={"task_ids": []})
    assert retry_response.status_code == 200

    app.state.content_service.execute_due_tasks(datetime(2026, 7, 8, 9, 31))
    detail_response = client.get(f"/api/tasks/{task_response.json()['id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["status"] == "已发布"
    assert detail["publish_result"] == "已发布"
    assert detail["publish_job_id"]
    assert detail["publish_job"]["status"] == "published"
    assert detail["publish_job"]["action"] == "publish"
    assert "job_context" not in detail["publish_job"].get("response", {})
    assert detail["runs"][0]["publish_job_id"] == detail["publish_job_id"]
    assert detail["publish_package"]["status"] == "published"
    assert detail["publish_package"]["account_name"] == "AI记账助手"


def test_task_publish_action_can_be_updated_to_draft_and_rejects_invalid_values(tmp_path: Path):
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=FakePlatformPublisher(),
        scheduler_enabled=False,
    )
    client = TestClient(app)
    account_id = add_authorized_account(app.state.content_service, "动作测试账号")
    payload = {
        "platform": "微信公众号",
        "account_id": account_id,
        "account_name": "动作测试账号",
        "execute_time": "2026-07-14 09:30",
        "task_type": "微信公众号图文生成",
        "topic": "动作合同测试",
    }
    created = client.post("/api/tasks", json=payload)

    updated = client.patch(
        f"/api/tasks/{created.json()['id']}",
        json={**payload, "publish_action": "draft"},
    )
    invalid_create = client.post("/api/tasks", json={**payload, "publish_action": "preview"})
    invalid_update = client.patch(
        f"/api/tasks/{created.json()['id']}",
        json={**payload, "publish_action": "preview"},
    )

    assert created.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["publish_action"] == "draft"
    assert client.get("/api/tasks").json()["tasks"][0]["publish_action"] == "draft"
    assert invalid_create.status_code == 422
    assert invalid_update.status_code == 422


def test_task_create_and_update_reject_platform_account_mismatch(tmp_path: Path):
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=FakePlatformPublisher(),
        scheduler_enabled=False,
    )
    client = TestClient(app)
    account_id = add_authorized_account(app.state.content_service, "平台一致性账号")
    payload = {
        "platform": "微信公众号",
        "account_id": account_id,
        "account_name": "平台一致性账号",
        "execute_time": "2026-07-14 09:30",
        "task_type": "微信公众号图文生成",
        "topic": "平台一致性测试",
    }

    invalid_create = client.post("/api/tasks", json={**payload, "platform": "小红书"})
    task = client.post("/api/tasks", json=payload).json()
    invalid_update = client.patch(
        f"/api/tasks/{task['id']}",
        json={**payload, "platform": "小红书"},
    )

    assert invalid_create.status_code == 400
    assert invalid_update.status_code == 400
    assert "任务平台与账号平台不一致" in invalid_create.json()["detail"]
    assert "任务平台与账号平台不一致" in invalid_update.json()["detail"]
    assert client.get(f"/api/tasks/{task['id']}").json()["platform"] == "微信公众号"


def test_task_publishing_rejects_unavailable_platform_on_create_update_and_execute(tmp_path: Path):
    publisher = FakePlatformPublisher()
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    client = TestClient(app)
    wechat_id = add_authorized_account(app.state.content_service, "可用公众号")
    xhs_id = add_authorized_account(app.state.content_service, "不可用小红书", platform="小红书")
    payload = {
        "platform": "微信公众号",
        "account_id": wechat_id,
        "account_name": "可用公众号",
        "execute_time": "2026-07-14 09:30",
        "task_type": "微信公众号图文生成",
        "topic": "平台边界测试",
    }

    invalid_create = client.post(
        "/api/tasks",
        json={
            **payload,
            "platform": "小红书",
            "account_id": xhs_id,
            "account_name": "不可用小红书",
            "task_type": "小红书图文生成（预留）",
        },
    )
    task = client.post("/api/tasks", json=payload).json()
    invalid_update = client.patch(
        f"/api/tasks/{task['id']}",
        json={
            **payload,
            "platform": "小红书",
            "account_id": xhs_id,
            "account_name": "不可用小红书",
            "task_type": "小红书图文生成（预留）",
        },
    )
    app.state.content_service.db.execute(
        "UPDATE scheduled_tasks SET platform = '小红书' WHERE id = ?",
        (task["id"],),
    )

    executed = app.state.content_service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))

    assert invalid_create.status_code == 400
    assert invalid_update.status_code == 400
    assert "仅支持微信公众号" in invalid_create.json()["detail"]
    assert "仅支持微信公众号" in invalid_update.json()["detail"]
    assert executed["status"] == "失败"
    assert "仅支持微信公众号" in executed["failure_reason"]
    assert publisher.calls == []


def test_draft_task_remains_distinct_from_direct_publish(tmp_path: Path):
    service = make_task_service(tmp_path)
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_name": "AI记账助手",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "只生成平台草稿",
            "publish_action": "draft",
        }
    )

    detail = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))

    assert detail["status"] == "已生成平台草稿"
    assert detail["publish_result"] == "已生成平台草稿"
    assert detail["publish_job"]["status"] == "platform_draft"
    assert detail["publish_job"]["action"] == "draft"
    assert detail["runs"][0]["status"] == "已生成平台草稿"


def test_repeat_task_waits_for_confirmation_before_scheduling_next_run(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(services_module, "datetime", FixedServiceDatetime)
    publisher = ConfirmationPublisher()
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    client = TestClient(app)
    account_id = add_authorized_account(app.state.content_service, "人工确认账号")
    task = client.post(
        "/api/tasks",
        json={
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "人工确认账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "确认后再排期",
            "execution_mode": "repeat",
            "repeat_rule": "daily",
        },
    ).json()

    waiting = app.state.content_service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))

    assert waiting["status"] == "待人工确认"
    assert waiting["publish_result"] == "待人工确认"
    assert waiting["next_run_at"] == ""
    assert waiting["runs"][0]["status"] == "待人工确认"
    assert waiting["runs"][0]["finished_at"] == ""
    assert "sensitive-browser-handle" not in json.dumps(waiting, ensure_ascii=False)
    public_job_text = json.dumps(waiting["publish_job"], ensure_ascii=False).lower()
    for private_field in ("resume_context", "token", "credentials", "job_context"):
        assert private_field not in public_job_text

    scan = app.state.content_service.execute_due_tasks(datetime(2026, 7, 14, 9, 31))
    still_waiting = client.get(f"/api/tasks/{task['id']}").json()

    assert scan["executed"] == 0
    assert still_waiting["status"] == "待人工确认"
    assert still_waiting["publish_job"]["status"] == "awaiting_confirmation"
    assert publisher.resume_calls == 0

    resumed = client.post(f"/api/publish-jobs/{waiting['publish_job_id']}/resume")
    final_task = client.get(f"/api/tasks/{task['id']}").json()

    assert resumed.status_code == 200
    assert resumed.json()["status"] == "published"
    assert final_task["status"] == "待执行"
    assert final_task["publish_result"] == "已发布"
    assert final_task["next_run_at"] == "2026-07-15 09:30"
    assert final_task["runs"][0]["status"] == "已发布"
    assert final_task["runs"][0]["finished_at"]


def test_task_list_hydrates_linked_public_publish_job(tmp_path: Path):
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=ConfirmationPublisher(),
        scheduler_enabled=False,
    )
    client = TestClient(app)
    account_id = add_authorized_account(app.state.content_service, "列表发布账号")
    task = client.post(
        "/api/tasks",
        json={
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "列表发布账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "列表加载发布作业",
        },
    ).json()
    app.state.content_service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))

    response = client.get("/api/tasks")

    assert response.status_code == 200
    listed = response.json()["tasks"][0]
    assert listed["publish_job_id"]
    assert listed["publish_job"]["status"] == "awaiting_confirmation"
    assert listed["publish_job"]["requires_confirmation"] is True
    assert "sensitive-browser-handle" not in response.text
    public_job_text = json.dumps(listed["publish_job"], ensure_ascii=False).lower()
    for private_field in ("resume_context", "token", "credentials", "job_context"):
        assert private_field not in public_job_text


def test_task_list_and_detail_drop_nested_publish_job_secrets(tmp_path: Path):
    publisher = PublisherWithNestedSecretKeys()
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    client = TestClient(app)
    account_id = add_authorized_account(app.state.content_service, "嵌套秘密账号")
    task = client.post(
        "/api/tasks",
        json={
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "嵌套秘密账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "公开任务资源不得泄漏秘密",
        },
    ).json()
    app.state.content_service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))

    listed = client.get("/api/tasks")
    detail = client.get(f"/api/tasks/{task['id']}")

    assert listed.status_code == detail.status_code == 200
    for response, public_job in (
        (listed, listed.json()["tasks"][0]["publish_job"]),
        (detail, detail.json()["publish_job"]),
    ):
        assert public_job["response"] == {
            "source": "wechat_platform",
            "event": "article_published",
            "receipt_id": "nested-secret-publish-id",
            "title": "ordinary-title",
            "corroboration": "publish_success",
            "confirmation": "accepted",
            "delivery_status": "delivered",
            "request_id": "ordinary-request-id",
            "platform_receipt": {
                "delivery_status": "accepted",
                "request_id": "ordinary-platform-receipt",
            },
            "platform_draft_id": "ordinary-platform-draft-id",
            "publish_id": "ordinary-publish-id",
            "url": "https://mp.weixin.qq.com/s/ordinary-publish-id",
            "receipt": {
                "id": "ordinary-receipt-id",
                "source": "api",
                "title": "ordinary-receipt-title",
                "request_id": "ordinary-receipt-request-id",
            },
            "report_id": "ordinary-report-id",
            "author_name": "ordinary-author-name",
        }
        for secret in publisher.secret_values:
            assert secret not in response.text


def test_initial_task_execution_links_pending_local_records_before_platform_returns(tmp_path: Path):
    class BlockingPublisher(FakePlatformPublisher):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def publish(self, payload, account, action):
            self.entered.set()
            assert self.release.wait(timeout=5)
            return super().publish(payload, account, action)

    publisher = BlockingPublisher()
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    service = app.state.content_service
    account_id = add_authorized_account(service, "首次执行窗口账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "首次执行窗口账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "平台返回前建立本地关联",
        }
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.execute_task, task["id"], datetime(2026, 7, 14, 9, 30))
        assert publisher.entered.wait(timeout=5)
        task_during_publish = service.db.fetch_one(
            "SELECT status, publish_package_id, publish_job_id FROM scheduled_tasks WHERE id = ?",
            (task["id"],),
        )
        run_during_publish = service.db.fetch_one(
            """
            SELECT status, publish_package_id, publish_job_id
            FROM task_runs WHERE task_id = ? ORDER BY id DESC LIMIT 1
            """,
            (task["id"],),
        )
        job_during_publish = service.db.fetch_one(
            "SELECT id, status FROM publish_jobs ORDER BY id DESC LIMIT 1"
        )
        package_during_publish = service.db.fetch_one(
            "SELECT id, status, publish_job_id FROM publish_packages ORDER BY id DESC LIMIT 1"
        )
        publisher.release.set()
        final = future.result(timeout=5)

    assert job_during_publish["status"] == "checking_login"
    assert task_during_publish == {
        "status": "执行中",
        "publish_package_id": package_during_publish["id"],
        "publish_job_id": job_during_publish["id"],
    }
    assert run_during_publish == {
        "status": "执行中",
        "publish_package_id": package_during_publish["id"],
        "publish_job_id": job_during_publish["id"],
    }
    assert package_during_publish == {
        "id": package_during_publish["id"],
        "status": "pending_publish",
        "publish_job_id": job_during_publish["id"],
    }
    assert final["status"] == "已发布"
    assert final["runs"][0]["status"] == "已发布"
    assert final["publish_package"]["status"] == "published"


def test_initial_task_finalization_rollback_stays_active_until_local_recovery(tmp_path: Path):
    publisher = FakePlatformPublisher()
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    service = app.state.content_service
    account_id = add_authorized_account(service, "首次事务回滚账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "首次事务回滚账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "首次最终化原子回滚",
        }
    )
    service.db.execute(
        """
        CREATE TRIGGER fail_initial_task_run_finalization
        BEFORE UPDATE ON task_runs
        WHEN NEW.status = '已发布'
        BEGIN
          SELECT RAISE(ABORT, 'forced initial task run finalization failure');
        END
        """
    )

    pending_recovery = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))

    assert pending_recovery["status"] == "执行中"
    assert pending_recovery["publish_job"]["status"] == "published"
    assert pending_recovery["runs"][0]["status"] == "执行中"
    assert pending_recovery["publish_package"]["status"] == "pending_publish"
    assert pending_recovery["failure_stage"] == ""
    assert pending_recovery["failure_reason"] == ""
    original_job_id = pending_recovery["publish_job_id"]

    service.db.execute("DROP TRIGGER fail_initial_task_run_finalization")
    scan = service.execute_due_tasks(datetime(2026, 7, 14, 9, 31))
    recovered = service.get_task(task["id"])

    assert scan["executed"] == 0
    assert recovered["status"] == "已发布"
    assert recovered["publish_job_id"] == original_job_id
    assert recovered["publish_package"]["status"] == "published"
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1
    assert len(publisher.calls) == 1


def test_repeated_local_finalization_failures_remain_pending_without_republishing(tmp_path: Path):
    publisher = FakePlatformPublisher()
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    service = app.state.content_service
    account_id = add_authorized_account(service, "连续最终化失败账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "连续最终化失败账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "连续本地事务失败不得重发",
        }
    )
    service.db.execute(
        """
        CREATE TRIGGER keep_failing_task_run_finalization
        BEFORE UPDATE ON task_runs
        WHEN NEW.status = '已发布'
        BEGIN
          SELECT RAISE(ABORT, 'persistent local finalization failure');
        END
        """
    )

    first = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    first_scan = service.execute_due_tasks(datetime(2026, 7, 14, 9, 31))
    second_scan = service.execute_due_tasks(datetime(2026, 7, 14, 9, 32))
    still_pending = service.get_task(task["id"])

    assert first["status"] == "执行中"
    assert first_scan["executed"] == second_scan["executed"] == 0
    assert still_pending["status"] == "执行中"
    assert still_pending["runs"][0]["status"] == "执行中"
    assert still_pending["publish_job"]["status"] == "published"
    assert still_pending["publish_package"]["status"] == "pending_publish"
    assert still_pending["failure_stage"] == ""
    assert still_pending["failure_reason"] == ""
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1
    assert len(publisher.calls) == 1


def test_scheduler_recovers_interrupted_initial_finalization_without_republishing(tmp_path: Path):
    class ProcessInterrupted(BaseException):
        pass

    publisher = FakePlatformPublisher()
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    service = app.state.content_service
    account_id = add_authorized_account(service, "进程恢复账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "进程恢复账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "平台成功后进程中断",
        }
    )
    original_finish = service._finish_task_from_publish_job
    interrupted = False

    def interrupt_first_finalization(*args, **kwargs):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise ProcessInterrupted("forced process interruption")
        return original_finish(*args, **kwargs)

    service._finish_task_from_publish_job = interrupt_first_finalization
    with pytest.raises(ProcessInterrupted, match="forced process interruption"):
        service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    service._finish_task_from_publish_job = original_finish

    interrupted_task = service.get_task(task["id"])
    assert interrupted_task["status"] == "执行中"
    assert interrupted_task["publish_job"]["status"] == "published"
    assert interrupted_task["runs"][0]["status"] == "执行中"
    assert interrupted_task["publish_package"]["status"] == "pending_publish"

    recovery = service.execute_due_tasks(datetime(2026, 7, 14, 9, 31))
    recovered = service.get_task(task["id"])

    assert recovery["executed"] == 0
    assert recovered["status"] == "已发布"
    assert recovered["runs"][0]["status"] == "已发布"
    assert recovered["publish_package"]["status"] == "published"
    assert len(publisher.calls) == 1


@pytest.mark.parametrize("recovery", ["manual", "scan"])
def test_interrupted_pending_publish_job_executes_same_job(
    tmp_path: Path, recovery: str
):
    service, publisher, task, interrupted = prepare_task_interrupted_with_pending_job(
        tmp_path, recovery
    )
    original_job_id = interrupted["publish_job_id"]

    if recovery == "manual":
        service.execute_task(task["id"], datetime(2026, 7, 14, 9, 31))
    else:
        scan = service.execute_due_tasks(datetime(2026, 7, 14, 9, 31))
        assert scan["executed"] == 0

    recovered = service.get_task(task["id"])
    assert recovered["status"] == "已发布"
    assert recovered["runs"][0]["status"] == "已发布"
    assert recovered["publish_package"]["status"] == "published"
    assert recovered["publish_job_id"] == original_job_id
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1
    assert len(publisher.calls) == 1


@pytest.mark.parametrize(
    "pre_adoption",
    ["global_maintenance", "job_already_published"],
)
def test_restart_adopts_job_when_process_exits_before_task_link(
    tmp_path: Path, pre_adoption: str
):
    class SimulatedProcessExit(BaseException):
        pass

    database_path = tmp_path / "prepare-before-link-exit.db"
    upload_dir = tmp_path / "prepare-before-link-uploads"
    publisher = FakePlatformPublisher()
    app = create_app(
        database_path=database_path,
        upload_dir=upload_dir,
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    service = app.state.content_service
    account_id = add_authorized_account(service, "prepare 后中断账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "prepare 后中断账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "prepare 后 link 前中断恢复",
        }
    )

    def exit_before_link(*args, **kwargs):
        raise SimulatedProcessExit

    service._link_task_publish_job = exit_before_link
    with pytest.raises(SimulatedProcessExit):
        service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))

    orphan = service.db.fetch_one(
        "SELECT id, status FROM publish_jobs ORDER BY id DESC LIMIT 1"
    )
    interrupted = service.get_task(task["id"])
    assert orphan is not None
    assert orphan["status"] == "pending"
    assert interrupted["status"] == "执行中"
    assert interrupted["publish_job_id"] is None
    assert interrupted["runs"][0]["publish_job_id"] is None
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_packages")["count"] == 1
    assert publisher.calls == []

    restarted_app = create_app(
        database_path=database_path,
        upload_dir=upload_dir,
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    restarted = restarted_app.state.content_service
    if pre_adoption == "global_maintenance":
        maintenance = restarted.recover_publish_jobs(datetime(2026, 7, 14, 9, 31))
        assert maintenance["recovered"] == 0
        assert restarted.get_publish_job(orphan["id"])["status"] == "pending"
    else:
        changed = restarted.execute_publish_job(orphan["id"])
        assert changed["status"] == "published"
    scan = restarted.execute_due_tasks(datetime(2026, 7, 14, 9, 31))
    recovered = restarted.get_task(task["id"])

    assert scan["executed"] == 0
    assert recovered["status"] == "已发布"
    assert recovered["publish_job_id"] == orphan["id"]
    assert recovered["runs"][0]["publish_job_id"] == orphan["id"]
    assert recovered["publish_package"]["publish_job_id"] == orphan["id"]
    assert restarted.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1
    assert restarted.db.fetch_one("SELECT COUNT(*) AS count FROM publish_packages")["count"] == 1
    assert len(publisher.calls) == 1


def test_restart_rebuilds_package_after_exit_between_prepare_and_package(tmp_path: Path):
    class SimulatedProcessExit(BaseException):
        pass

    database_path = tmp_path / "prepare-before-package-exit.db"
    upload_dir = tmp_path / "prepare-before-package-uploads"
    publisher = FakePlatformPublisher()
    app = create_app(
        database_path=database_path,
        upload_dir=upload_dir,
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    service = app.state.content_service
    account_id = add_authorized_account(service, "prepare 后 package 前中断账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "prepare 后 package 前中断账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "prepare 后 package 前中断恢复",
        }
    )

    def exit_before_package(*args, **kwargs):
        raise SimulatedProcessExit

    service._persist_publish_package = exit_before_package
    with pytest.raises(SimulatedProcessExit):
        service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))

    orphan = service.db.fetch_one(
        "SELECT id, status FROM publish_jobs ORDER BY id DESC LIMIT 1"
    )
    interrupted = service.get_task(task["id"])
    assert orphan is not None
    assert orphan["status"] == "pending"
    assert interrupted["status"] == "执行中"
    assert interrupted["publish_job_id"] is None
    assert interrupted["publish_package_id"] is None
    assert interrupted["runs"][0]["publish_job_id"] is None
    assert interrupted["runs"][0]["publish_package_id"] is None
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_packages")["count"] == 0
    assert publisher.calls == []

    restarted_app = create_app(
        database_path=database_path,
        upload_dir=upload_dir,
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    restarted = restarted_app.state.content_service
    scan = restarted.execute_due_tasks(datetime(2026, 7, 14, 9, 31))
    recovered = restarted.get_task(task["id"])

    assert scan["executed"] == 0
    assert recovered["status"] == "已发布"
    assert recovered["publish_job_id"] == orphan["id"]
    assert recovered["runs"][0]["publish_job_id"] == orphan["id"]
    assert recovered["publish_package"]["publish_job_id"] == orphan["id"]
    assert restarted.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1
    assert restarted.db.fetch_one("SELECT COUNT(*) AS count FROM publish_packages")["count"] == 1
    assert len(publisher.calls) == 1


def test_scheduler_fails_stale_claimed_resume_and_retry_reuses_job(tmp_path: Path):
    database_path, upload_dir, task, interrupted = prepare_task_interrupted_during_resume(
        tmp_path, "stale"
    )
    job_id = interrupted["publish_job_id"]
    db = Database(database_path)
    db.execute(
        "UPDATE publish_jobs SET updated_at = '2026-07-14 01:00:00' WHERE id = ?",
        (job_id,),
    )
    retry_publisher = FakePlatformPublisher()
    restarted_app = create_app(
        database_path=database_path,
        upload_dir=upload_dir,
        ai_client=FakeAIClient(),
        platform_publisher=retry_publisher,
        scheduler_enabled=False,
    )
    restarted = restarted_app.state.content_service

    scan = restarted.execute_due_tasks(datetime(2026, 7, 14, 10, 0))
    failed = restarted.get_task(task["id"])

    assert scan["executed"] == 0
    assert failed["status"] == "失败"
    assert failed["runs"][0]["status"] == "失败"
    assert failed["publish_package"]["status"] == "failed"
    assert failed["publish_job"]["status"] == "failed"
    assert failed["publish_job_id"] == job_id
    assert failed["failure_stage"] == "发布中断"
    assert "人工核对平台" in failed["failure_reason"]
    assert retry_publisher.calls == []

    retry = restarted.retry_failed_tasks([task["id"]])
    recovered = restarted.get_task(task["id"])

    assert retry["updated"] == 1
    assert recovered["status"] == "已发布"
    assert recovered["publish_job_id"] == job_id
    assert restarted.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1
    assert restarted.db.fetch_one("SELECT COUNT(*) AS count FROM publish_packages")["count"] == 1
    assert len(retry_publisher.calls) == 1


def test_scheduler_recognizes_recent_claimed_resume_without_timing_it_out(tmp_path: Path):
    database_path, upload_dir, task, interrupted = prepare_task_interrupted_during_resume(
        tmp_path, "recent"
    )
    job_id = interrupted["publish_job_id"]
    db = Database(database_path)
    db.execute(
        "UPDATE publish_jobs SET updated_at = '2026-07-14 01:55:00' WHERE id = ?",
        (job_id,),
    )
    restarted_app = create_app(
        database_path=database_path,
        upload_dir=upload_dir,
        ai_client=FakeAIClient(),
        platform_publisher=FakePlatformPublisher(),
        scheduler_enabled=False,
    )
    restarted = restarted_app.state.content_service
    stale_checks = []
    original_fail_stale = restarted.publish_coordinator.fail_stale_in_progress_job

    def record_stale_check(requested_job_id, stale_before):
        stale_checks.append((requested_job_id, stale_before))
        return original_fail_stale(requested_job_id, stale_before)

    restarted.publish_coordinator.fail_stale_in_progress_job = record_stale_check
    scan = restarted.execute_due_tasks(datetime(2026, 7, 14, 10, 0))
    unchanged = restarted.get_task(task["id"])

    assert scan["executed"] == 0
    assert stale_checks == [(job_id, "2026-07-14 01:45:00")]
    assert unchanged["status"] == "待人工确认"
    assert unchanged["runs"][0]["status"] == "待人工确认"
    assert unchanged["publish_package"]["status"] == "awaiting_confirmation"
    assert unchanged["publish_job"]["status"] == "checking_login"
    assert restarted.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1
    assert restarted.db.fetch_one("SELECT COUNT(*) AS count FROM publish_packages")["count"] == 1


@pytest.mark.parametrize(
    ("execution_mode", "expected_status", "expected_next_run_at"),
    [
        ("once", "已发布", ""),
        ("repeat", "待执行", "2026-07-15 09:30"),
    ],
)
def test_task_scheduler_finalizes_resume_success_after_exit_before_local_sync(
    tmp_path: Path,
    execution_mode: str,
    expected_status: str,
    expected_next_run_at: str,
):
    class ProcessInterrupted(BaseException):
        pass

    database_path = tmp_path / f"resume-terminal-{execution_mode}.db"
    upload_dir = tmp_path / f"resume-terminal-uploads-{execution_mode}"
    publisher = ConfirmationPublisher()
    app = create_app(
        database_path=database_path,
        upload_dir=upload_dir,
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    service = app.state.content_service
    account_id = add_authorized_account(service, f"resume 终态-{execution_mode}")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": f"resume 终态-{execution_mode}",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": f"resume 成功本地收尾-{execution_mode}",
            "execution_mode": execution_mode,
            "repeat_rule": "daily" if execution_mode == "repeat" else "",
        }
    )
    waiting = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))

    def exit_before_local_sync(job):
        assert job["status"] == "published"
        raise ProcessInterrupted("forced exit before resume local sync")

    service._synchronize_linked_waiting_tasks = exit_before_local_sync
    with pytest.raises(ProcessInterrupted, match="forced exit before resume local sync"):
        service.resume_publish_job(waiting["publish_job_id"])

    interrupted = service.get_task(task["id"])
    assert interrupted["status"] == "待人工确认"
    assert interrupted["runs"][0]["status"] == "待人工确认"
    assert interrupted["publish_package"]["status"] == "awaiting_confirmation"
    assert interrupted["publish_job"]["status"] == "published"
    assert publisher.resume_calls == 1

    recovery_publisher = FakePlatformPublisher()
    restarted_app = create_app(
        database_path=database_path,
        upload_dir=upload_dir,
        ai_client=FakeAIClient(),
        platform_publisher=recovery_publisher,
        scheduler_enabled=False,
    )
    restarted = restarted_app.state.content_service
    first_scan = restarted.execute_due_tasks(datetime(2026, 7, 14, 10, 0))
    recovered = restarted.get_task(task["id"])
    second_scan = restarted.execute_due_tasks(datetime(2026, 7, 14, 10, 1))
    repeated = restarted.get_task(task["id"])

    assert first_scan["executed"] == second_scan["executed"] == 0
    assert recovered["status"] == expected_status
    assert recovered["publish_result"] == "已发布"
    assert recovered["next_run_at"] == expected_next_run_at
    assert recovered["runs"][0]["status"] == "已发布"
    assert recovered["runs"][0]["finished_at"]
    assert recovered["publish_package"]["status"] == "published"
    assert recovered["publish_package"]["published_at"]
    assert recovered["publish_job"]["status"] == "published"
    assert recovered["publish_job"]["response"]["receipt_id"] == "publish-42"
    assert repeated["status"] == recovered["status"]
    assert repeated["next_run_at"] == recovered["next_run_at"]
    assert restarted.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1
    assert restarted.db.fetch_one("SELECT COUNT(*) AS count FROM publish_packages")["count"] == 1
    package_evidence = restarted.db.fetch_one(
        "SELECT platform_response_json FROM publish_packages WHERE id = ?",
        (recovered["publish_package_id"],),
    )
    assert json.loads(package_evidence["platform_response_json"])["receipt_id"] == "publish-42"
    assert recovery_publisher.calls == []


@pytest.mark.parametrize(
    ("execution_mode", "expected_status", "expected_next_run_at"),
    [
        ("once", "已生成平台草稿", ""),
        ("repeat", "待执行", "2026-07-15 09:30"),
    ],
)
def test_task_scheduler_locally_finalizes_linked_platform_draft_after_restart(
    tmp_path: Path,
    execution_mode: str,
    expected_status: str,
    expected_next_run_at: str,
):
    database_path = tmp_path / f"waiting-platform-draft-{execution_mode}.db"
    upload_dir = tmp_path / f"waiting-platform-draft-uploads-{execution_mode}"
    publisher = ConfirmationPublisher()
    app = create_app(
        database_path=database_path,
        upload_dir=upload_dir,
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    service = app.state.content_service
    account_id = add_authorized_account(service, f"平台草稿恢复-{execution_mode}")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": f"平台草稿恢复-{execution_mode}",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": f"平台草稿本地收尾-{execution_mode}",
            "execution_mode": execution_mode,
            "repeat_rule": "daily" if execution_mode == "repeat" else "",
        }
    )
    waiting = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    response = {
        "source": "wechat_platform",
        "event": "draft_created",
        "receipt_id": "recovered-draft",
        "corroboration": "draft_saved",
    }
    service.db.execute(
        """
        UPDATE publish_jobs
        SET status = 'platform_draft', platform_draft_id = 'recovered-draft',
            platform_publish_id = '', platform_url = ?, response_json = ?,
            requires_confirmation = 0, failure_stage = '', failure_reason = ''
        WHERE id = ?
        """,
        (
            "https://mp.weixin.qq.com/draft/recovered-draft",
            json.dumps(response, ensure_ascii=False),
            waiting["publish_job_id"],
        ),
    )

    recovery_publisher = FakePlatformPublisher()
    restarted_app = create_app(
        database_path=database_path,
        upload_dir=upload_dir,
        ai_client=FakeAIClient(),
        platform_publisher=recovery_publisher,
        scheduler_enabled=False,
    )
    restarted = restarted_app.state.content_service
    first_scan = restarted.execute_due_tasks(datetime(2026, 7, 14, 10, 0))
    recovered = restarted.get_task(task["id"])
    second_scan = restarted.execute_due_tasks(datetime(2026, 7, 14, 10, 1))

    assert first_scan["executed"] == second_scan["executed"] == 0
    assert recovered["status"] == expected_status
    assert recovered["publish_result"] == "已生成平台草稿"
    assert recovered["next_run_at"] == expected_next_run_at
    assert recovered["runs"][0]["status"] == "已生成平台草稿"
    assert recovered["runs"][0]["finished_at"]
    assert recovered["publish_package"]["status"] == "platform_draft"
    assert recovered["publish_package"]["platform_draft_id"] == "recovered-draft"
    assert recovered["publish_package"]["published_at"]
    assert recovered["publish_job"]["status"] == "platform_draft"
    assert recovered["publish_job"]["response"] == response
    assert restarted.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1
    assert restarted.db.fetch_one("SELECT COUNT(*) AS count FROM publish_packages")["count"] == 1
    assert publisher.resume_calls == 0
    assert publisher.calls == []
    assert recovery_publisher.calls == []


@pytest.mark.parametrize("execution_mode", ["once", "repeat"])
def test_task_scheduler_finalizes_stale_failure_after_exit_before_local_sync(
    tmp_path: Path, execution_mode: str
):
    class ProcessInterrupted(BaseException):
        pass

    database_path, upload_dir, task, interrupted = prepare_task_interrupted_during_resume(
        tmp_path, f"stale-terminal-{execution_mode}", execution_mode
    )
    job_id = interrupted["publish_job_id"]
    Database(database_path).execute(
        "UPDATE publish_jobs SET updated_at = '2026-07-14 01:00:00' WHERE id = ?",
        (job_id,),
    )
    recovery_publisher = FakePlatformPublisher()
    interrupted_app = create_app(
        database_path=database_path,
        upload_dir=upload_dir,
        ai_client=FakeAIClient(),
        platform_publisher=recovery_publisher,
        scheduler_enabled=False,
    )
    interrupted_service = interrupted_app.state.content_service

    def exit_before_local_sync(*args, **kwargs):
        assert args[4]["status"] == "failed"
        raise ProcessInterrupted("forced exit before stale local sync")

    interrupted_service._finish_task_from_publish_job = exit_before_local_sync
    with pytest.raises(ProcessInterrupted, match="forced exit before stale local sync"):
        interrupted_service.execute_due_tasks(datetime(2026, 7, 14, 10, 0))

    terminal = interrupted_service.get_task(task["id"])
    assert terminal["status"] == "待人工确认"
    assert terminal["runs"][0]["status"] == "待人工确认"
    assert terminal["publish_package"]["status"] == "awaiting_confirmation"
    assert terminal["publish_job"]["status"] == "failed"
    assert terminal["publish_job"]["failure_stage"] == "发布中断"

    restarted_app = create_app(
        database_path=database_path,
        upload_dir=upload_dir,
        ai_client=FakeAIClient(),
        platform_publisher=recovery_publisher,
        scheduler_enabled=False,
    )
    restarted = restarted_app.state.content_service
    first_scan = restarted.execute_due_tasks(datetime(2026, 7, 14, 10, 1))
    failed = restarted.get_task(task["id"])
    second_scan = restarted.execute_due_tasks(datetime(2026, 7, 14, 10, 2))
    repeated = restarted.get_task(task["id"])

    assert first_scan["executed"] == second_scan["executed"] == 0
    assert failed["status"] == "失败"
    assert failed["publish_result"] == ""
    assert failed["next_run_at"] == ""
    assert failed["failure_stage"] == "发布中断"
    assert "人工核对平台" in failed["failure_reason"]
    assert failed["manual_takeover_url"] == f"manual-takeover://task/{task['id']}"
    assert failed["runs"][0]["status"] == "失败"
    assert failed["runs"][0]["finished_at"]
    assert failed["runs"][0]["failure_stage"] == "发布中断"
    assert failed["publish_package"]["status"] == "failed"
    assert failed["publish_package"]["failure_stage"] == "发布中断"
    assert failed["publish_package"]["published_at"] == ""
    assert failed["publish_job"]["status"] == "failed"
    assert repeated["status"] == "失败"
    assert repeated["failure_reason"] == failed["failure_reason"]
    assert restarted.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1
    assert restarted.db.fetch_one("SELECT COUNT(*) AS count FROM publish_packages")["count"] == 1
    assert recovery_publisher.calls == []


def test_stale_checking_login_job_moves_to_manual_interruption_failure(tmp_path: Path):
    service, publisher, task, interrupted = prepare_task_interrupted_with_pending_job(
        tmp_path, "stale-checking-login"
    )
    original_job_id = interrupted["publish_job_id"]
    service.db.execute(
        """
        UPDATE publish_jobs
        SET status = 'checking_login', updated_at = '2026-07-14 01:00:00'
        WHERE id = ?
        """,
        (original_job_id,),
    )

    scan = service.execute_due_tasks(datetime(2026, 7, 14, 10, 0))
    failed = service.get_task(task["id"])

    assert scan["executed"] == 0
    assert failed["status"] == "失败"
    assert failed["runs"][0]["status"] == "失败"
    assert failed["publish_package"]["status"] == "failed"
    assert failed["publish_job"]["status"] == "failed"
    assert failed["publish_job_id"] == original_job_id
    assert failed["failure_stage"] == "发布中断"
    assert failed["runs"][0]["failure_stage"] == "发布中断"
    assert failed["publish_package"]["failure_stage"] == "发布中断"
    assert "人工核对平台" in failed["failure_reason"]
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1
    assert publisher.calls == []


def test_recent_checking_login_job_is_not_changed_by_recovery_scan(tmp_path: Path):
    service, publisher, task, interrupted = prepare_task_interrupted_with_pending_job(
        tmp_path, "recent-checking-login"
    )
    original_job_id = interrupted["publish_job_id"]
    service.db.execute(
        """
        UPDATE publish_jobs
        SET status = 'checking_login', updated_at = '2026-07-14 01:55:00'
        WHERE id = ?
        """,
        (original_job_id,),
    )

    scan = service.execute_due_tasks(datetime(2026, 7, 14, 10, 0))
    unchanged = service.get_task(task["id"])

    assert scan["executed"] == 0
    assert unchanged["status"] == "执行中"
    assert unchanged["runs"][0]["status"] == "执行中"
    assert unchanged["publish_package"]["status"] == "pending_publish"
    assert unchanged["publish_job"]["status"] == "checking_login"
    assert unchanged["publish_job_id"] == original_job_id
    assert unchanged["failure_stage"] == ""
    assert unchanged["failure_reason"] == ""
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1
    assert publisher.calls == []


def test_platform_success_survives_local_postprocessing_failure_without_republish(tmp_path: Path):
    publisher = FakePlatformPublisher()
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    service = app.state.content_service
    account_id = add_authorized_account(service, "后处理失败账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "后处理失败账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "平台成功后本地失败",
        }
    )
    original_autosave = service.autosave

    def fail_publish_autosave(project_id, stage="", restore_stage=""):
        if stage == "publish":
            raise RuntimeError("forced local postprocessing failure")
        return original_autosave(project_id, stage=stage, restore_stage=restore_stage)

    service.autosave = fail_publish_autosave

    detail = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    retried = service.retry_failed_tasks([task["id"]])

    assert detail["status"] == "已发布"
    assert detail["publish_job"]["status"] == "published"
    assert detail["publish_job_id"]
    assert detail["runs"][0]["publish_job_id"] == detail["publish_job_id"]
    assert detail["publish_package"]["publish_job_id"] == detail["publish_job_id"]
    assert retried["updated"] == 0
    assert len(publisher.calls) == 1

    service.db.execute("UPDATE scheduled_tasks SET status = '失败' WHERE id = ?", (task["id"],))
    recovered = service.retry_failed_tasks([task["id"]])
    recovered_detail = service.get_task(task["id"])

    assert recovered["updated"] == 1
    assert recovered_detail["status"] == "已发布"
    assert recovered_detail["publish_job_id"] == detail["publish_job_id"]
    assert len(publisher.calls) == 1


def test_stale_resume_cannot_overwrite_new_task_generation(tmp_path: Path):
    publisher = ConfirmationPublisher()
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    service = app.state.content_service
    account_id = add_authorized_account(service, "陈旧恢复账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "陈旧恢复账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "陈旧恢复不得覆盖",
        }
    )
    waiting = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    old_package_id = waiting["publish_package_id"]
    entered_finish = threading.Event()
    release_finish = threading.Event()
    original_finish = service._finish_task_from_publish_job

    def delayed_finish(*args, **kwargs):
        entered_finish.set()
        assert release_finish.wait(timeout=5)
        return original_finish(*args, **kwargs)

    service._finish_task_from_publish_job = delayed_finish
    with ThreadPoolExecutor(max_workers=1) as pool:
        resumed = pool.submit(service.resume_publish_job, waiting["publish_job_id"])
        assert entered_finish.wait(timeout=5)
        new_job_id = service.db.execute(
            """
            INSERT INTO publish_jobs (project_id, account_id, platform, action, status)
            VALUES (?, ?, '微信公众号', 'publish', 'pending')
            """,
            (waiting["content_project_id"], account_id),
        )
        new_run_id = service.db.execute(
            "INSERT INTO task_runs (task_id, status, started_at, publish_job_id) VALUES (?, '执行中', ?, ?)",
            (task["id"], "2026-07-14 10:00", new_job_id),
        )
        service.db.execute(
            """
            UPDATE scheduled_tasks
            SET status = '执行中', publish_result = '', publish_job_id = ?, publish_package_id = NULL
            WHERE id = ?
            """,
            (new_job_id, task["id"]),
        )
        release_finish.set()
        assert resumed.result(timeout=5)["status"] == "published"

    current = service.get_task(task["id"])
    old_package = service.db.fetch_one("SELECT status FROM publish_packages WHERE id = ?", (old_package_id,))
    assert current["status"] == "执行中"
    assert current["publish_job_id"] == new_job_id
    assert current["runs"][0]["id"] == new_run_id
    assert current["runs"][0]["status"] == "执行中"
    assert old_package["status"] == "awaiting_confirmation"


def test_confirmation_resume_recovers_when_local_package_was_not_created(tmp_path: Path):
    publisher = ConfirmationPublisher()
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    service = app.state.content_service
    account_id = add_authorized_account(service, "无发布包恢复账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "无发布包恢复账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "无本地发布包也可恢复",
        }
    )
    waiting = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    service.db.execute(
        "DELETE FROM publish_packages WHERE id = ?",
        (waiting["publish_package_id"],),
    )
    waiting = service.get_task(task["id"])

    assert waiting["status"] == "待人工确认"
    assert waiting["publish_job_id"]
    assert waiting["publish_package_id"] is None
    assert waiting["runs"][0]["publish_job_id"] == waiting["publish_job_id"]

    resumed = service.resume_publish_job(waiting["publish_job_id"])
    final_task = service.get_task(task["id"])

    assert resumed["status"] == "published"
    assert final_task["status"] == "已发布"
    assert final_task["publish_job_id"] == waiting["publish_job_id"]
    assert publisher.resume_calls == 1


def test_resume_task_package_and_run_statuses_roll_back_together(tmp_path: Path):
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=ConfirmationPublisher(),
        scheduler_enabled=False,
    )
    service = app.state.content_service
    account_id = add_authorized_account(service, "事务回滚账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "事务回滚账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "恢复状态原子提交",
        }
    )
    waiting = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    run_id = waiting["runs"][0]["id"]
    service.db.execute(
        f"""
        CREATE TRIGGER fail_task_run_resume
        BEFORE UPDATE ON task_runs
        WHEN OLD.id = {int(run_id)} AND NEW.status = '已发布'
        BEGIN
          SELECT RAISE(ABORT, 'forced task run sync failure');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="forced task run sync failure"):
        service.resume_publish_job(waiting["publish_job_id"])

    task_row = service.db.fetch_one("SELECT status FROM scheduled_tasks WHERE id = ?", (task["id"],))
    run_row = service.db.fetch_one("SELECT status FROM task_runs WHERE id = ?", (run_id,))
    package_row = service.db.fetch_one(
        "SELECT status FROM publish_packages WHERE id = ?", (waiting["publish_package_id"],)
    )
    assert task_row["status"] == "待人工确认"
    assert run_row["status"] == "待人工确认"
    assert package_row["status"] == "awaiting_confirmation"


def test_resume_retries_local_sync_without_republishing_after_platform_success(tmp_path: Path):
    publisher = ConfirmationPublisher()
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    service = app.state.content_service
    account_id = add_authorized_account(service, "恢复本地同步账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "恢复本地同步账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "平台成功后重试本地同步",
        }
    )
    waiting = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    run_id = waiting["runs"][0]["id"]
    service.db.execute(
        f"""
        CREATE TRIGGER fail_task_run_resume_once
        BEFORE UPDATE ON task_runs
        WHEN OLD.id = {int(run_id)} AND NEW.status = '已发布'
        BEGIN
          SELECT RAISE(ABORT, 'forced local resume sync failure');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="forced local resume sync failure"):
        service.resume_publish_job(waiting["publish_job_id"])

    service.db.execute("DROP TRIGGER fail_task_run_resume_once")
    resumed = service.resume_publish_job(waiting["publish_job_id"])
    final_task = service.get_task(task["id"])

    assert resumed["status"] == "published"
    assert final_task["status"] == "已发布"
    assert final_task["runs"][0]["status"] == "已发布"
    assert final_task["publish_package"]["status"] == "published"
    assert publisher.resume_calls == 1


def test_execute_due_tasks_reports_only_successful_claims(tmp_path: Path):
    service = make_task_service(tmp_path)
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_name": "AI记账助手",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "并发到期领取计数",
        }
    )
    barrier = threading.Barrier(2)
    original_claim = service._claim_task_execution

    def synchronized_claim(*args, **kwargs):
        barrier.wait(timeout=5)
        return original_claim(*args, **kwargs)

    service._claim_task_execution = synchronized_claim
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: service.execute_due_tasks(datetime(2026, 7, 14, 9, 31)),
                range(2),
            )
        )

    assert sorted(result["executed"] for result in results) == [0, 1]
    assert service.get_task(task["id"])["status"] == "已发布"


def test_due_scan_cannot_claim_task_after_patch_moves_it_to_future(tmp_path: Path):
    publisher = FakePlatformPublisher()
    app = create_app(
        database_path=tmp_path / "stale-due-scan.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    client = TestClient(app)
    service = app.state.content_service
    account_id = add_authorized_account(service, "旧扫描编辑账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "旧扫描编辑账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "旧扫描不应执行",
        }
    )
    claim_entered = threading.Event()
    release_claim = threading.Event()
    original_claim = service._claim_task_execution

    def delayed_claim(*args, **kwargs):
        claim_entered.set()
        assert release_claim.wait(timeout=5)
        return original_claim(*args, **kwargs)

    service._claim_task_execution = delayed_claim
    with ThreadPoolExecutor(max_workers=1) as pool:
        scan = pool.submit(service.execute_due_tasks, datetime(2026, 7, 14, 9, 31))
        assert claim_entered.wait(timeout=5)
        patched = client.patch(
            f"/api/tasks/{task['id']}",
            json={
                "platform": "微信公众号",
                "account_id": account_id,
                "account_name": "旧扫描编辑账号",
                "execute_time": "2026-07-15 10:00",
                "task_type": "微信公众号图文生成",
                "topic": "已经延期的任务",
            },
        )
        release_claim.set()
        result = scan.result(timeout=5)

    detail = service.get_task(task["id"])
    assert patched.status_code == 200
    assert result["executed"] == 0
    assert detail["status"] == "待执行"
    assert detail["next_run_at"] == "2026-07-15 10:00"
    assert detail["topic"] == "已经延期的任务"
    assert publisher.calls == []
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 0


def test_retry_scan_cannot_claim_task_after_patch_changes_generation(tmp_path: Path):
    publisher = FakePlatformPublisher()
    app = create_app(
        database_path=tmp_path / "stale-retry-scan.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    client = TestClient(app)
    service = app.state.content_service
    account_id = add_authorized_account(service, "旧重试编辑账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "旧重试编辑账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "旧重试不应执行",
            "status": "失败",
            "failure_stage": "生成文章",
            "failure_reason": "旧失败",
        }
    )
    claim_entered = threading.Event()
    release_claim = threading.Event()
    original_claim = service._claim_task_execution

    def delayed_claim(*args, **kwargs):
        claim_entered.set()
        assert release_claim.wait(timeout=5)
        return original_claim(*args, **kwargs)

    service._claim_task_execution = delayed_claim
    with ThreadPoolExecutor(max_workers=1) as pool:
        retry = pool.submit(service.retry_failed_tasks, [task["id"]])
        assert claim_entered.wait(timeout=5)
        patched = client.patch(
            f"/api/tasks/{task['id']}",
            json={
                "platform": "微信公众号",
                "account_id": account_id,
                "account_name": "旧重试编辑账号",
                "execute_time": "2026-07-15 11:00",
                "task_type": "微信公众号图文生成",
                "topic": "编辑后的失败任务",
            },
        )
        release_claim.set()
        result = retry.result(timeout=5)

    detail = service.get_task(task["id"])
    assert patched.status_code == 200
    assert result["updated"] == 0
    assert detail["status"] == "待执行"
    assert detail["next_run_at"] == "2026-07-15 11:00"
    assert detail["topic"] == "编辑后的失败任务"
    assert publisher.calls == []
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 0


def test_linked_job_retry_and_patch_keep_same_generation_and_job(tmp_path: Path):
    publisher = TaskRetryPublisher("published")
    app = create_app(
        database_path=tmp_path / "linked-retry-patch.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    client = TestClient(app)
    service = app.state.content_service
    account_id = add_authorized_account(service, "关联重试编辑账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "关联重试编辑账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "失败作业必须原位重试",
        }
    )
    failed = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    original_job_id = failed["publish_job_id"]
    claim_entered = threading.Event()
    release_claim = threading.Event()
    original_claim = service._claim_task_execution

    def delayed_claim(*args, **kwargs):
        claim_entered.set()
        assert release_claim.wait(timeout=5)
        return original_claim(*args, **kwargs)

    service._claim_task_execution = delayed_claim
    with ThreadPoolExecutor(max_workers=1) as pool:
        retry = pool.submit(service.retry_failed_tasks, [task["id"]])
        assert claim_entered.wait(timeout=5)
        patched = client.patch(
            f"/api/tasks/{task['id']}",
            json={
                "platform": "微信公众号",
                "account_id": account_id,
                "account_name": "关联重试编辑账号",
                "execute_time": "2026-07-15 11:00",
                "task_type": "微信公众号图文生成",
                "topic": "不得覆盖旧发布作业",
            },
        )
        release_claim.set()
        result = retry.result(timeout=5)

    final = service.get_task(task["id"])
    assert patched.status_code == 409
    assert result["updated"] == 1
    assert final["status"] == "已发布"
    assert final["topic"] == "失败作业必须原位重试"
    assert final["publish_job_id"] == original_job_id
    assert len(publisher.calls) == 2
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1


@pytest.mark.parametrize("source", ["run", "package"])
def test_patch_rejects_publish_job_retained_only_in_history(tmp_path: Path, source: str):
    client, service, publisher, account_id, finished = (
        prepare_task_with_historical_publish_link(
            tmp_path, source, "失败", "patch"
        )
    )

    response = client.patch(
        f"/api/tasks/{finished['id']}",
        json={
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": f"历史关联-patch-{source}",
            "execute_time": "2026-07-15 10:00",
            "task_type": "微信公众号图文生成",
            "topic": "不得覆盖历史发布作业",
        },
    )

    unchanged = service.get_task(finished["id"])
    assert response.status_code == 409
    assert unchanged["topic"] == "历史发布作业必须原位恢复"
    assert len(publisher.calls) == 1
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1


@pytest.mark.parametrize("source", ["run", "package"])
def test_retry_recovers_terminal_job_retained_only_in_history(
    tmp_path: Path, source: str
):
    _, service, publisher, _, finished = prepare_task_with_historical_publish_link(
        tmp_path, source, "失败", "retry"
    )

    result = service.retry_failed_tasks([finished["id"]])
    recovered = service.get_task(finished["id"])

    assert result["updated"] == 1
    assert recovered["status"] == "已发布"
    assert recovered["publish_job_id"] == finished["publish_job_id"]
    assert len(publisher.calls) == 1
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1


@pytest.mark.parametrize("source", ["run", "package"])
def test_scan_recovers_terminal_job_retained_only_in_history(
    tmp_path: Path, source: str
):
    _, service, publisher, _, finished = prepare_task_with_historical_publish_link(
        tmp_path, source, "执行中", "scan"
    )

    result = service.execute_due_tasks(datetime(2026, 7, 14, 9, 31))
    recovered = service.get_task(finished["id"])

    assert result["executed"] == 0
    assert recovered["status"] == "已发布"
    assert recovered["publish_job_id"] == finished["publish_job_id"]
    assert len(publisher.calls) == 1
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1


def test_conflicting_task_run_and_package_jobs_fail_closed(tmp_path: Path):
    _, service, publisher, account_id, finished = prepare_task_with_historical_publish_link(
        tmp_path, "package", "失败", "conflict"
    )
    conflicting_job_id = service.db.execute(
        """
        INSERT INTO publish_jobs (project_id, account_id, platform, action, status)
        VALUES (?, ?, '微信公众号', 'publish', 'pending')
        """,
        (finished["content_project_id"], account_id),
    )
    service.db.execute(
        "UPDATE task_runs SET publish_job_id = ? WHERE id = ?",
        (conflicting_job_id, finished["runs"][0]["id"]),
    )

    with pytest.raises(TaskConflictError, match="相互冲突"):
        service.retry_failed_tasks([finished["id"]])

    unchanged = service.db.fetch_one(
        "SELECT status, publish_job_id FROM scheduled_tasks WHERE id = ?",
        (finished["id"],),
    )
    assert unchanged == {"status": "失败", "publish_job_id": None}
    assert len(publisher.calls) == 1
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 2


def test_repeat_task_starts_next_generation_with_a_new_publish_job(tmp_path: Path):
    publisher = FakePlatformPublisher()
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    service = app.state.content_service
    account_id = add_authorized_account(service, "重复任务代次账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "重复任务代次账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "每天创建新发布作业",
            "execution_mode": "repeat",
            "repeat_rule": "daily",
        }
    )

    first = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    second_result = service.execute_due_tasks(datetime(2026, 7, 15, 9, 31))
    second = service.get_task(task["id"])

    assert second_result["executed"] == 1
    assert second["status"] == "待执行"
    assert second["publish_job_id"] != first["publish_job_id"]
    assert len(second["runs"]) == 2
    assert len(publisher.calls) == 2
    assert service.db.fetch_all(
        "SELECT generation FROM task_runs WHERE task_id = ? ORDER BY id",
        (task["id"],),
    ) == [{"generation": 1}, {"generation": 2}]


def test_once_task_patch_starts_new_generation_without_reusing_old_publish_job(
    tmp_path: Path,
):
    class GenerationAwareAIClient(FakeAIClient):
        def recommend_topics(self, user_prompt: str):
            self.topic_prompts.append(user_prompt)
            title = "第二代标题" if "第二代主题" in user_prompt else "第一代标题"
            return [
                TopicRecommendation(
                    direction="代次隔离",
                    subtitle="代次隔离",
                    titles=[title],
                )
            ]

    publisher = FakePlatformPublisher()
    app = create_app(
        database_path=tmp_path / "once-new-generation.db",
        upload_dir=tmp_path / "uploads",
        ai_client=GenerationAwareAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    client = TestClient(app)
    service = app.state.content_service
    account_id = add_authorized_account(service, "单次任务新代次账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "单次任务新代次账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "第一代主题",
        }
    )
    first = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))

    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "单次任务新代次账号",
            "execute_time": "2026-07-15 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "第二代主题",
        },
    )
    second = service.execute_task(task["id"], datetime(2026, 7, 15, 9, 30))

    assert response.status_code == 200
    assert second["publish_job_id"] != first["publish_job_id"]
    assert second["topic"] == "第二代主题"
    assert second["content_project"]["title"] == "第二代标题"
    assert publisher.calls[1][0]["title"] == "第二代标题"
    assert len(publisher.calls) == 2
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 2
    run_generations = service.db.fetch_all(
        "SELECT generation FROM task_runs WHERE task_id = ? ORDER BY id",
        (task["id"],),
    )
    assert run_generations == [{"generation": 1}, {"generation": 3}]


def test_stale_generation_claim_does_not_backfill_old_publish_job(tmp_path: Path):
    publisher = FakePlatformPublisher()
    app = create_app(
        database_path=tmp_path / "stale-generation-backfill.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    client = TestClient(app)
    service = app.state.content_service
    account_id = add_authorized_account(service, "陈旧扫描代次账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "陈旧扫描代次账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "第一代扫描主题",
        }
    )
    first = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    patched = client.patch(
        f"/api/tasks/{task['id']}",
        json={
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "陈旧扫描代次账号",
            "execute_time": "2026-07-15 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "第二代扫描主题",
        },
    ).json()

    run_id = service._claim_task_execution(
        task["id"],
        "2026-07-15 09:30",
        expected_status="待执行",
        expected_generation=first["generation"],
    )

    unchanged = service.db.fetch_one(
        "SELECT generation, publish_job_id FROM scheduled_tasks WHERE id = ?",
        (task["id"],),
    )
    assert run_id is None
    assert unchanged == {"generation": patched["generation"], "publish_job_id": None}
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM task_runs")["count"] == 1
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1
    assert len(publisher.calls) == 1


def test_task_image_failure_keeps_generated_draft_and_reports_exact_stage(tmp_path: Path):
    db = Database(tmp_path / "content.db")
    db.init_schema()
    service = ContentService(db=db, ai_client=FailingImageAIClient(), upload_dir=tmp_path / "uploads")
    add_authorized_account(service, "AI记账助手")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_name": "AI记账助手",
            "execute_time": "2026-07-11 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "AI记账",
        }
    )

    result = service.execute_task(task["id"], datetime(2026, 7, 11, 9, 31))

    assert result["status"] == "失败"
    assert result["failure_stage"] == "生成图片"
    assert result["content_project_id"]
    assert result["content_project"]["current_stage"] == "article"
    draft = db.fetch_one("SELECT restore_stage FROM draft_snapshots WHERE project_id = ?", (result["content_project_id"],))
    assert draft and draft["restore_stage"] == "images"


def make_task_service(tmp_path: Path) -> ContentService:
    db = Database(tmp_path / "content.db")
    db.init_schema()
    service = ContentService(
        db=db,
        ai_client=FakeAIClient(),
        upload_dir=tmp_path / "uploads",
        platform_publisher=FakePlatformPublisher(),
    )
    add_authorized_account(service, "AI记账助手")
    add_authorized_account(service, "小微账房")
    return service


def test_due_wechat_task_generates_content_package_and_publishes(tmp_path: Path):
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
    assert detail["status"] == "已发布"
    assert detail["content_project_id"]
    assert detail["publish_package_id"]
    assert detail["selected_title"] == "8小时变5分钟"
    assert detail["content_project"]["title"] == "8小时变5分钟"
    assert detail["content_project"]["title_direction"] == "效率颠覆"
    assert detail["failure_reason"] == ""
    assert detail["runs"][0]["status"] == "已发布"
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
    assert detail["status"] == "已发布"
    assert detail["content_project"]["title"] == "8小时变5分钟"


def test_due_task_generates_title_from_topic_account_and_creation_requirements(tmp_path: Path):
    db = Database(tmp_path / "content.db")
    db.init_schema()
    ai_client = FakeAIClient()
    service = ContentService(db=db, ai_client=ai_client, upload_dir=tmp_path / "uploads")
    add_authorized_account(service, "AI记账助手")
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


def test_patch_task_is_rejected_while_publish_is_running_without_duplicate_job(tmp_path: Path):
    class BlockingPublisher(FakePlatformPublisher):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def publish(self, payload, account, action):
            self.entered.set()
            assert self.release.wait(timeout=5)
            return super().publish(payload, account, action)

    publisher = BlockingPublisher()
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    client = TestClient(app)
    service = app.state.content_service
    account_id = add_authorized_account(service, "并发编辑账号")
    task = client.post(
        "/api/tasks",
        json={
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "并发编辑账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "原始发布主题",
            "execution_mode": "repeat",
            "repeat_rule": "daily",
        },
    ).json()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.execute_task, task["id"], datetime(2026, 7, 14, 9, 30))
        assert publisher.entered.wait(timeout=5)
        response = client.patch(
            f"/api/tasks/{task['id']}",
            json={
                "platform": "微信公众号",
                "account_id": account_id,
                "account_name": "并发编辑账号",
                "execute_time": "2026-07-15 10:00",
                "task_type": "微信公众号图文生成",
                "topic": "不应覆盖的编辑主题",
                "execution_mode": "repeat",
                "repeat_rule": "daily",
            },
        )
        during_publish = service.get_task(task["id"])
        publisher.release.set()
        final = future.result(timeout=5)

    assert response.status_code == 409
    assert "正在发布或等待确认" in response.json()["detail"]
    assert during_publish["topic"] == "原始发布主题"
    assert during_publish["publish_job_id"]
    assert during_publish["publish_package_id"]
    assert final["status"] == "待执行"
    assert final["publish_job_id"] == during_publish["publish_job_id"]
    assert final["publish_package_id"] == during_publish["publish_package_id"]
    assert len(publisher.calls) == 1
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1


def test_patch_task_is_rejected_while_waiting_or_linked_job_is_active(tmp_path: Path):
    publisher = ConfirmationPublisher()
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    client = TestClient(app)
    service = app.state.content_service
    account_id = add_authorized_account(service, "待确认编辑账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "待确认编辑账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "待确认原始主题",
        }
    )
    waiting = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    payload = {
        "platform": "微信公众号",
        "account_id": account_id,
        "account_name": "待确认编辑账号",
        "execute_time": "2026-07-15 10:00",
        "task_type": "微信公众号图文生成",
        "topic": "不应覆盖的待确认主题",
    }

    waiting_response = client.patch(f"/api/tasks/{task['id']}", json=payload)
    service.db.execute("UPDATE scheduled_tasks SET status = '失败' WHERE id = ?", (task["id"],))
    active_job_response = client.patch(f"/api/tasks/{task['id']}", json=payload)
    unchanged = service.get_task(task["id"])

    assert waiting_response.status_code == 409
    assert active_job_response.status_code == 409
    assert unchanged["topic"] == "待确认原始主题"
    assert unchanged["publish_job_id"] == waiting["publish_job_id"]
    assert unchanged["publish_package_id"] == waiting["publish_package_id"]
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1


@pytest.mark.parametrize(
    ("publish_action", "job_status"),
    [("publish", "published"), ("draft", "platform_draft")],
)
def test_patch_task_rejects_unsynchronized_terminal_publish_job(
    tmp_path: Path, publish_action: str, job_status: str
):
    publisher = FakePlatformPublisher()
    app = create_app(
        database_path=tmp_path / f"unsynchronized-{job_status}.db",
        upload_dir=tmp_path / f"uploads-{job_status}",
        ai_client=FakeAIClient(),
        platform_publisher=publisher,
        scheduler_enabled=False,
    )
    client = TestClient(app)
    service = app.state.content_service
    account_id = add_authorized_account(service, f"未同步-{job_status}-账号")
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": f"未同步-{job_status}-账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "平台已成功但本地失败",
            "publish_action": publish_action,
        }
    )
    finished = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    original_job_id = finished["publish_job_id"]
    service.db.execute(
        "UPDATE scheduled_tasks SET status = '失败' WHERE id = ?",
        (task["id"],),
    )
    service.db.execute(
        "UPDATE task_runs SET status = '失败' WHERE task_id = ? AND publish_job_id = ?",
        (task["id"], original_job_id),
    )
    service.db.execute(
        "UPDATE publish_packages SET status = 'failed' WHERE id = ?",
        (finished["publish_package_id"],),
    )

    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": f"未同步-{job_status}-账号",
            "execute_time": "2026-07-15 10:00",
            "task_type": "微信公众号图文生成",
            "topic": "不应创建新发布作业",
            "publish_action": publish_action,
        },
    )

    unchanged = service.get_task(task["id"])
    assert response.status_code == 409
    assert unchanged["topic"] == "平台已成功但本地失败"
    assert unchanged["publish_job_id"] == original_job_id
    assert unchanged["publish_job"]["status"] == job_status
    assert len(publisher.calls) == 1
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1


def test_api_task_edit_delete_detail_and_selected_retry(tmp_path: Path):
    app = create_app(
        database_path=tmp_path / "content.db",
        upload_dir=tmp_path / "uploads",
        ai_client=FakeAIClient(),
        platform_publisher=FakePlatformPublisher(),
    )
    client = TestClient(app)
    add_authorized_account(app.state.content_service, "AI记账助手")
    add_authorized_account(app.state.content_service, "小微账房")
    add_authorized_account(app.state.content_service, "老板懂财务")

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
            "platform": "微信公众号",
            "account_name": "AI记账助手",
            "execute_time": "2026-07-09 20:00",
            "task_type": "微信公众号图文生成",
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
    assert retried_detail["status"] == "已发布"
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
    add_authorized_account(app.state.content_service, "AI记账助手")

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
    add_authorized_account(app.state.content_service, "AI记账助手")

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


def test_unavailable_platform_task_is_rejected_before_generation(tmp_path: Path):
    service = make_task_service(tmp_path)
    with pytest.raises(RuntimeError, match="仅支持微信公众号"):
        service.create_task(
            {
                "platform": "小红书",
                "account_name": "财务自动化笔记",
                "execute_time": "2026-07-08 09:30",
                "task_type": "小红书图文生成（预留）",
                "topic": "小红书预留任务",
            }
        )
