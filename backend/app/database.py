import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[1] / ".env")
configured_database_path = os.getenv("GAMEIONX_DATABASE_PATH", "").strip()
DATABASE_PATH = Path(
    configured_database_path
    or Path(__file__).parents[1] / "gameionx.sqlite3"
)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize_database() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                email TEXT,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS saved_games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                prompt TEXT NOT NULL,
                spec_json TEXT NOT NULL,
                runtime_json TEXT NOT NULL DEFAULT '{}',
                qa_summary TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ready',
                game_definition_json TEXT NOT NULL DEFAULT '{}',
                qa_report_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                action TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                original_prompt TEXT NOT NULL,
                status TEXT NOT NULL,
                spec_json TEXT NOT NULL,
                runtime_json TEXT NOT NULL,
                game_definition_json TEXT NOT NULL DEFAULT '{}',
                qa_report_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        user_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(users)")
        }
        if "email" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN email TEXT")
        connection.execute(
            """
            UPDATE users
            SET email = lower(username) || '@legacy.gameionx.invalid'
            WHERE email IS NULL OR email = ''
            """
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique ON users(email COLLATE NOCASE)"
        )
        session_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(sessions)")
        }
        if "expires_at" not in session_columns:
            connection.execute(
                "ALTER TABLE sessions ADD COLUMN expires_at TEXT NOT NULL DEFAULT ''"
            )
        connection.execute(
            """
            UPDATE sessions
            SET expires_at = datetime(created_at, '+7 days')
            WHERE expires_at = ''
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(saved_games)").fetchall()
        }
        if "runtime_json" not in columns:
            connection.execute(
                "ALTER TABLE saved_games ADD COLUMN runtime_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "status" not in columns:
            connection.execute(
                "ALTER TABLE saved_games ADD COLUMN status TEXT NOT NULL DEFAULT 'ready'"
            )
        if "updated_at" not in columns:
            connection.execute(
                "ALTER TABLE saved_games ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''"
            )
            connection.execute("UPDATE saved_games SET updated_at = created_at WHERE updated_at = ''")
        if "game_definition_json" not in columns:
            connection.execute(
                "ALTER TABLE saved_games ADD COLUMN game_definition_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "qa_report_json" not in columns:
            connection.execute(
                "ALTER TABLE saved_games ADD COLUMN qa_report_json TEXT NOT NULL DEFAULT '{}'"
            )


def json_dumps(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)
