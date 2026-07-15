import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.ai import GeneratedImage, TopicRecommendation
from app.database import Database
from app.publishing.wechat import PublishResult
from app.services import ContentService


class FakeAIClient:
    def recommend_topics(self, user_prompt: str):
        return [
            TopicRecommendation(
                direction="效率颠覆",
                subtitle="用时间对比直击老板痛点",
                titles=["8小时到5分钟：老板终于不用盯着账本发愁"]
            )
        ]

    def generate_article(self, title: str, direction: str):
        return [
            {"type": "intro", "label": "开头钩子", "content": f"{title}正在成为老板的新焦虑。"},
            {"type": "pain", "label": "痛点放大", "content": "财务人力成本高、票据流水处理慢。"},
            {"type": "solution", "label": "解决路径", "content": "AI 记账能把重复流程自动化。"},
            {"type": "cta", "label": "结尾 CTA", "content": "私信【AI记账】进入老板 AI 咨询群。"},
        ]

    def generate_image(self, prompt: str, image_type: str):
        return GeneratedImage(
            filename=f"{image_type}.svg",
            content=b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
            content_type="image/svg+xml",
        )


class FakePlatformPublisher:
    def publish(self, payload, account, action):
        return PublishResult(
            status="platform_draft",
            platform_draft_id="draft-media-id",
            platform_url="https://mp.weixin.qq.com/draft/draft-media-id",
            response={
                "source": "wechat_platform",
                "event": "draft_created",
                "receipt_id": "draft-media-id",
                "corroboration": "draft_api_response",
            },
        )

    def resume(self, account, job_context):
        raise AssertionError("draft jobs cannot be resumed")

    def cancel(self, account, job_context):
        return None


def make_service(tmp_path: Path) -> ContentService:
    db = Database(tmp_path / "content.db")
    db.init_schema()
    return ContentService(
        db=db,
        ai_client=FakeAIClient(),
        upload_dir=tmp_path / "uploads",
        platform_publisher=FakePlatformPublisher(),
    )


def test_content_creation_flow_persists_article_images_draft_and_publish_package(tmp_path):
    service = make_service(tmp_path)

    recommendations = service.recommend_topics("写给小微企业老板，推广 AI 记账")
    assert recommendations[0].titles[0] == "8小时到5分钟：老板终于不用盯着账本发愁"

    project = service.create_project(
        title=recommendations[0].titles[0],
        title_direction=recommendations[0].direction,
    )
    assert project["current_stage"] == "article"

    article = service.generate_article(project["id"])
    assert [block["type"] for block in article["blocks"]] == ["intro", "body", "body", "body"]
    assert [block["label"] for block in article["blocks"]] == ["引言", "正文", "正文", "正文"]

    images = service.generate_images(project["id"])
    assert {asset["asset_type"] for asset in images["assets"]} == {"cover", "inline"}
    assert all((tmp_path / asset["local_path"]).exists() for asset in images["assets"])

    layout = service.save_layout(
        project["id"],
        [
            {"section_type": "title", "sort_order": 1, "content": project["title"], "media_asset_id": images["assets"][0]["id"]},
            {
                "section_type": "intro",
                "sort_order": 2,
                "content": article["blocks"][0]["content"],
                "media_asset_id": images["assets"][1]["id"],
            },
        ],
    )
    assert layout["layout_blocks"][1]["media_asset_id"] == images["assets"][1]["id"]
    service.autosave(project["id"], stage="publish", restore_stage="layout")
    drafts = service.list_drafts()
    assert drafts[0]["title"] == project["title"]
    assert drafts[0]["restore_stage"] == "layout"

    account_id = service.db.execute(
        "INSERT INTO platform_accounts (platform, name, auth_status) VALUES (?, ?, ?)",
        ("微信公众号", "AI 记账工具公众号", "已授权"),
    )
    package = service.create_publish_package(
        project["id"],
        platform="微信公众号",
        account_name="AI 记账工具公众号",
        account_id=account_id,
    )
    assert package["status"] == "platform_draft"
    assert package["platform_draft_id"] == "draft-media-id"
    assert package["platform"] == "微信公众号"


def test_publish_coordinator_persists_transition_before_external_publish(tmp_path):
    class TransitionPublisher(FakePlatformPublisher):
        def __init__(self, db):
            self.db = db
            self.status_during_publish = ""

        def publish(self, payload, account, action):
            row = self.db.fetch_one("SELECT status FROM publish_jobs ORDER BY id DESC LIMIT 1")
            self.status_during_publish = row["status"] if row else ""
            return super().publish(payload, account, action)

    service = make_service(tmp_path)
    publisher = TransitionPublisher(service.db)
    service.platform_publisher = publisher
    service.publish_coordinator.publisher = publisher
    project = service.create_project("协调器状态测试", platform="微信公众号")
    service.generate_article(project["id"])
    images = service.generate_images(project["id"])["assets"]
    cover = next(asset for asset in images if asset["asset_type"] == "cover")
    service.save_layout(
        project["id"],
        [
            {
                "section_type": "title",
                "content": project["title"],
                "media_asset_id": cover["id"],
                "sort_order": 1,
            },
            {
                "section_type": "intro",
                "content": "协调器正文",
                "media_asset_id": None,
                "sort_order": 2,
            },
        ],
    )
    account_id = service.db.execute(
        "INSERT INTO platform_accounts (platform, name, auth_status) VALUES (?, ?, ?)",
        ("微信公众号", "协调器测试账号", "已授权"),
    )

    job = service.create_publish_job(project["id"], account_id, "draft")

    assert publisher.status_during_publish == "checking_login"
    assert job["status"] == "platform_draft"
    persisted = service.db.fetch_one(
        "SELECT status, platform_draft_id, failure_reason FROM publish_jobs WHERE id = ?",
        (job["id"],),
    )
    assert persisted == {
        "status": "platform_draft",
        "platform_draft_id": "draft-media-id",
        "failure_reason": "",
    }


def test_publish_coordinator_persists_real_browser_progress(tmp_path):
    class ProgressPublisher(FakePlatformPublisher):
        def __init__(self, db):
            self.db = db
            self.persisted_statuses = []

        def publish_with_progress(self, payload, account, action, report_progress):
            for status in ("filling_content", "uploading_assets", "publishing"):
                report_progress(status)
                row = self.db.fetch_one(
                    "SELECT status FROM publish_jobs ORDER BY id DESC LIMIT 1"
                )
                self.persisted_statuses.append(row["status"])
            return super().publish(payload, account, action)

    service = make_service(tmp_path)
    project, account_id = prepare_publish_project(service, "真实发布进度")
    publisher = ProgressPublisher(service.db)
    service.publish_coordinator.publisher = publisher

    job = service.create_publish_job(project["id"], account_id, "draft")

    assert publisher.persisted_statuses == [
        "filling_content",
        "uploading_assets",
        "publishing",
    ]
    assert job["status"] == "platform_draft"


def test_concurrent_create_publish_job_is_idempotent_and_calls_publisher_once(tmp_path):
    class BlockingPublisher(FakePlatformPublisher):
        def __init__(self):
            self.calls = 0
            self.started = threading.Event()
            self.release = threading.Event()
            self.lock = threading.Lock()

        def publish(self, payload, account, action):
            with self.lock:
                self.calls += 1
            self.started.set()
            assert self.release.wait(timeout=5)
            return super().publish(payload, account, action)

    service = make_service(tmp_path)
    project, account_id = prepare_publish_project(service, "并发创建幂等")
    publisher = BlockingPublisher()
    service.publish_coordinator.publisher = publisher

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            service.create_publish_job, project["id"], account_id, "draft"
        )
        assert publisher.started.wait(timeout=5)
        second = executor.submit(
            service.create_publish_job, project["id"], account_id, "draft"
        )
        time.sleep(0.1)
        publisher.release.set()
        results = [first.result(timeout=5), second.result(timeout=5)]

    assert publisher.calls == 1
    assert {result["id"] for result in results} == {results[0]["id"]}
    assert service.db.fetch_one(
        "SELECT COUNT(*) AS count FROM publish_jobs WHERE project_id = ? AND account_id = ? AND action = 'draft'",
        (project["id"], account_id),
    )["count"] == 1


