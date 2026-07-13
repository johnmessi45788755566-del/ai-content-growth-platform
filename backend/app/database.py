import sqlite3
from contextlib import suppress
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS content_projects (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  title TEXT NOT NULL,
                  title_direction TEXT DEFAULT '',
                  platform TEXT NOT NULL DEFAULT '微信公众号',
                  current_stage TEXT NOT NULL DEFAULT 'article',
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS article_blocks (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  project_id INTEGER NOT NULL REFERENCES content_projects(id) ON DELETE CASCADE,
                  block_type TEXT NOT NULL,
                  label TEXT NOT NULL,
                  content TEXT NOT NULL,
                  sort_order INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS media_assets (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  project_id INTEGER NOT NULL REFERENCES content_projects(id) ON DELETE CASCADE,
                  asset_type TEXT NOT NULL,
                  url TEXT NOT NULL,
                  local_path TEXT NOT NULL,
                  prompt TEXT NOT NULL DEFAULT '',
                  selected INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS layout_blocks (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  project_id INTEGER NOT NULL REFERENCES content_projects(id) ON DELETE CASCADE,
                  section_type TEXT NOT NULL,
                  content TEXT NOT NULL,
                  media_asset_id INTEGER REFERENCES media_assets(id) ON DELETE SET NULL,
                  sort_order INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS draft_snapshots (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  project_id INTEGER NOT NULL REFERENCES content_projects(id) ON DELETE CASCADE,
                  title TEXT NOT NULL,
                  platform TEXT NOT NULL,
                  stage TEXT NOT NULL,
                  restore_stage TEXT NOT NULL,
                  snapshot_json TEXT NOT NULL DEFAULT '{}',
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS publish_packages (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  project_id INTEGER NOT NULL REFERENCES content_projects(id) ON DELETE CASCADE,
                  account_id INTEGER,
                  platform TEXT NOT NULL,
                  account_name TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'pending_publish',
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS platform_accounts (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  platform TEXT NOT NULL,
                  name TEXT NOT NULL,
                  external_id TEXT NOT NULL DEFAULT '',
                  owner TEXT NOT NULL DEFAULT '未分配',
                  tag TEXT NOT NULL DEFAULT '',
                  auth_status TEXT NOT NULL DEFAULT '已授权',
                  monitor_status TEXT NOT NULL DEFAULT '监测中',
                  sync_status TEXT NOT NULL DEFAULT '已同步',
                  role TEXT NOT NULL DEFAULT '',
                  audience TEXT NOT NULL DEFAULT '',
                  style TEXT NOT NULL DEFAULT '',
                  taboo TEXT NOT NULL DEFAULT '',
                  preference TEXT NOT NULL DEFAULT '',
                  auth_method TEXT NOT NULL DEFAULT '扫码授权',
                  qr_url TEXT NOT NULL DEFAULT '',
                  last_sync_at TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS publish_jobs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  project_id INTEGER NOT NULL REFERENCES content_projects(id) ON DELETE CASCADE,
                  account_id INTEGER NOT NULL REFERENCES platform_accounts(id) ON DELETE CASCADE,
                  platform TEXT NOT NULL CHECK(platform = '微信公众号'),
                  action TEXT NOT NULL CHECK(action IN ('draft', 'publish')),
                  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
                    'pending', 'checking_login', 'uploading_assets', 'filling_content',
                    'platform_draft', 'publishing', 'awaiting_confirmation', 'published', 'failed'
                  )),
                  platform_draft_id TEXT NOT NULL DEFAULT '',
                  platform_publish_id TEXT NOT NULL DEFAULT '',
                  platform_url TEXT NOT NULL DEFAULT '',
                  response_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(response_json)),
                  requires_confirmation INTEGER NOT NULL DEFAULT 0 CHECK(requires_confirmation IN (0, 1)),
                  failure_stage TEXT NOT NULL DEFAULT '',
                  failure_reason TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CHECK(
                    status NOT IN ('platform_draft', 'published')
                    OR (
                      status = 'platform_draft'
                      AND (
                        length(trim(platform_draft_id)) > 0
                        AND platform_url LIKE 'https://mp.weixin.qq.com/%'
                        AND json_valid(response_json)
                        AND json_type(response_json) = 'object'
                        AND json(response_json) <> '{}'
                      )
                    )
                    OR (
                      status = 'published'
                      AND (
                        length(trim(platform_publish_id)) > 0
                        AND platform_url LIKE 'https://mp.weixin.qq.com/%'
                        AND json_valid(response_json)
                        AND json_type(response_json) = 'object'
                        AND json(response_json) <> '{}'
                      )
                    )
                  ),
                  CHECK(status <> 'awaiting_confirmation' OR requires_confirmation = 1)
                );

                CREATE TABLE IF NOT EXISTS account_metric_snapshots (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  account_id INTEGER NOT NULL REFERENCES platform_accounts(id) ON DELETE CASCADE,
                  metric_date TEXT NOT NULL,
                  fans INTEGER NOT NULL DEFAULT 0,
                  reads INTEGER NOT NULL DEFAULT 0,
                  recommends INTEGER NOT NULL DEFAULT 0,
                  likes INTEGER NOT NULL DEFAULT 0,
                  comments INTEGER NOT NULL DEFAULT 0,
                  shares INTEGER NOT NULL DEFAULT 0,
                  favorites INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS platform_works (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  account_id INTEGER NOT NULL REFERENCES platform_accounts(id) ON DELETE CASCADE,
                  platform TEXT NOT NULL,
                  account_name TEXT NOT NULL,
                  title TEXT NOT NULL,
                  content_type TEXT NOT NULL DEFAULT '图文',
                  status TEXT NOT NULL DEFAULT '已发布',
                  publish_url TEXT NOT NULL DEFAULT '',
                  published_at TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS work_metric_snapshots (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  work_id INTEGER NOT NULL REFERENCES platform_works(id) ON DELETE CASCADE,
                  metric_date TEXT NOT NULL,
                  reads INTEGER NOT NULL DEFAULT 0,
                  recommends INTEGER NOT NULL DEFAULT 0,
                  likes INTEGER NOT NULL DEFAULT 0,
                  comments INTEGER NOT NULL DEFAULT 0,
                  shares INTEGER NOT NULL DEFAULT 0,
                  favorites INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  platform TEXT NOT NULL,
                  account_name TEXT NOT NULL,
                  execute_time TEXT NOT NULL,
                  task_type TEXT NOT NULL,
                  topic TEXT NOT NULL,
                  article_requirements TEXT NOT NULL DEFAULT '',
                  selected_title TEXT NOT NULL DEFAULT '',
                  title_direction TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL DEFAULT '待执行',
                  publish_action TEXT NOT NULL DEFAULT 'publish' CHECK(publish_action IN ('draft', 'publish')),
                  account_id INTEGER REFERENCES platform_accounts(id) ON DELETE SET NULL,
                  publish_result TEXT NOT NULL DEFAULT '',
                  manual_takeover_url TEXT NOT NULL DEFAULT '',
                  execution_mode TEXT NOT NULL DEFAULT 'once',
                  repeat_rule TEXT NOT NULL DEFAULT '',
                  weekly_day INTEGER,
                  next_run_at TEXT NOT NULL DEFAULT '',
                  failure_stage TEXT NOT NULL DEFAULT '',
                  failure_reason TEXT NOT NULL DEFAULT '',
                  retry_count INTEGER NOT NULL DEFAULT 0,
                  content_project_id INTEGER REFERENCES content_projects(id) ON DELETE SET NULL,
                  publish_package_id INTEGER REFERENCES publish_packages(id) ON DELETE SET NULL,
                  last_run_at TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS task_runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  task_id INTEGER NOT NULL REFERENCES scheduled_tasks(id) ON DELETE CASCADE,
                  status TEXT NOT NULL,
                  started_at TEXT NOT NULL,
                  finished_at TEXT NOT NULL DEFAULT '',
                  failure_stage TEXT NOT NULL DEFAULT '',
                  failure_reason TEXT NOT NULL DEFAULT '',
                  content_project_id INTEGER REFERENCES content_projects(id) ON DELETE SET NULL,
                  publish_package_id INTEGER REFERENCES publish_packages(id) ON DELETE SET NULL,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            for column_sql in [
                "ALTER TABLE publish_packages ADD COLUMN account_id INTEGER",
                "ALTER TABLE scheduled_tasks ADD COLUMN account_id INTEGER REFERENCES platform_accounts(id) ON DELETE SET NULL",
                "ALTER TABLE scheduled_tasks ADD COLUMN publish_action TEXT NOT NULL DEFAULT 'publish'",
                "ALTER TABLE scheduled_tasks ADD COLUMN publish_result TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE scheduled_tasks ADD COLUMN manual_takeover_url TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE scheduled_tasks ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'once'",
                "ALTER TABLE scheduled_tasks ADD COLUMN article_requirements TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE scheduled_tasks ADD COLUMN selected_title TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE scheduled_tasks ADD COLUMN title_direction TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE scheduled_tasks ADD COLUMN repeat_rule TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE scheduled_tasks ADD COLUMN weekly_day INTEGER",
                "ALTER TABLE scheduled_tasks ADD COLUMN next_run_at TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE scheduled_tasks ADD COLUMN failure_stage TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE scheduled_tasks ADD COLUMN failure_reason TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE scheduled_tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE scheduled_tasks ADD COLUMN content_project_id INTEGER REFERENCES content_projects(id) ON DELETE SET NULL",
                "ALTER TABLE scheduled_tasks ADD COLUMN publish_package_id INTEGER REFERENCES publish_packages(id) ON DELETE SET NULL",
                "ALTER TABLE scheduled_tasks ADD COLUMN last_run_at TEXT NOT NULL DEFAULT ''",
            ]:
                with suppress(sqlite3.OperationalError):
                    conn.execute(column_sql)

            conn.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS publish_jobs_platform_insert
                BEFORE INSERT ON publish_jobs
                WHEN COALESCE(NEW.platform, '') <> '微信公众号'
                BEGIN
                  SELECT RAISE(ABORT, 'publish_jobs platform must be 微信公众号');
                END;

                CREATE TRIGGER IF NOT EXISTS publish_jobs_platform_update
                BEFORE UPDATE OF platform ON publish_jobs
                WHEN COALESCE(NEW.platform, '') <> '微信公众号'
                BEGIN
                  SELECT RAISE(ABORT, 'publish_jobs platform must be 微信公众号');
                END;

                CREATE TRIGGER IF NOT EXISTS publish_jobs_platform_refs_insert
                BEFORE INSERT ON publish_jobs
                WHEN NOT EXISTS (
                  SELECT 1
                  FROM platform_accounts
                  WHERE id = NEW.account_id AND platform = '微信公众号'
                ) OR NOT EXISTS (
                  SELECT 1
                  FROM content_projects
                  WHERE id = NEW.project_id AND platform = '微信公众号'
                )
                BEGIN
                  SELECT RAISE(ABORT, 'publish_jobs account and project must use 微信公众号');
                END;

                CREATE TRIGGER IF NOT EXISTS publish_jobs_platform_refs_update
                BEFORE UPDATE OF project_id, account_id ON publish_jobs
                WHEN NOT EXISTS (
                  SELECT 1
                  FROM platform_accounts
                  WHERE id = NEW.account_id AND platform = '微信公众号'
                ) OR NOT EXISTS (
                  SELECT 1
                  FROM content_projects
                  WHERE id = NEW.project_id AND platform = '微信公众号'
                )
                BEGIN
                  SELECT RAISE(ABORT, 'publish_jobs account and project must use 微信公众号');
                END;

                CREATE TRIGGER IF NOT EXISTS publish_jobs_account_insert
                BEFORE INSERT ON publish_jobs
                WHEN EXISTS (
                  SELECT 1
                  FROM publish_jobs
                  WHERE project_id = NEW.project_id AND account_id <> NEW.account_id
                )
                BEGIN
                  SELECT RAISE(ABORT, 'publish_jobs project_id must use one account_id');
                END;

                CREATE TRIGGER IF NOT EXISTS publish_jobs_account_update
                BEFORE UPDATE OF project_id, account_id ON publish_jobs
                WHEN EXISTS (
                  SELECT 1
                  FROM publish_jobs
                  WHERE id <> NEW.id AND project_id = NEW.project_id AND account_id <> NEW.account_id
                )
                BEGIN
                  SELECT RAISE(ABORT, 'publish_jobs project_id must use one account_id');
                END;

                CREATE TRIGGER IF NOT EXISTS publish_jobs_result_insert
                BEFORE INSERT ON publish_jobs
                WHEN (
                  (NEW.status = 'platform_draft' AND NOT (
                    length(trim(COALESCE(NEW.platform_draft_id, ''))) > 0
                    AND COALESCE(NEW.platform_url, '') LIKE 'https://mp.weixin.qq.com/%'
                    AND json_valid(COALESCE(NEW.response_json, '{}'))
                    AND json_type(COALESCE(NEW.response_json, '{}')) = 'object'
                    AND json(COALESCE(NEW.response_json, '{}')) <> '{}'
                  ))
                  OR (NEW.status = 'published' AND NOT (
                    length(trim(COALESCE(NEW.platform_publish_id, ''))) > 0
                    AND COALESCE(NEW.platform_url, '') LIKE 'https://mp.weixin.qq.com/%'
                    AND json_valid(COALESCE(NEW.response_json, '{}'))
                    AND json_type(COALESCE(NEW.response_json, '{}')) = 'object'
                    AND json(COALESCE(NEW.response_json, '{}')) <> '{}'
                  ))
                  OR (NEW.status = 'awaiting_confirmation' AND COALESCE(NEW.requires_confirmation, 0) <> 1)
                )
                BEGIN
                  SELECT RAISE(ABORT, 'publish_jobs status requires platform evidence');
                END;

                CREATE TRIGGER IF NOT EXISTS publish_jobs_result_update
                BEFORE UPDATE OF status, platform_draft_id, platform_publish_id, platform_url, response_json, requires_confirmation ON publish_jobs
                WHEN (
                  (NEW.status = 'platform_draft' AND NOT (
                    length(trim(COALESCE(NEW.platform_draft_id, ''))) > 0
                    AND COALESCE(NEW.platform_url, '') LIKE 'https://mp.weixin.qq.com/%'
                    AND json_valid(COALESCE(NEW.response_json, '{}'))
                    AND json_type(COALESCE(NEW.response_json, '{}')) = 'object'
                    AND json(COALESCE(NEW.response_json, '{}')) <> '{}'
                  ))
                  OR (NEW.status = 'published' AND NOT (
                    length(trim(COALESCE(NEW.platform_publish_id, ''))) > 0
                    AND COALESCE(NEW.platform_url, '') LIKE 'https://mp.weixin.qq.com/%'
                    AND json_valid(COALESCE(NEW.response_json, '{}'))
                    AND json_type(COALESCE(NEW.response_json, '{}')) = 'object'
                    AND json(COALESCE(NEW.response_json, '{}')) <> '{}'
                  ))
                  OR (NEW.status = 'awaiting_confirmation' AND COALESCE(NEW.requires_confirmation, 0) <> 1)
                )
                BEGIN
                  SELECT RAISE(ABORT, 'publish_jobs status requires platform evidence');
                END;

                CREATE TRIGGER IF NOT EXISTS scheduled_tasks_publish_action_insert
                BEFORE INSERT ON scheduled_tasks
                WHEN COALESCE(NEW.publish_action, '') NOT IN ('draft', 'publish')
                BEGIN
                  SELECT RAISE(ABORT, 'scheduled_tasks publish_action must be draft or publish');
                END;

                CREATE TRIGGER IF NOT EXISTS scheduled_tasks_publish_action_update
                BEFORE UPDATE OF publish_action ON scheduled_tasks
                WHEN COALESCE(NEW.publish_action, '') NOT IN ('draft', 'publish')
                BEGIN
                  SELECT RAISE(ABORT, 'scheduled_tasks publish_action must be draft or publish');
                END;
                """
            )

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self.connect() as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return int(cursor.lastrowid)

    def execute_many(self, sql: str, params: list[tuple[Any, ...]]) -> None:
        with self.connect() as conn:
            conn.executemany(sql, params)
            conn.commit()
