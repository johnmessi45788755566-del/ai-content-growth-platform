import sqlite3
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
                  platform TEXT NOT NULL,
                  account_name TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'pending_publish',
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
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