def test_failed_pending_input_load_cannot_overwrite_concurrent_execution(tmp_path):
    class CountingPublisher(FakePlatformPublisher):
        def __init__(self):
            self.calls = 0

        def publish(self, payload, account, action):
            self.calls += 1
            return super().publish(payload, account, action)

    service = make_service(tmp_path)
    project, account_id = prepare_publish_project(service, "输入加载失败竞态")
    pending = service.prepare_publish_job(project["id"], account_id, "draft")
    original_loader = service.publish_coordinator.account_loader
    first_load_started = threading.Event()
    release_first_load = threading.Event()
    load_calls = 0
    load_lock = threading.Lock()

    def fail_first_loader(requested_account_id):
        nonlocal load_calls
        with load_lock:
            load_calls += 1
            call_number = load_calls
        if call_number == 1:
            first_load_started.set()
            assert release_first_load.wait(timeout=5)
            raise RuntimeError("首个加载者读取到损坏输入")
        return original_loader(requested_account_id)

    publisher = CountingPublisher()
    service.publish_coordinator.account_loader = fail_first_loader
    service.publish_coordinator.publisher = publisher

    with ThreadPoolExecutor(max_workers=2) as executor:
        slow_failure = executor.submit(
            service.execute_publish_job, pending["id"]
        )
        assert first_load_started.wait(timeout=5)
        concurrent_execution = executor.submit(
            service.execute_publish_job, pending["id"]
        )
        try:
            concurrent_result = concurrent_execution.result(timeout=5)
        finally:
            release_first_load.set()
        results = [slow_failure.result(timeout=5), concurrent_result]

    assert concurrent_result["status"] == "platform_draft"
    assert publisher.calls == 1
    assert {result["status"] for result in results} == {"platform_draft"}
    assert service.get_publish_job(pending["id"])["status"] == "platform_draft"


def test_lost_attempt_is_checked_before_calling_publisher(tmp_path):
    class CountingPublisher(FakePlatformPublisher):
        def __init__(self):
            self.calls = 0

        def publish(self, payload, account, action):
            self.calls += 1
            return super().publish(payload, account, action)

    service = make_service(tmp_path)
    project, account_id = prepare_publish_project(service, "调用平台前所有权检查")
    pending = service.prepare_publish_job(project["id"], account_id, "draft")
    publisher = CountingPublisher()
    service.publish_coordinator.publisher = publisher
    original_claim = service.publish_coordinator._claim_execution

    def claim_then_lose_attempt(*args, **kwargs):
        attempt_token = original_claim(*args, **kwargs)
        if attempt_token is not None:
            service.db.execute(
                "UPDATE publish_jobs SET updated_at = '2026-07-15 01:00:00' WHERE id = ?",
                (pending["id"],),
            )
            lost = service.publish_coordinator.fail_stale_in_progress_job(
                pending["id"], "2026-07-15 01:15:00"
            )
            assert lost["status"] == "failed"
        return attempt_token

    service.publish_coordinator._claim_execution = claim_then_lose_attempt

    result = service.execute_publish_job(pending["id"])

    assert result["status"] == "failed"
    assert result["failure_stage"] == "发布中断"
    assert publisher.calls == 0


def test_create_publish_job_executes_existing_pending_job_once(tmp_path):
    class CountingPublisher(FakePlatformPublisher):
        def __init__(self):
            self.calls = 0

        def publish(self, payload, account, action):
            self.calls += 1
            return super().publish(payload, account, action)

    service = make_service(tmp_path)
    project, account_id = prepare_publish_project(service, "待执行创建幂等")
    publisher = CountingPublisher()
    service.publish_coordinator.publisher = publisher
    pending = service.prepare_publish_job(project["id"], account_id, "draft")

    created = service.create_publish_job(project["id"], account_id, "draft")
    replayed = service.create_publish_job(project["id"], account_id, "draft")

    assert created["id"] == replayed["id"] == pending["id"]
    assert created["status"] == replayed["status"] == "platform_draft"
    assert publisher.calls == 1
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1


def test_terminal_and_failed_create_requests_return_existing_job_without_retry(tmp_path):
    class CountingPublisher(FakePlatformPublisher):
        def __init__(self):
            self.calls = 0

        def publish(self, payload, account, action):
            self.calls += 1
            return super().publish(payload, account, action)

    service = make_service(tmp_path)
    project, account_id = prepare_publish_project(service, "终态创建幂等")
    publisher = CountingPublisher()
    service.publish_coordinator.publisher = publisher

    terminal = service.create_publish_job(project["id"], account_id, "draft")
    service.db.execute(
        "UPDATE platform_accounts SET auth_status = '未授权' WHERE id = ?",
        (account_id,),
    )
    repeated_terminal = service.create_publish_job(project["id"], account_id, "draft")

    assert repeated_terminal["id"] == terminal["id"]
    assert repeated_terminal["status"] == "platform_draft"
    assert publisher.calls == 1

    service.db.execute(
        "UPDATE publish_jobs SET status = 'failed', platform_draft_id = '', platform_url = '', response_json = '{}', failure_stage = '测试', failure_reason = '失败' WHERE id = ?",
        (terminal["id"],),
    )
    repeated_failed = service.create_publish_job(project["id"], account_id, "draft")

    assert repeated_failed["id"] == terminal["id"]
    assert repeated_failed["status"] == "failed"
    assert publisher.calls == 1


def test_draft_and_publish_actions_have_independent_idempotent_jobs(tmp_path):
    class ActionPublisher(FakePlatformPublisher):
        def __init__(self):
            self.calls = []

        def publish(self, payload, account, action):
            self.calls.append(action)
            if action == "publish":
                return PublishResult(
                    status="published",
                    platform_publish_id="publish-action-id",
                    platform_url="https://mp.weixin.qq.com/s/publish-action-id",
                    response={
                        "source": "wechat_platform",
                        "event": "article_published",
                        "receipt_id": "publish-action-id",
                        "corroboration": "publish_success",
                    },
                )
            return super().publish(payload, account, action)

    service = make_service(tmp_path)
    project, account_id = prepare_publish_project(service, "动作独立幂等")
    publisher = ActionPublisher()
    service.publish_coordinator.publisher = publisher

    draft = service.create_publish_job(project["id"], account_id, "draft")
    published = service.create_publish_job(project["id"], account_id, "publish")

    assert draft["id"] != published["id"]
    assert draft["status"] == "platform_draft"
    assert published["status"] == "published"
    assert publisher.calls == ["draft", "publish"]


def test_confirmation_expiry_fails_same_job_and_allows_retry(tmp_path):
    class ExpiringPublisher:
        def __init__(self):
            self.calls = 0
            self.expire_callback = None

        def publish(self, payload, account, action):
            self.calls += 1
            if self.calls == 1:
                return PublishResult(
                    status="awaiting_confirmation",
                    response={
                        "source": "wechat_platform",
                        "event": "operator_confirmation_required",
                        "job_context": {"resume_handle": "private-expiring-handle"},
                    },
                    requires_confirmation=True,
                )
            return PublishResult(
                status="published",
                platform_publish_id="retry-after-expiry",
                platform_url="https://mp.weixin.qq.com/s/retry-after-expiry",
                response={
                    "source": "wechat_platform",
                    "event": "article_published",
                    "receipt_id": "retry-after-expiry",
                    "corroboration": "publish_success",
                },
            )

        def bind_confirmation_expiry(self, job_context, callback):
            assert job_context == {"resume_handle": "private-expiring-handle"}
            self.expire_callback = callback

        def resume(self, account, job_context):
            raise AssertionError("expired confirmation must use retry")

    service = make_service(tmp_path)
    project, account_id = prepare_publish_project(service, "确认过期重试")
    publisher = ExpiringPublisher()
    service.publish_coordinator.publisher = publisher

    waiting = service.create_publish_job(project["id"], account_id, "publish")
    assert waiting["status"] == "awaiting_confirmation"
    assert publisher.expire_callback is not None

    publisher.expire_callback()
    failed = service.get_publish_job(waiting["id"])
    assert failed["status"] == "failed"
    assert "过期" in failed["failure_reason"]
    assert "private-expiring-handle" not in str(failed)

    retried = service.retry_publish_job(waiting["id"])
    assert retried["id"] == waiting["id"]
    assert retried["status"] == "published"
    assert publisher.calls == 2


def test_restart_fails_unrecoverable_confirmation_and_late_callback_cannot_overwrite_retry(tmp_path):
    db = Database(tmp_path / "content.db")
    db.init_schema()
    first_service = ContentService(
        db=db,
        ai_client=FakeAIClient(),
        upload_dir=tmp_path / "uploads-first",
        platform_publisher=FakePlatformPublisher(),
    )
    project, account_id = prepare_publish_project(first_service, "重启确认恢复")

    class FirstPublisher:
        def __init__(self):
            self.expire_callback = None

        def publish(self, payload, account, action):
            return PublishResult(
                status="awaiting_confirmation",
                response={
                    "source": "wechat_platform",
                    "event": "operator_confirmation_required",
                    "job_context": {"resume_handle": "restart-private-handle"},
                },
                requires_confirmation=True,
            )

        def bind_confirmation_expiry(self, job_context, callback):
            self.expire_callback = callback

    first_publisher = FirstPublisher()
    first_service.publish_coordinator.publisher = first_publisher
    waiting = first_service.create_publish_job(project["id"], account_id, "publish")

    class RestartPublisher(FakePlatformPublisher):
        def publish(self, payload, account, action):
            return PublishResult(
                status="published",
                platform_publish_id="restart-retry-id",
                platform_url="https://mp.weixin.qq.com/s/restart-retry-id",
                response={
                    "source": "wechat_platform",
                    "event": "article_published",
                    "receipt_id": "restart-retry-id",
                    "corroboration": "publish_success",
                },
            )

    restarted = ContentService(
        db=db,
        ai_client=FakeAIClient(),
        upload_dir=tmp_path / "uploads-restarted",
        platform_publisher=RestartPublisher(),
    )
    failed = restarted.get_publish_job(waiting["id"])
    assert failed["status"] == "failed"
    assert "服务重启" in failed["failure_reason"]

    retried = restarted.retry_publish_job(waiting["id"])
    assert retried["status"] == "published"
    first_publisher.expire_callback()
    assert restarted.get_publish_job(waiting["id"])["status"] == "published"


