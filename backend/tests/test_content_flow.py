from pathlib import Path

from app.ai import GeneratedImage, TopicRecommendation
from app.database import Database
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


def make_service(tmp_path: Path) -> ContentService:
    db = Database(tmp_path / "content.db")
    db.init_schema()
    return ContentService(db=db, ai_client=FakeAIClient(), upload_dir=tmp_path / "uploads")


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

    package = service.create_publish_package(project["id"], platform="微信公众号", account_name="AI 记账工具公众号")
    assert package["status"] == "pending_publish"
    assert package["platform"] == "微信公众号"


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
