import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
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
    task_columns = {row["name"] for row in db.fetch_all("PRAGMA table_info(scheduled_tasks)")}
    run_columns = {row["name"] for row in db.fetch_all("PRAGMA table_info(task_runs)")}

    assert "publish_job_id" in task_columns
    assert "publish_job_id" in run_columns

    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO scheduled_tasks (publish_action) VALUES (?)", ("invalid",))

    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE scheduled_tasks SET publish_action = ? WHERE id = ?", ("invalid", task_id))


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