def test_stale_confirmation_scan_does_not_fail_recent_handle(tmp_path):
    service = make_service(tmp_path)
    project, account_id = prepare_publish_project(service, "确认扫描时间")

    class WaitingPublisher:
        def publish(self, payload, account, action):
            return PublishResult(
                status="awaiting_confirmation",
                response={
                    "source": "wechat_platform",
                    "event": "operator_confirmation_required",
                    "job_context": {"resume_handle": "scan-private-handle"},
                },
                requires_confirmation=True,
            )

    service.publish_coordinator.publisher = WaitingPublisher()
    waiting = service.create_publish_job(project["id"], account_id, "publish")
    service.db.execute(
        "UPDATE publish_jobs SET updated_at = '2026-07-15 01:00:00' WHERE id = ?",
        (waiting["id"],),
    )

    service.recover_stale_awaiting_confirmations(datetime(2026, 7, 15, 1, 4))
    assert service.get_publish_job(waiting["id"])["status"] == "awaiting_confirmation"

    service.recover_stale_awaiting_confirmations(datetime(2026, 7, 15, 1, 6))
    assert service.get_publish_job(waiting["id"])["status"] == "failed"


def test_direct_pending_publish_job_recovers_after_restart_without_scheduled_task(
    tmp_path: Path,
):
    database_path = tmp_path / "content.db"
    first = make_service(tmp_path)
    project, account_id = prepare_publish_project(first, "独立发布崩溃恢复")
    pending = first.prepare_publish_job(project["id"], account_id, "draft")

    class CountingPublisher(FakePlatformPublisher):
        def __init__(self):
            self.calls = 0

        def publish(self, payload, account, action):
            self.calls += 1
            return super().publish(payload, account, action)

    publisher = CountingPublisher()
    restarted = ContentService(
        db=Database(database_path),
        ai_client=FakeAIClient(),
        upload_dir=tmp_path / "uploads-restarted",
        platform_publisher=publisher,
    )

    restarted.recover_publish_jobs(datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc))
    restarted.recover_publish_jobs(datetime(2026, 7, 15, 1, 1, tzinfo=timezone.utc))

    recovered = restarted.get_publish_job(pending["id"])
    assert recovered["id"] == pending["id"]
    assert recovered["status"] == "platform_draft"
    assert publisher.calls == 1
    assert restarted.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1
    assert restarted.db.fetch_one("SELECT COUNT(*) AS count FROM scheduled_tasks")["count"] == 0


def test_publish_recovery_isolates_bad_pending_job_and_continues_maintenance(
    tmp_path: Path,
):
    service = make_service(tmp_path)
    bad_project, bad_account_id = prepare_publish_project(service, "损坏发布输入")
    valid_project, valid_account_id = prepare_publish_project(service, "有效待发布")
    active_project, active_account_id = prepare_publish_project(service, "陈旧执行中")
    bad = service.prepare_publish_job(bad_project["id"], bad_account_id, "draft")
    valid = service.prepare_publish_job(valid_project["id"], valid_account_id, "draft")
    active = service.prepare_publish_job(active_project["id"], active_account_id, "draft")
    service.db.execute(
        "UPDATE platform_accounts SET auth_status = '未授权' WHERE id = ?",
        (bad_account_id,),
    )
    service.db.execute(
        """
        UPDATE publish_jobs
        SET status = 'checking_login', response_json = ?, updated_at = '2026-07-15 01:00:00'
        WHERE id = ?
        """,
        (json.dumps({"_attempt_token": "stale-maintenance-attempt"}), active["id"]),
    )

    class CountingPublisher(FakePlatformPublisher):
        def __init__(self):
            self.calls = 0

        def publish(self, payload, account, action):
            self.calls += 1
            return super().publish(payload, account, action)

    publisher = CountingPublisher()
    service.publish_coordinator.publisher = publisher

    result = service.recover_publish_jobs(
        datetime(2026, 7, 15, 1, 30, tzinfo=timezone.utc)
    )

    failed = service.get_publish_job(bad["id"])
    assert failed["status"] == "failed"
    assert failed["failure_stage"] == "加载发布输入"
    assert failed["failure_reason"]
    assert service.get_publish_job(valid["id"])["status"] == "platform_draft"
    assert service.get_publish_job(active["id"])["status"] == "failed"
    assert result == {"recovered": 2, "failed": 1}
    assert publisher.calls == 1


def test_direct_active_publish_recovery_fails_only_stale_job_and_retries_same_job(
    tmp_path: Path,
):
    database_path = tmp_path / "content.db"
    first = make_service(tmp_path)
    stale_project, account_id = prepare_publish_project(first, "陈旧独立发布")
    recent_project, _ = prepare_publish_project(first, "近期独立发布")
    stale = first.prepare_publish_job(stale_project["id"], account_id, "draft")
    recent = first.prepare_publish_job(recent_project["id"], account_id, "draft")
    first.db.execute(
        """
        UPDATE publish_jobs
        SET status = 'publishing', response_json = ?, updated_at = '2026-07-15 01:00:00'
        WHERE id = ?
        """,
        (json.dumps({"_attempt_token": "stale-attempt"}), stale["id"]),
    )
    first.db.execute(
        """
        UPDATE publish_jobs
        SET status = 'publishing', response_json = ?, updated_at = '2026-07-15 01:10:00'
        WHERE id = ?
        """,
        (json.dumps({"_attempt_token": "recent-attempt"}), recent["id"]),
    )

    class CountingPublisher(FakePlatformPublisher):
        def __init__(self):
            self.calls = 0

        def publish(self, payload, account, action):
            self.calls += 1
            return super().publish(payload, account, action)

    publisher = CountingPublisher()
    restarted = ContentService(
        db=Database(database_path),
        ai_client=FakeAIClient(),
        upload_dir=tmp_path / "uploads-restarted",
        platform_publisher=publisher,
    )

    restarted.recover_publish_jobs(
        datetime(2026, 7, 15, 1, 16, tzinfo=timezone.utc)
    )

    failed = restarted.get_publish_job(stale["id"])
    assert failed["status"] == "failed"
    assert failed["failure_stage"] == "发布中断"
    assert restarted.get_publish_job(recent["id"])["status"] == "publishing"
    replayed = restarted.create_publish_job(stale_project["id"], account_id, "draft")
    assert replayed["id"] == stale["id"]
    assert replayed["status"] == "failed"
    assert publisher.calls == 0
    assert restarted.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 2

    retried = restarted.retry_publish_job(stale["id"])

    assert retried["id"] == stale["id"]
    assert retried["status"] == "platform_draft"
    assert publisher.calls == 1
    assert restarted.get_publish_job(recent["id"])["status"] == "publishing"
    assert restarted.db.fetch_one("SELECT COUNT(*) AS count FROM scheduled_tasks")["count"] == 0


def test_stale_pre_publish_task_fails_and_retries_without_touching_recent_execution(tmp_path):
    class CountingPublisher(FakePlatformPublisher):
        def __init__(self):
            self.calls = 0

        def publish(self, payload, account, action):
            self.calls += 1
            return super().publish(payload, account, action)

    service = make_service(tmp_path)
    publisher = CountingPublisher()
    service.publish_coordinator.publisher = publisher
    account_id = service.db.execute(
        "INSERT INTO platform_accounts (platform, name, auth_status) VALUES ('微信公众号', '生成中断账号', '已授权')"
    )

    def create_claimed_task(topic, started_at):
        task = service.create_task(
            {
                "platform": "微信公众号",
                "account_id": account_id,
                "account_name": "生成中断账号",
                "execute_time": "2026-07-15 01:00",
                "task_type": "微信公众号图文生成",
                "topic": topic,
                "publish_action": "draft",
            }
        )
        run_id = service._claim_task_execution(task["id"], started_at)
        assert run_id is not None
        return task, run_id

    stale, stale_run_id = create_claimed_task("陈旧生成任务", "2026-07-15 01:00")
    recent, recent_run_id = create_claimed_task("活跃生成任务", "2026-07-15 01:10")

    service.execute_due_tasks(datetime(2026, 7, 15, 1, 16))

    stale_detail = service.get_task(stale["id"])
    recent_detail = service.get_task(recent["id"])
    assert stale_detail["status"] == "失败"
    assert stale_detail["publish_job_id"] is None
    assert stale_detail["failure_stage"] == "内容生成中断"
    assert stale_detail["runs"][0]["id"] == stale_run_id
    assert stale_detail["runs"][0]["status"] == "失败"
    assert recent_detail["status"] == "执行中"
    assert recent_detail["runs"][0]["id"] == recent_run_id
    assert recent_detail["runs"][0]["status"] == "执行中"
    assert publisher.calls == 0

    retried = service.retry_failed_tasks([stale["id"]])
    retried_detail = service.get_task(stale["id"])
    assert retried == {"updated": 1}
    assert retried_detail["status"] == "已生成平台草稿"
    assert retried_detail["publish_job"]["status"] == "platform_draft"
    assert retried_detail["retry_count"] == 1
    assert publisher.calls == 1


def test_retry_publish_job_claim_allows_only_one_publisher_call(tmp_path):
    class MissingEvidencePublisher(FakePlatformPublisher):
        def publish(self, payload, account, action):
            return PublishResult(status="platform_draft")

    class CountingPublisher(FakePlatformPublisher):
        def __init__(self):
            self.calls = 0
            self.lock = threading.Lock()

        def publish(self, payload, account, action):
            with self.lock:
                self.calls += 1
            return super().publish(payload, account, action)

    service = make_service(tmp_path)
    project, account_id = prepare_publish_project(service, "retry claim 测试")
    service.publish_coordinator.publisher = MissingEvidencePublisher()
    failed = service.create_publish_job(project["id"], account_id, "draft")
    assert failed["status"] == "failed"

    barrier = threading.Barrier(2)
    original_builder = service.publish_coordinator.payload_builder

    def synchronized_builder(project_row, account_name):
        payload = original_builder(project_row, account_name)
        barrier.wait(timeout=5)
        return payload

    publisher = CountingPublisher()
    service.publish_coordinator.payload_builder = synchronized_builder
    service.publish_coordinator.publisher = publisher

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: service.retry_publish_job(failed["id"]), range(2)))

    assert publisher.calls == 1
    assert {result["id"] for result in results} == {failed["id"]}
    assert service.get_publish_job(failed["id"])["status"] == "platform_draft"


def test_stale_retry_cannot_claim_a_later_failed_generation(tmp_path):
    class MissingEvidencePublisher(FakePlatformPublisher):
        def publish(self, payload, account, action):
            return PublishResult(status="platform_draft")

    class FailThenSucceedPublisher(FakePlatformPublisher):
        def __init__(self):
            self.calls = 0
            self.lock = threading.Lock()
            self.first_call_finished = threading.Event()

        def publish(self, payload, account, action):
            with self.lock:
                self.calls += 1
                call_number = self.calls
            if call_number == 1:
                self.first_call_finished.set()
                raise RuntimeError("first retry failed quickly")
            return super().publish(payload, account, action)

    service = make_service(tmp_path)
    project, account_id = prepare_publish_project(service, "retry ABA 测试")
    service.publish_coordinator.publisher = MissingEvidencePublisher()
    failed = service.create_publish_job(project["id"], account_id, "draft")
    assert failed["status"] == "failed"
    initial_private = service.db.fetch_one(
        "SELECT response_json FROM publish_jobs WHERE id = ?", (failed["id"],)
    )
    initial_token = json.loads(initial_private["response_json"])["_attempt_token"]
    assert len(initial_token) >= 32
    assert initial_token not in str(failed)

    publisher = FailThenSucceedPublisher()
    original_builder = service.publish_coordinator.payload_builder
    builder_barrier = threading.Barrier(2)
    builder_calls = 0
    builder_lock = threading.Lock()

    def delay_old_generation_contender(project_row, account_name):
        nonlocal builder_calls
        payload = original_builder(project_row, account_name)
        with builder_lock:
            builder_calls += 1
            call_number = builder_calls
        builder_barrier.wait(timeout=5)
        if call_number == 2:
            assert publisher.first_call_finished.wait(timeout=5)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                current = service.db.fetch_one(
                    "SELECT status FROM publish_jobs WHERE id = ?", (failed["id"],)
                )
                if current and current["status"] == "failed":
                    break
                time.sleep(0.01)
            else:
                raise AssertionError("winning retry did not persist failure")
        return payload

    service.publish_coordinator.payload_builder = delay_old_generation_contender
    service.publish_coordinator.publisher = publisher
    with ThreadPoolExecutor(max_workers=2) as executor:
        stale_results = list(
            executor.map(lambda _: service.retry_publish_job(failed["id"]), range(2))
        )

    assert publisher.calls == 1
    assert {result["status"] for result in stale_results} == {"failed"}
    failed_again_private = service.db.fetch_one(
        "SELECT response_json FROM publish_jobs WHERE id = ?", (failed["id"],)
    )
    failed_again_token = json.loads(failed_again_private["response_json"])["_attempt_token"]
    assert failed_again_token != initial_token
    assert failed_again_token not in str(stale_results)

    service.publish_coordinator.payload_builder = original_builder
    fresh_retry = service.retry_publish_job(failed["id"])

    assert publisher.calls == 2
    assert fresh_retry["status"] == "platform_draft"
    fresh_private = service.db.fetch_one(
        "SELECT response_json FROM publish_jobs WHERE id = ?", (failed["id"],)
    )
    fresh_token = json.loads(fresh_private["response_json"])["_attempt_token"]
    assert fresh_token not in {initial_token, failed_again_token}
    assert fresh_token not in str(fresh_retry)


@pytest.mark.parametrize("late_outcome", ["success", "failure"])
def test_late_stale_attempt_cannot_overwrite_new_attempt_evidence(
    tmp_path, late_outcome
):
    class BlockingFirstAttemptPublisher(FakePlatformPublisher):
        def __init__(self):
            self.calls = 0
            self.lock = threading.Lock()
            self.first_started = threading.Event()
            self.release_first = threading.Event()

        def publish(self, payload, account, action):
            with self.lock:
                self.calls += 1
                call_number = self.calls
            if call_number == 1:
                self.first_started.set()
                assert self.release_first.wait(timeout=5)
                if late_outcome == "failure":
                    raise RuntimeError("旧 attempt 晚到失败")
                publish_id = "old-attempt-publish"
            else:
                publish_id = "new-attempt-publish"
            return PublishResult(
                status="published",
                platform_publish_id=publish_id,
                platform_url=f"https://mp.weixin.qq.com/s/{publish_id}",
                response={
                    "source": "wechat_platform",
                    "event": "article_published",
                    "receipt_id": publish_id,
                    "corroboration": "publish_success",
                },
            )

    service = make_service(tmp_path)
    project, account_id = prepare_publish_project(service, "晚到 attempt ownership")
    publisher = BlockingFirstAttemptPublisher()
    service.publish_coordinator.publisher = publisher

    with ThreadPoolExecutor(max_workers=1) as executor:
        old_attempt = executor.submit(
            service.create_publish_job, project["id"], account_id, "publish"
        )
        assert publisher.first_started.wait(timeout=5)
        job = service.db.fetch_one(
            "SELECT id, status FROM publish_jobs ORDER BY id DESC LIMIT 1"
        )
        assert job is not None
        assert job["status"] == "checking_login"
        service.db.execute(
            "UPDATE publish_jobs SET updated_at = '2026-07-14 01:00:00' WHERE id = ?",
            (job["id"],),
        )

        timed_out = service.publish_coordinator.fail_stale_in_progress_job(
            int(job["id"]), "2026-07-14 01:15:00"
        )
        assert timed_out["status"] == "failed"
        assert timed_out["failure_stage"] == "发布中断"

        retried = service.retry_publish_job(int(job["id"]))
        new_private = service.db.fetch_one(
            "SELECT response_json FROM publish_jobs WHERE id = ?", (job["id"],)
        )["response_json"]
        assert retried["status"] == "published"
        assert retried["platform_publish_id"] == "new-attempt-publish"
        assert retried["response"]["receipt_id"] == "new-attempt-publish"

        publisher.release_first.set()
        old_attempt.result(timeout=5)

    final = service.get_publish_job(int(job["id"]))
    final_private = service.db.fetch_one(
        "SELECT response_json FROM publish_jobs WHERE id = ?", (job["id"],)
    )["response_json"]
    assert publisher.calls == 2
    assert final["status"] == "published"
    assert final["platform_publish_id"] == "new-attempt-publish"
    assert final["response"]["receipt_id"] == "new-attempt-publish"
    assert final["failure_stage"] == ""
    assert final["failure_reason"] == ""
    assert final_private == new_private


@pytest.mark.parametrize("late_outcome", ["success", "failure"])
def test_late_stale_resume_cannot_overwrite_retry_evidence(tmp_path, late_outcome):
    class BlockingResumePublisher(FakePlatformPublisher):
        def __init__(self):
            self.publish_calls = 0
            self.resume_calls = 0
            self.resume_started = threading.Event()
            self.release_resume = threading.Event()

        def publish(self, payload, account, action):
            self.publish_calls += 1
            if self.publish_calls == 1:
                return PublishResult(
                    status="awaiting_confirmation",
                    response={
                        "source": "wechat_platform",
                        "event": "operator_confirmation_required",
                        "job_context": {"resume_handle": "late-resume-handle"},
                    },
                    requires_confirmation=True,
                )
            return PublishResult(
                status="published",
                platform_publish_id="new-retry-publish",
                platform_url="https://mp.weixin.qq.com/s/new-retry-publish",
                response={
                    "source": "wechat_platform",
                    "event": "article_published",
                    "receipt_id": "new-retry-publish",
                    "corroboration": "publish_success",
                },
            )

        def resume(self, account, job_context):
            self.resume_calls += 1
            self.resume_started.set()
            assert self.release_resume.wait(timeout=5)
            if late_outcome == "failure":
                raise RuntimeError("旧 resume 晚到失败")
            return PublishResult(
                status="published",
                platform_publish_id="old-resume-publish",
                platform_url="https://mp.weixin.qq.com/s/old-resume-publish",
                response={
                    "source": "wechat_platform",
                    "event": "article_published",
                    "receipt_id": "old-resume-publish",
                    "corroboration": "publish_success",
                },
            )

    service = make_service(tmp_path)
    project, account_id = prepare_publish_project(service, "晚到 resume ownership")
    publisher = BlockingResumePublisher()
    service.publish_coordinator.publisher = publisher
    awaiting = service.create_publish_job(project["id"], account_id, "publish")
    assert awaiting["status"] == "awaiting_confirmation"

    with ThreadPoolExecutor(max_workers=1) as executor:
        old_resume = executor.submit(service.resume_publish_job, awaiting["id"])
        assert publisher.resume_started.wait(timeout=5)
        service.db.execute(
            "UPDATE publish_jobs SET updated_at = '2026-07-14 01:00:00' WHERE id = ?",
            (awaiting["id"],),
        )

        timed_out = service.publish_coordinator.fail_stale_in_progress_job(
            awaiting["id"], "2026-07-14 01:15:00"
        )
        assert timed_out["status"] == "failed"

        retried = service.retry_publish_job(awaiting["id"])
        new_private = service.db.fetch_one(
            "SELECT response_json FROM publish_jobs WHERE id = ?", (awaiting["id"],)
        )["response_json"]
        assert retried["status"] == "published"
        assert retried["platform_publish_id"] == "new-retry-publish"
        assert retried["response"]["receipt_id"] == "new-retry-publish"

        publisher.release_resume.set()
        old_resume.result(timeout=5)

    final = service.get_publish_job(awaiting["id"])
    final_private = service.db.fetch_one(
        "SELECT response_json FROM publish_jobs WHERE id = ?", (awaiting["id"],)
    )["response_json"]
    assert publisher.publish_calls == 2
    assert publisher.resume_calls == 1
    assert final["status"] == "published"
    assert final["platform_publish_id"] == "new-retry-publish"
    assert final["response"]["receipt_id"] == "new-retry-publish"
    assert final["failure_stage"] == ""
    assert final["failure_reason"] == ""
    assert final_private == new_private


def test_recent_active_attempt_is_not_timed_out(tmp_path):
    class BlockingPublisher(FakePlatformPublisher):
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def publish(self, payload, account, action):
            self.started.set()
            assert self.release.wait(timeout=5)
            return PublishResult(
                status="published",
                platform_publish_id="recent-active-publish",
                platform_url="https://mp.weixin.qq.com/s/recent-active-publish",
                response={
                    "source": "wechat_platform",
                    "event": "article_published",
                    "receipt_id": "recent-active-publish",
                    "corroboration": "publish_success",
                },
            )

    service = make_service(tmp_path)
    project, account_id = prepare_publish_project(service, "近期 active attempt")
    publisher = BlockingPublisher()
    service.publish_coordinator.publisher = publisher

    with ThreadPoolExecutor(max_workers=1) as executor:
        active_attempt = executor.submit(
            service.create_publish_job, project["id"], account_id, "publish"
        )
        assert publisher.started.wait(timeout=5)
        job = service.db.fetch_one(
            "SELECT id, status FROM publish_jobs ORDER BY id DESC LIMIT 1"
        )
        assert job is not None

        unchanged = service.publish_coordinator.fail_stale_in_progress_job(
            int(job["id"]), "2000-01-01 00:00:00"
        )
        assert unchanged["status"] == "checking_login"

        publisher.release.set()
        active_attempt.result(timeout=5)

    assert service.get_publish_job(int(job["id"]))["status"] == "published"


def test_resume_publish_job_claim_allows_only_one_publisher_call(tmp_path):
    class AwaitingPublisher(FakePlatformPublisher):
        def publish(self, payload, account, action):
            return PublishResult(
                status="awaiting_confirmation",
                response={
                    "source": "wechat_platform",
                    "event": "operator_confirmation_required",
                    "job_context": {"resume_handle": "claim-handle"},
                },
                requires_confirmation=True,
            )

        def __init__(self):
            self.resume_calls = 0
            self.lock = threading.Lock()

        def resume(self, account, job_context):
            with self.lock:
                self.resume_calls += 1
            return PublishResult(
                status="published",
                platform_publish_id="publish-claim",
                platform_url="https://mp.weixin.qq.com/s/publish-claim",
                response={
                    "source": "wechat_platform",
                    "event": "article_published",
                    "receipt_id": "publish-claim",
                    "corroboration": "publish_api_response",
                },
            )

    service = make_service(tmp_path)
    project, account_id = prepare_publish_project(service, "resume claim 测试")
    publisher = AwaitingPublisher()
    service.publish_coordinator.publisher = publisher
    awaiting = service.create_publish_job(project["id"], account_id, "publish")
    assert awaiting["status"] == "awaiting_confirmation"

    barrier = threading.Barrier(2)
    original_loader = service.publish_coordinator.account_loader

    def synchronized_loader(requested_account_id):
        account = original_loader(requested_account_id)
        barrier.wait(timeout=5)
        return account

    service.publish_coordinator.account_loader = synchronized_loader
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: service.resume_publish_job(awaiting["id"]), range(2)))
    service.publish_coordinator.account_loader = original_loader

    assert publisher.resume_calls == 1
    assert {result["id"] for result in results} == {awaiting["id"]}
    assert service.get_publish_job(awaiting["id"])["status"] == "published"


def prepare_publish_project(service: ContentService, title: str):
    project = service.create_project(title, platform="微信公众号")
    service.generate_article(project["id"])
    images = service.generate_images(project["id"])["assets"]
    cover = next(asset for asset in images if asset["asset_type"] == "cover")
    service.save_layout(
        project["id"],
        [
            {
                "section_type": "title",
                "content": project["title"],
                "media_asset_id": cover["id"],
                "sort_order": 1,
            },
            {
                "section_type": "intro",
                "content": "协调器 claim 正文",
                "media_asset_id": None,
                "sort_order": 2,
            },
        ],
    )
    account_id = service.db.execute(
        "INSERT INTO platform_accounts (platform, name, auth_status) VALUES (?, ?, ?)",
        ("微信公众号", f"{title}账号", "已授权"),
    )
    return project, account_id


def test_publish_jobs_schema_and_default_task_action(tmp_path):
    service = make_service(tmp_path)
    project = service.create_project("浏览器发布测试", platform="微信公众号")
    account_id = service.db.execute(
        "INSERT INTO platform_accounts (platform, name) VALUES (?, ?)",
        ("微信公众号", "浏览器授权账号"),
    )

    job_id = service.db.execute(
        "INSERT INTO publish_jobs (project_id, account_id, platform, action, status) VALUES (?, ?, ?, ?, ?)",
        (project["id"], account_id, "微信公众号", "draft", "pending"),
    )
    assert service.db.fetch_one("SELECT action, status FROM publish_jobs WHERE id = ?", (job_id,)) == {
        "action": "draft",
        "status": "pending",
    }

    task_id = service.db.execute(
        """
        INSERT INTO scheduled_tasks (platform, account_name, execute_time, task_type, topic)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("微信公众号", "浏览器授权账号", "2026-07-13 10:00", "微信公众号图文生成", "测试发布"),
    )
    assert service.db.fetch_one("SELECT publish_action FROM scheduled_tasks WHERE id = ?", (task_id,)) == {
        "publish_action": "publish",
    }


def test_publish_jobs_rejects_non_wechat_platform(tmp_path):
    service = make_service(tmp_path)
    project = service.create_project("平台约束测试", platform="微信公众号")
    account_id = service.db.execute(
        "INSERT INTO platform_accounts (platform, name) VALUES (?, ?)",
        ("微信公众号", "浏览器授权账号"),
    )

    with pytest.raises(sqlite3.IntegrityError):
        service.db.execute(
            "INSERT INTO publish_jobs (project_id, account_id, platform, action, status) VALUES (?, ?, ?, ?, ?)",
            (project["id"], account_id, "小红书", "draft", "pending"),
        )


def test_publish_jobs_reject_non_wechat_account_and_project_on_insert_and_update(tmp_path):
    service = make_service(tmp_path)
    project = service.create_project("跨表平台约束测试", platform="微信公众号")
    xhs_project = service.create_project("小红书项目", platform="小红书")
    wechat_account_id = service.db.execute(
        "INSERT INTO platform_accounts (platform, name) VALUES (?, ?)",
        ("微信公众号", "公众号账号"),
    )
    xhs_account_id = service.db.execute(
        "INSERT INTO platform_accounts (platform, name) VALUES (?, ?)",
        ("小红书", "小红书账号"),
    )

    with pytest.raises(sqlite3.IntegrityError):
        service.db.execute(
            "INSERT INTO publish_jobs (project_id, account_id, platform, action, status) VALUES (?, ?, ?, ?, ?)",
            (project["id"], xhs_account_id, "微信公众号", "draft", "pending"),
        )

    with pytest.raises(sqlite3.IntegrityError):
        service.db.execute(
            "INSERT INTO publish_jobs (project_id, account_id, platform, action, status) VALUES (?, ?, ?, ?, ?)",
            (xhs_project["id"], wechat_account_id, "微信公众号", "draft", "pending"),
        )

    job_id = service.db.execute(
        "INSERT INTO publish_jobs (project_id, account_id, platform, action, status) VALUES (?, ?, ?, ?, ?)",
        (project["id"], wechat_account_id, "微信公众号", "draft", "pending"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        service.db.execute("UPDATE publish_jobs SET account_id = ? WHERE id = ?", (xhs_account_id, job_id))
    with pytest.raises(sqlite3.IntegrityError):
        service.db.execute("UPDATE publish_jobs SET project_id = ? WHERE id = ?", (xhs_project["id"], job_id))


def test_publish_jobs_block_parent_platform_downgrades(tmp_path):
    service = make_service(tmp_path)
    project = service.create_project("父表平台保护测试", platform="微信公众号")
    account_id = service.db.execute(
        "INSERT INTO platform_accounts (platform, name) VALUES (?, ?)",
        ("微信公众号", "公众号账号"),
    )
    service.db.execute(
        "INSERT INTO publish_jobs (project_id, account_id, platform, action, status) VALUES (?, ?, ?, ?, ?)",
        (project["id"], account_id, "微信公众号", "draft", "pending"),
    )

    with pytest.raises(sqlite3.IntegrityError):
        service.db.execute("UPDATE platform_accounts SET platform = ? WHERE id = ?", ("小红书", account_id))

    with pytest.raises(sqlite3.IntegrityError):
        service.db.execute("UPDATE content_projects SET platform = ? WHERE id = ?", ("小红书", project["id"]))

    service.db.execute("UPDATE platform_accounts SET name = ? WHERE id = ?", ("更新后的公众号账号", account_id))
    service.db.execute("UPDATE content_projects SET title = ? WHERE id = ?", ("更新后的项目", project["id"]))


def test_publish_jobs_allow_retries_for_one_account_but_reject_account_switch(tmp_path):
    service = make_service(tmp_path)
    project = service.create_project("账号一致性测试", platform="微信公众号")
    first_account_id = service.db.execute(
        "INSERT INTO platform_accounts (platform, name) VALUES (?, ?)",
        ("微信公众号", "第一个账号"),
    )
    second_account_id = service.db.execute(
        "INSERT INTO platform_accounts (platform, name) VALUES (?, ?)",
        ("微信公众号", "第二个账号"),
    )

    service.db.execute(
        "INSERT INTO publish_jobs (project_id, account_id, platform, action, status) VALUES (?, ?, ?, ?, ?)",
        (project["id"], first_account_id, "微信公众号", "draft", "failed"),
    )
    retry_job_id = service.db.execute(
        "INSERT INTO publish_jobs (project_id, account_id, platform, action, status) VALUES (?, ?, ?, ?, ?)",
        (project["id"], first_account_id, "微信公众号", "publish", "pending"),
    )

    with pytest.raises(sqlite3.IntegrityError):
        service.db.execute(
            "INSERT INTO publish_jobs (project_id, account_id, platform, action, status) VALUES (?, ?, ?, ?, ?)",
            (project["id"], second_account_id, "微信公众号", "publish", "pending"),
        )

    with pytest.raises(sqlite3.IntegrityError):
        service.db.execute(
            "UPDATE publish_jobs SET account_id = ? WHERE id = ?",
            (second_account_id, retry_job_id),
        )


@pytest.mark.parametrize(
    ("status", "extra_columns"),
    [
        ("platform_draft", ""),
        ("published", ""),
        ("awaiting_confirmation", ", requires_confirmation"),
    ],
)
def test_publish_jobs_reject_success_states_without_platform_evidence(tmp_path, status, extra_columns):
    service = make_service(tmp_path)
    project = service.create_project(f"证据约束测试-{status}", platform="微信公众号")
    account_id = service.db.execute(
        "INSERT INTO platform_accounts (platform, name) VALUES (?, ?)",
        ("微信公众号", "浏览器授权账号"),
    )
    columns = "project_id, account_id, platform, action, status" + extra_columns
    placeholders = "?, ?, ?, ?, ?" + (", ?" if extra_columns else "")
    params = (project["id"], account_id, "微信公众号", "publish", status) + ((0,) if extra_columns else ())

    with pytest.raises(sqlite3.IntegrityError):
        service.db.execute(
            f"INSERT INTO publish_jobs ({columns}) VALUES ({placeholders})",
            params,
        )


@pytest.mark.parametrize(
    ("status", "platform_url", "platform_draft_id", "platform_publish_id", "response_json"),
    [
        ("platform_draft", "https://example.com/draft", "draft-1", "", '{"media_id":"draft-1"}'),
        ("platform_draft", "https://mp.weixin.qq.com/draft/1", "", "", '{"media_id":"draft-1"}'),
        ("platform_draft", "https://mp.weixin.qq.com/draft/1", "draft-1", "", "{}"),
        ("published", "https://example.com/article", "", "publish-1", '{"publish_id":"publish-1"}'),
        ("published", "https://mp.weixin.qq.com/s/1", "", "", '{"publish_id":"publish-1"}'),
        ("published", "https://mp.weixin.qq.com/s/1", "", "publish-1", "{}"),
    ],
)
def test_publish_jobs_reject_fake_url_or_incomplete_platform_evidence(
    tmp_path, status, platform_url, platform_draft_id, platform_publish_id, response_json
):
    service = make_service(tmp_path)
    project = service.create_project(f"严格证据测试-{status}", platform="微信公众号")
    account_id = service.db.execute(
        "INSERT INTO platform_accounts (platform, name) VALUES (?, ?)",
        ("微信公众号", "浏览器授权账号"),
    )

    with pytest.raises(sqlite3.IntegrityError):
        service.db.execute(
            """
            INSERT INTO publish_jobs (
              project_id, account_id, platform, action, status,
              platform_draft_id, platform_publish_id, platform_url, response_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project["id"],
                account_id,
                "微信公众号",
                "publish",
                status,
                platform_draft_id,
                platform_publish_id,
                platform_url,
                response_json,
            ),
        )


def test_publish_jobs_accept_platform_evidence_and_confirmation(tmp_path):
    service = make_service(tmp_path)
    project = service.create_project("证据通过测试", platform="微信公众号")
    account_id = service.db.execute(
        "INSERT INTO platform_accounts (platform, name) VALUES (?, ?)",
        ("微信公众号", "浏览器授权账号"),
    )

    for status, fields in [
        (
            "platform_draft",
            {
                "platform_draft_id": "draft-1",
                "platform_url": "https://mp.weixin.qq.com/draft/1",
                "response_json": '{"media_id":"draft-1"}',
            },
        ),
        (
            "published",
            {
                "platform_publish_id": "publish-1",
                "platform_url": "https://mp.weixin.qq.com/s/published",
                "response_json": '{"publish_id":"publish-1"}',
            },
        ),
        ("awaiting_confirmation", {"requires_confirmation": 1}),
    ]:
        columns = ["project_id", "account_id", "platform", "action", "status", *fields]
        values = [project["id"], account_id, "微信公众号", "publish", status, *fields.values()]
        placeholders = ", ".join("?" for _ in columns)
        service.db.execute(
            f"INSERT INTO publish_jobs ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(values),
        )


def test_existing_scheduled_tasks_reject_invalid_publish_action_after_migration(tmp_path):
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as conn:
        conn.execute("CREATE TABLE scheduled_tasks (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE task_runs (id INTEGER PRIMARY KEY)")

    db = Database(database_path)
    db.init_schema()

    task_id = db.execute("INSERT INTO scheduled_tasks (publish_action) VALUES (?)", ("draft",))
    run_id = db.execute("INSERT INTO task_runs DEFAULT VALUES")
    task_columns = {row["name"] for row in db.fetch_all("PRAGMA table_info(scheduled_tasks)")}
    run_columns = {row["name"] for row in db.fetch_all("PRAGMA table_info(task_runs)")}

    assert "publish_job_id" in task_columns
    assert "publish_job_id" in run_columns
    assert "generation" in task_columns
    assert "generation" in run_columns
    assert db.fetch_one("SELECT generation FROM scheduled_tasks WHERE id = ?", (task_id,)) == {
        "generation": 0
    }
    assert db.fetch_one("SELECT generation FROM task_runs WHERE id = ?", (run_id,)) == {
        "generation": 0
    }

    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO scheduled_tasks (publish_action) VALUES (?)", ("invalid",))

    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE scheduled_tasks SET publish_action = ? WHERE id = ?", ("invalid", task_id))


def test_migration_does_not_revive_completed_once_run_after_new_generation_patch(
    tmp_path,
):
    class TopicAIClient(FakeAIClient):
        def recommend_topics(self, user_prompt: str):
            topic = "新主题" if "新主题" in user_prompt else "旧主题"
            return [
                TopicRecommendation(
                    direction="代际隔离",
                    subtitle="",
                    titles=[f"{topic}标题"],
                )
            ]

    class RecordingPublisher(FakePlatformPublisher):
        def __init__(self):
            self.calls = []

        def publish(self, payload, account, action):
            self.calls.append(payload["title"])
            return PublishResult(
                status="published",
                platform_publish_id=f"publish-{len(self.calls)}",
                platform_url=f"https://mp.weixin.qq.com/s/publish-{len(self.calls)}",
                response={
                    "source": "wechat_platform",
                    "event": "article_published",
                    "receipt_id": f"publish-{len(self.calls)}",
                    "corroboration": "publish_success",
                },
            )

    db = Database(tmp_path / "patched-once-migration.db")
    db.init_schema()
    publisher = RecordingPublisher()
    service = ContentService(
        db=db,
        ai_client=TopicAIClient(),
        upload_dir=tmp_path / "patched-once-uploads",
        platform_publisher=publisher,
    )
    account_id = db.execute(
        "INSERT INTO platform_accounts (platform, name, auth_status) VALUES ('微信公众号', '迁移代际账号', '已授权')"
    )
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "迁移代际账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "旧主题",
        }
    )
    first = service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))
    old_run_id = first["runs"][0]["id"]
    old_job_id = first["publish_job_id"]

    with db.transaction() as conn:
        conn.execute(
            "UPDATE task_runs SET generation = 0 WHERE id = ?",
            (old_run_id,),
        )
        conn.execute(
            """
            UPDATE scheduled_tasks
            SET topic = '新主题', status = '待执行', next_run_at = '2026-07-15 09:30',
                selected_title = '', title_direction = '', publish_result = '',
                content_project_id = NULL, publish_package_id = NULL,
                publish_job_id = NULL, generation = generation + 1
            WHERE id = ?
            """,
            (task["id"],),
        )

    db.init_schema()

    assert db.fetch_one(
        "SELECT generation, publish_job_id FROM task_runs WHERE id = ?",
        (old_run_id,),
    ) == {"generation": 0, "publish_job_id": old_job_id}
    assert db.fetch_one(
        "SELECT status, publish_job_id FROM scheduled_tasks WHERE id = ?",
        (task["id"],),
    ) == {"status": "待执行", "publish_job_id": None}

    second = service.execute_task(task["id"], datetime(2026, 7, 15, 9, 30))

    assert second["status"] == "已发布"
    assert second["publish_job_id"] != old_job_id
    assert db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 2
    assert publisher.calls == ["旧主题标题", "新主题标题"]


def test_existing_publish_package_backfills_publish_job_id_from_payload(tmp_path):
    db = Database(tmp_path / "legacy-package.db")
    db.init_schema()
    project_id = db.execute("INSERT INTO content_projects (title) VALUES ('legacy package')")
    account_id = db.execute(
        "INSERT INTO platform_accounts (platform, name, auth_status) VALUES ('微信公众号', '旧包账号', '已授权')"
    )
    job_id = db.execute(
        """
        INSERT INTO publish_jobs (project_id, account_id, platform, action, status)
        VALUES (?, ?, '微信公众号', 'publish', 'pending')
        """,
        (project_id, account_id),
    )
    package_id = db.execute(
        """
        INSERT INTO publish_packages (
          project_id, account_id, platform, account_name, payload_json, status, publish_job_id
        ) VALUES (?, ?, '微信公众号', '旧包账号', ?, 'pending', NULL)
        """,
        (project_id, account_id, json.dumps({"publish_job_id": job_id})),
    )

    db.init_schema()

    package = db.fetch_one("SELECT publish_job_id FROM publish_packages WHERE id = ?", (package_id,))
    assert package["publish_job_id"] == job_id


def test_publish_package_backfill_ignores_deleted_or_non_integer_job_ids(tmp_path):
    db = Database(tmp_path / "stale-legacy-package.db")
    db.init_schema()
    project_id = db.execute("INSERT INTO content_projects (title) VALUES ('stale legacy package')")
    account_id = db.execute(
        "INSERT INTO platform_accounts (platform, name, auth_status) VALUES ('微信公众号', '已删除账号', '已授权')"
    )
    job_id = db.execute(
        """
        INSERT INTO publish_jobs (project_id, account_id, platform, action, status)
        VALUES (?, ?, '微信公众号', 'publish', 'pending')
        """,
        (project_id, account_id),
    )
    stale_package_id = db.execute(
        """
        INSERT INTO publish_packages (
          project_id, account_id, platform, account_name, payload_json, status, publish_job_id
        ) VALUES (?, ?, '微信公众号', '已删除账号', ?, 'pending_publish', ?)
        """,
        (project_id, account_id, json.dumps({"publish_job_id": job_id}), job_id),
    )
    malformed_package_id = db.execute(
        """
        INSERT INTO publish_packages (
          project_id, account_id, platform, account_name, payload_json, status, publish_job_id
        ) VALUES (?, ?, '微信公众号', '已删除账号', ?, 'pending_publish', NULL)
        """,
        (project_id, account_id, json.dumps({"publish_job_id": str(job_id)})),
    )
    db.execute("DELETE FROM publish_jobs WHERE id = ?", (job_id,))

    db.init_schema()

    assert db.fetch_one("SELECT id FROM publish_jobs WHERE id = ?", (job_id,)) is None
    assert db.fetch_one(
        "SELECT publish_job_id FROM publish_packages WHERE id = ?", (stale_package_id,)
    )["publish_job_id"] is None
    assert db.fetch_one(
        "SELECT publish_job_id FROM publish_packages WHERE id = ?", (malformed_package_id,)
    )["publish_job_id"] is None


@pytest.mark.parametrize("source", ["run", "package"])
def test_existing_task_backfills_publish_job_from_latest_run_or_package(
    tmp_path, source
):
    db = Database(tmp_path / f"legacy-{source}-task-link.db")
    db.init_schema()
    account_id = db.execute(
        "INSERT INTO platform_accounts (platform, name, auth_status) VALUES ('微信公众号', '历史关联账号', '已授权')"
    )
    project_id = db.execute(
        "INSERT INTO content_projects (title, platform) VALUES ('历史关联项目', '微信公众号')"
    )
    job_id = db.execute(
        """
        INSERT INTO publish_jobs (
          project_id, account_id, platform, action, status,
          platform_publish_id, platform_url, response_json
        ) VALUES (?, ?, '微信公众号', 'publish', 'published', ?, ?, ?)
        """,
        (
            project_id,
            account_id,
            "historical-publish-id",
            "https://mp.weixin.qq.com/s/historical-publish-id",
            json.dumps(
                {
                    "source": "wechat_platform",
                    "event": "article_published",
                    "receipt_id": "historical-publish-id",
                    "corroboration": "publish_success",
                }
            ),
        ),
    )
    package_id = db.execute(
        """
        INSERT INTO publish_packages (
          project_id, account_id, platform, account_name, payload_json, status,
          publish_job_id
        ) VALUES (?, ?, '微信公众号', '历史关联账号', '{}', 'published', ?)
        """,
        (project_id, account_id, job_id if source == "package" else None),
    )
    task_id = db.execute(
        """
        INSERT INTO scheduled_tasks (
          platform, account_id, account_name, execute_time, task_type, topic,
          status, content_project_id, publish_package_id, publish_job_id
        ) VALUES ('微信公众号', ?, '历史关联账号', '2026-07-14 09:30',
                  '微信公众号图文生成', '历史关联迁移', '失败', ?, NULL, NULL)
        """,
        (account_id, project_id),
    )
    run_id = db.execute(
        """
        INSERT INTO task_runs (
          task_id, status, started_at, finished_at, content_project_id,
          publish_package_id, publish_job_id
        ) VALUES (?, '失败', '2026-07-14 09:30', '2026-07-14 09:31', ?, ?, ?)
        """,
        (
            task_id,
            project_id,
            package_id if source == "package" else None,
            job_id if source == "run" else None,
        ),
    )

    db.init_schema()
    db.init_schema()

    assert db.fetch_one(
        "SELECT publish_job_id FROM scheduled_tasks WHERE id = ?", (task_id,)
    )["publish_job_id"] == job_id
    assert db.fetch_one(
        "SELECT publish_job_id FROM task_runs WHERE id = ?", (run_id,)
    )["publish_job_id"] == job_id


def test_legacy_task_links_backfill_through_package_and_retry_reuses_job(tmp_path):
    class UnexpectedPublisher(FakePlatformPublisher):
        def __init__(self):
            self.calls = 0

        def publish(self, payload, account, action):
            self.calls += 1
            return super().publish(payload, account, action)

    db = Database(tmp_path / "legacy-task-links.db")
    db.init_schema()
    publisher = UnexpectedPublisher()
    service = ContentService(
        db=db,
        ai_client=FakeAIClient(),
        upload_dir=tmp_path / "uploads",
        platform_publisher=publisher,
    )
    account_id = db.execute(
        "INSERT INTO platform_accounts (platform, name, auth_status) VALUES ('微信公众号', '旧任务账号', '已授权')"
    )
    project_id = db.execute(
        "INSERT INTO content_projects (title, platform) VALUES ('旧任务项目', '微信公众号')"
    )
    job_id = db.execute(
        """
        INSERT INTO publish_jobs (
          project_id, account_id, platform, action, status,
          platform_publish_id, platform_url, response_json
        ) VALUES (?, ?, '微信公众号', 'publish', 'published', ?, ?, ?)
        """,
        (
            project_id,
            account_id,
            "legacy-publish-id",
            "https://mp.weixin.qq.com/s/legacy-publish-id",
            json.dumps(
                {
                    "source": "wechat_platform",
                    "event": "article_published",
                    "receipt_id": "legacy-publish-id",
                    "corroboration": "publish_success",
                }
            ),
        ),
    )
    package_id = db.execute(
        """
        INSERT INTO publish_packages (
          project_id, account_id, platform, account_name, payload_json, status, publish_job_id
        ) VALUES (?, ?, '微信公众号', '旧任务账号', ?, 'pending_publish', NULL)
        """,
        (project_id, account_id, json.dumps({"publish_job_id": job_id})),
    )
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "旧任务账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "旧失败任务恢复",
        }
    )
    db.execute(
        """
        UPDATE scheduled_tasks
        SET status = '失败', content_project_id = ?, publish_package_id = ?, publish_job_id = NULL
        WHERE id = ?
        """,
        (project_id, package_id, task["id"]),
    )
    run_id = db.execute(
        """
        INSERT INTO task_runs (
          task_id, status, started_at, finished_at, content_project_id,
          publish_package_id, publish_job_id
        ) VALUES (?, '失败', '2026-07-14 09:30', '2026-07-14 09:31', ?, ?, NULL)
        """,
        (task["id"], project_id, package_id),
    )

    db.init_schema()
    db.init_schema()

    assert db.fetch_one(
        "SELECT publish_job_id FROM publish_packages WHERE id = ?", (package_id,)
    )["publish_job_id"] == job_id
    assert db.fetch_one(
        "SELECT publish_job_id FROM scheduled_tasks WHERE id = ?", (task["id"],)
    )["publish_job_id"] == job_id
    assert db.fetch_one(
        "SELECT publish_job_id FROM task_runs WHERE id = ?", (run_id,)
    )["publish_job_id"] == job_id

    retried = service.retry_failed_tasks([task["id"]])
    recovered = service.get_task(task["id"])

    assert retried["updated"] == 1
    assert recovered["status"] == "已发布"
    assert recovered["publish_job_id"] == job_id
    assert db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1
    assert publisher.calls == 0


def test_concurrent_task_execution_claims_generation_once(tmp_path):
    class CountingPublisher(FakePlatformPublisher):
        def __init__(self):
            self.calls = 0
            self.lock = threading.Lock()

        def publish(self, payload, account, action):
            with self.lock:
                self.calls += 1
            return super().publish(payload, account, action)

    service = make_service(tmp_path)
    publisher = CountingPublisher()
    service.publish_coordinator.publisher = publisher
    account_id = service.db.execute(
        "INSERT INTO platform_accounts (platform, name, auth_status) VALUES (?, ?, ?)",
        ("微信公众号", "并发任务账号", "已授权"),
    )
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "并发任务账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "并发只生成一次",
            "publish_action": "draft",
        }
    )
    barrier = threading.Barrier(2)

    def execute():
        barrier.wait(timeout=5)
        return service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: execute(), range(2)))

    assert {result["id"] for result in results} == {task["id"]}
    assert publisher.calls == 1
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM task_runs")["count"] == 1
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM content_projects")["count"] == 1
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM publish_jobs")["count"] == 1


def test_concurrent_failed_task_retries_claim_once(tmp_path):
    class FailingPublisher(FakePlatformPublisher):
        def publish(self, payload, account, action):
            raise RuntimeError("first generation failed")

    class CountingPublisher(FakePlatformPublisher):
        def __init__(self):
            self.calls = 0
            self.lock = threading.Lock()

        def publish(self, payload, account, action):
            with self.lock:
                self.calls += 1
            return super().publish(payload, account, action)

    service = make_service(tmp_path)
    account_id = service.db.execute(
        "INSERT INTO platform_accounts (platform, name, auth_status) VALUES (?, ?, ?)",
        ("微信公众号", "重试并发账号", "已授权"),
    )
    service.publish_coordinator.publisher = FailingPublisher()
    task = service.create_task(
        {
            "platform": "微信公众号",
            "account_id": account_id,
            "account_name": "重试并发账号",
            "execute_time": "2026-07-14 09:30",
            "task_type": "微信公众号图文生成",
            "topic": "失败后并发重试",
            "publish_action": "draft",
        }
    )
    assert service.execute_task(task["id"], datetime(2026, 7, 14, 9, 30))["status"] == "失败"

    publisher = CountingPublisher()
    service.publish_coordinator.publisher = publisher
    fetch_barrier = threading.Barrier(2)
    original_fetch_all = service.db.fetch_all

    def synchronized_fetch_all(sql, params=()):
        rows = original_fetch_all(sql, params)
        if "SELECT id FROM scheduled_tasks WHERE status = '失败'" in sql:
            fetch_barrier.wait(timeout=5)
        return rows

    service.db.fetch_all = synchronized_fetch_all
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: service.retry_failed_tasks([task["id"]]), range(2)))

    assert sum(result["updated"] for result in results) == 1
    assert publisher.calls == 1
    assert service.db.fetch_one("SELECT COUNT(*) AS count FROM task_runs")["count"] == 2
    assert service.get_task(task["id"])["status"] == "已生成平台草稿"


def test_existing_publish_jobs_rebuilds_same_named_relaxed_result_triggers(tmp_path):
    database_path = tmp_path / "legacy-publish-jobs.db"
    with sqlite3.connect(database_path) as conn:
        conn.executescript(
            """
            CREATE TABLE content_projects (
              id INTEGER PRIMARY KEY,
              title TEXT NOT NULL,
              platform TEXT NOT NULL
            );
            CREATE TABLE platform_accounts (
              id INTEGER PRIMARY KEY,
              platform TEXT NOT NULL,
              name TEXT NOT NULL
            );
            CREATE TABLE publish_jobs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              project_id INTEGER NOT NULL,
              account_id INTEGER NOT NULL,
              platform TEXT NOT NULL,
              action TEXT NOT NULL,
              status TEXT NOT NULL,
              platform_draft_id TEXT NOT NULL DEFAULT '',
              platform_publish_id TEXT NOT NULL DEFAULT '',
              platform_url TEXT NOT NULL DEFAULT '',
              response_json TEXT NOT NULL DEFAULT '{}',
              requires_confirmation INTEGER NOT NULL DEFAULT 0,
              failure_stage TEXT NOT NULL DEFAULT '',
              failure_reason TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO content_projects (id, title, platform) VALUES (1, '旧项目', '微信公众号');
            INSERT INTO platform_accounts (id, platform, name) VALUES (1, '微信公众号', '旧账号');
            CREATE TRIGGER publish_jobs_result_insert
            BEFORE INSERT ON publish_jobs
            BEGIN
              SELECT NULL;
            END;
            CREATE TRIGGER publish_jobs_result_update
            BEFORE UPDATE OF status, platform_url, response_json ON publish_jobs
            BEGIN
              SELECT NULL;
            END;
            """
        )

    db = Database(database_path)
    db.init_schema()

    trigger_sql = db.fetch_all(
        "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name IN (?, ?)",
        ("publish_jobs_result_insert", "publish_jobs_result_update"),
    )
    assert len(trigger_sql) == 2
    assert all("mp.weixin.qq.com" in row["sql"] for row in trigger_sql)

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO publish_jobs (
              project_id, account_id, platform, action, status,
              platform_publish_id, platform_url, response_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, 1, "微信公众号", "publish", "published", "publish-1", "https://example.com/article", "{}"),
        )
