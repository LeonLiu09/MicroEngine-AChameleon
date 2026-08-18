"""SkillSwap server with profiles, skills, swaps, and local administration.

The server uses only Python's standard library. It creates or migrates the account
and skill databases, serves the user application, and exposes a loopback-only
administrator dashboard with isolated authentication.
"""

from __future__ import annotations

import argparse
from collections.abc import MutableMapping
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import ipaddress
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import threading
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

APP_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = APP_ROOT / "data" / "skillswap.db"
DEFAULT_SKILLS_DB_PATH = APP_ROOT / "data" / "skills.db"
SESSION_COOKIE = "skillswap_session"
ADMIN_SESSION_COOKIE = "skillswap_admin_session"
ADMIN_CSRF_COOKIE = "skillswap_admin_csrf"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
ADMIN_SESSION_TTL_SECONDS = 8 * 60 * 60
PBKDF2_ITERATIONS = 600_000
MAX_JSON_BODY_BYTES = 6 * 1024 * 1024
SKILL_CATEGORIES = {"technology", "creative", "academic", "sports", "lifestyle"}
SKILL_LEVELS = {"complete-beginner", "beginner", "intermediate", "advanced"}
SKILL_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CURRENT_SCHEMA_VERSION = 3
ADMIN_RATE_LIMIT_ATTEMPTS = 5
ADMIN_RATE_LIMIT_WINDOW_SECONDS = 15 * 60
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
DOTENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

SEED_SKILLS = (
    ("photography", "creative", "摄影", "Photography"),
    ("chemistry", "academic", "化学", "Chemistry"),
    ("python", "technology", "Python 编程", "Python"),
    ("guitar", "creative", "吉他", "Guitar"),
    ("badminton", "sports", "羽毛球", "Badminton"),
    ("cooking", "lifestyle", "烹饪", "Cooking"),
    ("english", "academic", "英语", "English"),
    ("drawing", "creative", "绘画", "Drawing"),
    ("fitness", "sports", "健身", "Fitness"),
    ("video-editing", "technology", "视频剪辑", "Video Editing"),
    ("arduino", "technology", "Arduino", "Arduino"),
    ("writing", "academic", "写作", "Writing"),
    ("basketball", "sports", "篮球", "Basketball"),
    ("planting", "lifestyle", "植物养护", "Plant Care"),
    ("ui-design", "creative", "UI 设计", "UI Design"),
    ("product-design", "creative", "产品设计", "Product Design"),
    ("latte-art", "lifestyle", "咖啡拉花", "Latte Art"),
    ("baking", "lifestyle", "烘焙", "Baking"),
    ("public-speaking", "academic", "公众演讲", "Public Speaking"),
    ("tennis", "sports", "网球", "Tennis"),
    ("calligraphy", "creative", "书法", "Calligraphy"),
    ("excel", "technology", "Excel", "Excel"),
    ("japanese", "academic", "日语", "Japanese"),
    ("french", "academic", "法语", "French"),
    ("yoga", "sports", "瑜伽", "Yoga"),
    ("illustration", "creative", "插画", "Illustration"),
    ("personal-finance", "lifestyle", "基础理财", "Personal Finance"),
    ("pottery", "creative", "陶艺", "Pottery"),
)


class ApiProblem(Exception):
    def __init__(self, status: HTTPStatus, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
def _parse_dotenv_value(raw_value: str, *, line_number: int) -> str:
    value = raw_value.strip()
    if not value:
        return ""
    if value[0] not in {"'", '"'}:
        return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()

    quote = value[0]
    escaped = False
    closing_index = None
    for index in range(1, len(value)):
        character = value[index]
        if quote == '"' and character == "\\" and not escaped:
            escaped = True
            continue
        if character == quote and not escaped:
            closing_index = index
            break
        escaped = False
    if closing_index is None:
        raise ValueError(f"Invalid .env line {line_number}: unterminated quote")
    trailing = value[closing_index + 1 :].strip()
    if trailing and not trailing.startswith("#"):
        raise ValueError(
            f"Invalid .env line {line_number}: unexpected text after quoted value"
        )
    quoted_value = value[1:closing_index]
    if quote == "'":
        return quoted_value
    try:
        return json.loads(value[: closing_index + 1])
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid .env line {line_number}: invalid quoted value"
        ) from error


def load_dotenv(
    dotenv_path: Path = APP_ROOT / ".env",
    *,
    environ: MutableMapping[str, str] | None = None,
    override: bool = False,
) -> dict[str, str]:
    """Load SKILLSWAP_* settings without adding a third-party dependency."""
    target = os.environ if environ is None else environ
    if not dotenv_path.is_file():
        return {}
    loaded: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        dotenv_path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not DOTENV_KEY_PATTERN.fullmatch(key):
            raise ValueError(f"Invalid .env line {line_number}: expected KEY=VALUE")
        if not key.startswith("SKILLSWAP_"):
            continue
        value = _parse_dotenv_value(raw_value, line_number=line_number)
        loaded[key] = value
        if override or key not in target:
            target[key] = value
    return loaded


def env_flag(name: str, *, default: bool = False) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    normalized = raw_value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be one of: 1, 0, true, false, yes, no, on, off")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_email(value: str) -> str:
    return value.strip().casefold()


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    password_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), password_salt, PBKDF2_ITERATIONS
    )
    return password_salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, digest_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    _, candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, digest_hex)


def connect_database(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _needs_admin_migration(db_path: Path) -> bool:
    if not db_path.exists() or db_path.stat().st_size == 0:
        return False
    with closing(connect_database(db_path)) as connection:
        user_columns = _column_names(connection, "users")
        session_columns = _column_names(connection, "sessions")
        return bool(user_columns and session_columns) and (
            "role" not in user_columns
            or not {"public_id", "purpose", "csrf_token_hash"}.issubset(
                session_columns
            )
            or not _table_exists(connection, "admin_audit_log")
        )


def backup_database(db_path: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = db_path.with_name(f"{db_path.name}.backup-{timestamp}")
    with closing(connect_database(db_path)) as source, closing(
        sqlite3.connect(backup_path)
    ) as destination:
        source.backup(destination)
    return backup_path


def _audit_event(
    connection: sqlite3.Connection,
    actor: sqlite3.Row | dict[str, Any] | None,
    action: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    actor_id = actor["id"] if actor is not None else None
    actor_email = actor["email"] if actor is not None else "system"
    connection.execute(
        """
        INSERT INTO admin_audit_log
            (actor_user_id, actor_email, action, target_type, target_id,
             details_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor_id,
            actor_email,
            action,
            target_type,
            target_id,
            json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
            utc_now_iso(),
        ),
    )


def validate_email(value: str) -> bool:
    normalized = normalize_email(value)
    return 3 <= len(normalized) <= 254 and EMAIL_PATTERN.fullmatch(normalized) is not None


def validate_password_for_role(password: str, role: str) -> bool:
    minimum = 12 if role == "superadmin" else 8
    return minimum <= len(password) <= 128


def is_loopback_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if address.is_loopback:
        return True
    return bool(
        isinstance(address, ipaddress.IPv6Address)
        and address.ipv4_mapped
        and address.ipv4_mapped.is_loopback
    )


def initialize_database(
    db_path: Path,
    *,
    demo_email: str = "daniel@example.com",
    demo_password: str = "SkillSwap123!",
    admin_email: str | None = None,
    admin_password: str | None = None,
    admin_name: str = "超级管理员",
    admin_sync: bool = False,
) -> Path | None:
    """Initialize or migrate the account database without discarding user data."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    needs_migration = _needs_admin_migration(db_path)
    backup_path = backup_database(db_path) if needs_migration else None
    with closing(connect_database(db_path)) as connection, connection:
        connection.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
                created_at TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user'
                    CHECK (role IN ('user', 'superadmin'))
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                public_id TEXT,
                purpose TEXT NOT NULL DEFAULT 'user'
                    CHECK (purpose IN ('user', 'admin')),
                csrf_token_hash TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS login_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_token_hash TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id INTEGER PRIMARY KEY,
                age INTEGER NOT NULL DEFAULT 18 CHECK (age BETWEEN 13 AND 99),
                country_id TEXT NOT NULL DEFAULT '',
                city_id TEXT NOT NULL DEFAULT '',
                languages_json TEXT NOT NULL DEFAULT '[]',
                bio_zh TEXT NOT NULL DEFAULT '',
                bio_en TEXT NOT NULL DEFAULT '',
                avatar_data_url TEXT NOT NULL DEFAULT '',
                profile_visibility TEXT NOT NULL DEFAULT 'community'
                    CHECK (profile_visibility IN ('community', 'private')),
                onboarding_completed INTEGER NOT NULL DEFAULT 0
                    CHECK (onboarding_completed IN (0, 1)),
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS user_skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                skill_id TEXT NOT NULL,
                direction TEXT NOT NULL CHECK (direction IN ('offer', 'want')),
                level TEXT NOT NULL CHECK (
                    level IN ('complete-beginner', 'beginner', 'intermediate', 'advanced')
                ),
                description_zh TEXT NOT NULL DEFAULT '',
                description_en TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (user_id, skill_id, direction),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS swap_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requester_id INTEGER NOT NULL,
                target_user_id INTEGER NOT NULL,
                offered_skill_id TEXT NOT NULL,
                requested_skill_id TEXT NOT NULL,
                meeting_policy TEXT NOT NULL DEFAULT 'flexible'
                    CHECK (meeting_policy IN ('flexible', 'public-place')),
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','accepted','rejected','cancelled','completed')),
                created_at TEXT NOT NULL,
                accepted_at TEXT,
                rejected_at TEXT,
                cancelled_at TEXT,
                requester_completed_at TEXT,
                target_completed_at TEXT,
                completed_at TEXT,
                CHECK (requester_id != target_user_id),
                FOREIGN KEY (requester_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (target_user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
            CREATE INDEX IF NOT EXISTS idx_login_events_day
                ON login_events(created_at, user_id);
            CREATE INDEX IF NOT EXISTS idx_user_skills_user ON user_skills(user_id);
            CREATE INDEX IF NOT EXISTS idx_user_skills_lookup
                ON user_skills(skill_id, direction);
            CREATE INDEX IF NOT EXISTS idx_swap_requests_requester
                ON swap_requests(requester_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_swap_requests_target
                ON swap_requests(target_user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_swap_requests_status
                ON swap_requests(status, completed_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_swap_requests_active_unique
                ON swap_requests(requester_id,target_user_id,offered_skill_id,requested_skill_id)
                WHERE status IN ('pending','accepted');

            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admin_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_user_id INTEGER,
                actor_email TEXT NOT NULL,
                action TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
            CREATE INDEX IF NOT EXISTS idx_audit_created_at
                ON admin_audit_log(created_at DESC);
            """
        )
        user_columns = _column_names(connection, "users")
        if "role" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user' "
                "CHECK (role IN ('user', 'superadmin'))"
            )
        if "is_admin" in user_columns:
            connection.execute(
                "UPDATE users SET role = 'superadmin' WHERE is_admin = 1"
            )
        session_columns = _column_names(connection, "sessions")
        if "public_id" not in session_columns:
            connection.execute("ALTER TABLE sessions ADD COLUMN public_id TEXT")
        if "purpose" not in session_columns:
            connection.execute(
                "ALTER TABLE sessions ADD COLUMN purpose TEXT NOT NULL DEFAULT 'user' "
                "CHECK (purpose IN ('user', 'admin'))"
            )
        if "csrf_token_hash" not in session_columns:
            connection.execute("ALTER TABLE sessions ADD COLUMN csrf_token_hash TEXT")
        connection.execute(
            """INSERT OR IGNORE INTO login_events(session_token_hash,user_id,created_at)
               SELECT token_hash,user_id,created_at FROM sessions
               WHERE purpose = 'user'"""
        )
        missing_public_ids = connection.execute(
            "SELECT token_hash FROM sessions WHERE public_id IS NULL OR public_id = ''"
        ).fetchall()
        for row in missing_public_ids:
            connection.execute(
                "UPDATE sessions SET public_id = ? WHERE token_hash = ?",
                (uuid.uuid4().hex, row["token_hash"]),
            )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_public_id "
            "ON sessions(public_id)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (1, utc_now_iso()),
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (CURRENT_SCHEMA_VERSION, utc_now_iso()),
        )
        existing = connection.execute(
            "SELECT id FROM users WHERE email = ?", (normalize_email(demo_email),)
        ).fetchone()
        if existing is None:
            salt, password_digest = hash_password(demo_password)
            connection.execute(
                """
                INSERT INTO users
                    (email, password_salt, password_hash, display_name, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    normalize_email(demo_email),
                    salt,
                    password_digest,
                    "Daniel Liu",
                    utc_now_iso(),
                ),
            )
        demo_user_id = connection.execute(
            "SELECT id FROM users WHERE email = ?", (normalize_email(demo_email),)
        ).fetchone()["id"]
        connection.execute(
            """INSERT INTO user_profiles
               (user_id,age,country_id,city_id,languages_json,bio_zh,bio_en,
                profile_visibility,onboarding_completed,updated_at)
               VALUES (?,19,'cn','tianjin','["zh", "en"]',?,?,'community',1,?)
               ON CONFLICT(user_id) DO NOTHING""",
            (
                demo_user_id,
                "化学爱好者、健身常客，也一直在尝试学习新东西。",
                "Chemistry enthusiast, gym regular, and always trying new things.",
                utc_now_iso(),
            ),
        )
        has_admin_email = bool(admin_email and admin_email.strip())
        has_admin_password = bool(admin_password)
        if has_admin_email != has_admin_password:
            raise ValueError(
                "SKILLSWAP_ADMIN_EMAIL and SKILLSWAP_ADMIN_PASSWORD must be set together"
            )
        if has_admin_email and has_admin_password:
            normalized_admin_email = normalize_email(admin_email or "")
            clean_admin_name = admin_name.strip()
            if not validate_email(normalized_admin_email):
                raise ValueError("SKILLSWAP_ADMIN_EMAIL is invalid")
            if not clean_admin_name or len(clean_admin_name) > 80:
                raise ValueError("SKILLSWAP_ADMIN_NAME must be 1-80 characters")
            if not validate_password_for_role(admin_password or "", "superadmin"):
                raise ValueError(
                    "SKILLSWAP_ADMIN_PASSWORD must be 12-128 characters"
                )
            existing_admin = connection.execute(
                """
                SELECT id, email, role, display_name, password_salt, password_hash
                FROM users WHERE email = ?
                """,
                (normalized_admin_email,),
            ).fetchone()
            if existing_admin is not None and existing_admin["role"] != "superadmin":
                raise ValueError(
                    "Admin email already belongs to a regular user; refusing privilege escalation"
                )
            if existing_admin is None and admin_sync:
                configured_admins = connection.execute(
                    """
                    SELECT id, email, role, display_name, password_salt, password_hash
                    FROM users WHERE role = 'superadmin' ORDER BY id
                    """
                ).fetchall()
                if len(configured_admins) == 1:
                    existing_admin = configured_admins[0]
                elif len(configured_admins) > 1:
                    raise ValueError(
                        "SKILLSWAP_ADMIN_SYNC cannot choose between multiple "
                        "superadmins; use an existing admin email or the admin dashboard"
                    )
            if existing_admin is None:
                salt, password_digest = hash_password(admin_password or "")
                cursor = connection.execute(
                    """
                    INSERT INTO users
                        (email, password_salt, password_hash, display_name,
                         is_active, created_at, role)
                    VALUES (?, ?, ?, ?, 1, ?, 'superadmin')
                    """,
                    (
                        normalized_admin_email,
                        salt,
                        password_digest,
                        clean_admin_name,
                        utc_now_iso(),
                    ),
                )
                _audit_event(
                    connection,
                    None,
                    "admin.bootstrap",
                    "user",
                    str(cursor.lastrowid),
                    {"email": normalized_admin_email},
                )
            elif admin_sync:
                changed_fields: list[str] = []
                updates: dict[str, str] = {}
                if existing_admin["email"] != normalized_admin_email:
                    updates["email"] = normalized_admin_email
                    changed_fields.append("email")
                if existing_admin["display_name"] != clean_admin_name:
                    updates["display_name"] = clean_admin_name
                    changed_fields.append("displayName")
                if not verify_password(
                    admin_password or "",
                    existing_admin["password_salt"],
                    existing_admin["password_hash"],
                ):
                    salt, password_digest = hash_password(admin_password or "")
                    updates["password_salt"] = salt
                    updates["password_hash"] = password_digest
                    changed_fields.append("password")
                if updates:
                    assignments = ", ".join(f"{column} = ?" for column in updates)
                    connection.execute(
                        f"UPDATE users SET {assignments} WHERE id = ?",
                        (*updates.values(), existing_admin["id"]),
                    )
                    _audit_event(
                        connection,
                        None,
                        "admin.config_sync",
                        "user",
                        str(existing_admin["id"]),
                        {
                            "changedFields": changed_fields,
                            "email": normalized_admin_email,
                        },
                    )
    return backup_path


def initialize_skill_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect_database(db_path)) as connection, connection:
        connection.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS skills (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL CHECK (
                    category IN ('technology','creative','academic','sports','lifestyle')
                ),
                name_zh TEXT NOT NULL COLLATE NOCASE UNIQUE,
                name_en TEXT NOT NULL COLLATE NOCASE UNIQUE,
                is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category);
            CREATE INDEX IF NOT EXISTS idx_skills_active ON skills(is_active);
            """
        )
        now = utc_now_iso()
        connection.executemany(
            """INSERT INTO skills
               (id,category,name_zh,name_en,is_active,created_at,updated_at)
               VALUES (?,?,?,?,1,?,?) ON CONFLICT(id) DO NOTHING""",
            [(sid, category, zh, en, now, now) for sid, category, zh, en in SEED_SKILLS],
        )


def seed_admin_user_skills(account_db_path: Path) -> None:
    offered = (
        ("chemistry", "advanced", "可以辅导 A-Level 化学与热力学。", "A-Level chemistry and thermodynamics."),
        ("badminton", "intermediate", "一起练习步法和基础对打。", "Footwork and steady rallies."),
        ("fitness", "advanced", "制定安全、简单的力量训练计划。", "A safe, simple strength routine."),
    )
    wanted = (
        ("photography", "beginner", "想学会手动曝光和人像构图。", "Manual exposure and portrait composition."),
        ("python", "complete-beginner", "想用 Python 做第一个小项目。", "Build a first small Python project."),
        ("cooking", "beginner", "想学几道适合宿舍的快手菜。", "Quick dorm-friendly recipes."),
    )
    with closing(connect_database(account_db_path)) as connection, connection:
        user = connection.execute("SELECT id FROM users WHERE email = 'daniel@example.com'").fetchone()
        if user is None:
            return
        now = utc_now_iso()
        rows = [
            (user["id"], sid, direction, level, zh, en, now, now)
            for direction, values in (("offer", offered), ("want", wanted))
            for sid, level, zh, en in values
        ]
        connection.executemany(
            """INSERT INTO user_skills
               (user_id,skill_id,direction,level,description_zh,description_en,
                created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id,skill_id,direction) DO NOTHING""",
            rows,
        )


def authenticate_user(db_path: Path, email: str, password: str) -> sqlite3.Row | None:
    if not email or not password or len(email) > 254 or len(password) > 1024:
        return None
    with closing(connect_database(db_path)) as connection:
        user = connection.execute(
            """
            SELECT id, email, password_salt, password_hash, display_name, role
            FROM users
            WHERE email = ? AND is_active = 1
            """,
            (normalize_email(email),),
        ).fetchone()
    if user is None:
        hash_password(password, bytes(16))
        return None
    return user if verify_password(password, user["password_salt"], user["password_hash"]) else None


def register_user(db_path: Path, email: str, password: str) -> sqlite3.Row:
    normalized = normalize_email(email)
    parts = normalized.split("@", 1)
    if len(normalized) > 254 or len(parts) != 2 or not parts[0] or "." not in parts[1]:
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_email", "Enter a valid email address.")
    if len(password) < 8 or len(password) > 1024:
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_password", "Password must contain at least 8 characters.")
    salt, digest = hash_password(password)
    now = utc_now_iso()
    try:
        with closing(connect_database(db_path)) as connection, connection:
            cursor = connection.execute(
                """INSERT INTO users
                   (email,password_salt,password_hash,display_name,created_at,role)
                   VALUES (?,?,?,?,?,'user')""",
                (normalized, salt, digest, parts[0][:80], now),
            )
            user_id = cursor.lastrowid
            connection.execute(
                "INSERT INTO user_profiles (user_id,languages_json,updated_at) VALUES (?,?,?)",
                (user_id, json.dumps(["zh"]), now),
            )
            return connection.execute(
                "SELECT id,email,display_name,role FROM users WHERE id = ?", (user_id,)
            ).fetchone()
    except sqlite3.IntegrityError as error:
        raise ApiProblem(HTTPStatus.CONFLICT, "email_exists", "An account with this email already exists.") from error


def public_user(user: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(user["id"]),
        "email": user["email"],
        "displayName": user["display_name"],
        "role": user["role"],
        "isAdmin": user["role"] == "superadmin",
    }


def create_session(db_path: Path, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    now = int(time.time())
    with closing(connect_database(db_path)) as connection, connection:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        connection.execute(
            """
            INSERT INTO sessions
                (token_hash, user_id, created_at, expires_at, public_id, purpose)
            VALUES (?, ?, ?, ?, ?, 'user')
            """,
            (token_hash, user_id, now, now + SESSION_TTL_SECONDS, uuid.uuid4().hex),
        )
        connection.execute(
            "INSERT INTO login_events (session_token_hash,user_id,created_at) VALUES (?,?,?)",
            (token_hash, user_id, now),
        )
    return token


def create_admin_session(db_path: Path, user_id: int) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    now = int(time.time())
    with closing(connect_database(db_path)) as connection, connection:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        connection.execute(
            """
            INSERT INTO sessions
                (token_hash, user_id, created_at, expires_at, public_id,
                 purpose, csrf_token_hash)
            VALUES (?, ?, ?, ?, ?, 'admin', ?)
            """,
            (
                hashlib.sha256(token.encode("ascii")).hexdigest(),
                user_id,
                now,
                now + ADMIN_SESSION_TTL_SECONDS,
                uuid.uuid4().hex,
                hashlib.sha256(csrf_token.encode("ascii")).hexdigest(),
            ),
        )
    return token, csrf_token


def user_for_session(db_path: Path, token: str) -> sqlite3.Row | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = int(time.time())
    with closing(connect_database(db_path)) as connection, connection:
        user = connection.execute(
            """
            SELECT users.id, users.email, users.display_name, users.role
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ?
              AND sessions.expires_at > ?
              AND sessions.purpose = 'user'
              AND users.is_active = 1
            """,
            (token_hash, now),
        ).fetchone()
    return user


def admin_for_session(db_path: Path, token: str) -> sqlite3.Row | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = int(time.time())
    with closing(connect_database(db_path)) as connection:
        return connection.execute(
            """
            SELECT users.id, users.email, users.display_name, users.role,
                   sessions.public_id, sessions.csrf_token_hash,
                   sessions.created_at, sessions.expires_at
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ?
              AND sessions.expires_at > ?
              AND sessions.purpose = 'admin'
              AND users.role = 'superadmin'
              AND users.is_active = 1
            """,
            (token_hash, now),
        ).fetchone()


def delete_session(db_path: Path, token: str) -> None:
    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with closing(connect_database(db_path)) as connection, connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def _json_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bounded_text(value: Any, field: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_request", f"{field} must be text.")
    clean = value.strip()
    if required and not clean:
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_request", f"{field} is required.")
    if len(clean) > maximum:
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_request", f"{field} is too long.")
    return clean


def serialize_skill(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "category": row["category"],
        "names": {"zh": row["name_zh"], "en": row["name_en"]},
        "isActive": bool(row["is_active"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def validate_skill_payload(payload: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if creating:
        skill_id = _bounded_text(payload.get("id"), "id", 64, required=True)
        if not SKILL_ID_PATTERN.fullmatch(skill_id):
            raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_skill_id", "Skill id must be a lowercase hyphenated slug.")
        result["id"] = skill_id
    category = _bounded_text(payload.get("category"), "category", 32, required=True)
    if category not in SKILL_CATEGORIES:
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_category", "Unknown skill category.")
    names = _json_object(payload.get("names"))
    result.update(
        category=category,
        name_zh=_bounded_text(names.get("zh"), "names.zh", 80, required=True),
        name_en=_bounded_text(names.get("en"), "names.en", 80, required=True),
    )
    if "isActive" in payload:
        if not isinstance(payload["isActive"], bool):
            raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_request", "isActive must be boolean.")
        result["is_active"] = int(payload["isActive"])
    return result


def skill_by_id(db_path: Path, skill_id: str) -> sqlite3.Row | None:
    with closing(connect_database(db_path)) as connection:
        return connection.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()


def list_skills(
    db_path: Path, *, query: str = "", category: str = "", include_inactive: bool = False
) -> list[dict[str, Any]]:
    sql, params = "SELECT * FROM skills WHERE 1=1", []
    if not include_inactive:
        sql += " AND is_active = 1"
    if category:
        sql += " AND category = ?"
        params.append(category)
    with closing(connect_database(db_path)) as connection:
        rows = connection.execute(sql, params).fetchall()
    needle = query.strip().casefold()

    def rank(row: sqlite3.Row) -> tuple[int, str, str]:
        values = [row["id"].casefold(), row["name_zh"].casefold(), row["name_en"].casefold()]
        score = 3 if not needle else 0 if needle in values else 1 if any(v.startswith(needle) for v in values) else 2 if any(needle in v for v in values) else 99
        return score, row["name_en"].casefold(), row["id"]

    return [serialize_skill(row) for row in sorted(rows, key=rank) if rank(row)[0] < 99]


def create_skill(db_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    data, now = validate_skill_payload(payload, creating=True), utc_now_iso()
    try:
        with closing(connect_database(db_path)) as connection, connection:
            connection.execute(
                """INSERT INTO skills
                   (id,category,name_zh,name_en,is_active,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (data["id"], data["category"], data["name_zh"], data["name_en"], data.get("is_active", 1), now, now),
            )
            row = connection.execute("SELECT * FROM skills WHERE id = ?", (data["id"],)).fetchone()
    except sqlite3.IntegrityError as error:
        raise ApiProblem(HTTPStatus.CONFLICT, "skill_exists", "Skill id or name already exists.") from error
    return serialize_skill(row)


def update_skill(db_path: Path, skill_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if "id" in payload and payload["id"] != skill_id:
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "immutable_skill_id", "Skill id cannot be changed.")
    data, existing = validate_skill_payload(payload, creating=False), skill_by_id(db_path, skill_id)
    if existing is None:
        raise ApiProblem(HTTPStatus.NOT_FOUND, "skill_not_found", "Skill not found.")
    try:
        with closing(connect_database(db_path)) as connection, connection:
            connection.execute(
                """UPDATE skills SET category=?,name_zh=?,name_en=?,is_active=?,updated_at=?
                   WHERE id=?""",
                (data["category"], data["name_zh"], data["name_en"], data.get("is_active", existing["is_active"]), utc_now_iso(), skill_id),
            )
            row = connection.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
    except sqlite3.IntegrityError as error:
        raise ApiProblem(HTTPStatus.CONFLICT, "skill_exists", "Skill name already exists.") from error
    return serialize_skill(row)


def deactivate_skill(db_path: Path, skill_id: str) -> None:
    with closing(connect_database(db_path)) as connection, connection:
        cursor = connection.execute(
            "UPDATE skills SET is_active=0,updated_at=? WHERE id=?", (utc_now_iso(), skill_id)
        )
        if not cursor.rowcount:
            raise ApiProblem(HTTPStatus.NOT_FOUND, "skill_not_found", "Skill not found.")


def _safe_json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []


def get_profile(db_path: Path, user_id: int) -> dict[str, Any]:
    with closing(connect_database(db_path)) as connection:
        row = connection.execute(
            """SELECT u.id,u.email,u.display_name,u.role,u.created_at,
                      p.age,p.country_id,p.city_id,p.languages_json,p.bio_zh,p.bio_en,
                      p.avatar_data_url,p.profile_visibility,p.onboarding_completed
               FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id WHERE u.id=?""",
            (user_id,),
        ).fetchone()
    if row is None:
        raise ApiProblem(HTTPStatus.NOT_FOUND, "user_not_found", "User not found.")
    return {
        "id": str(row["id"]), "email": row["email"], "name": row["display_name"],
        "isAdmin": row["role"] == "superadmin", "age": row["age"] or 18,
        "countryId": row["country_id"] or "", "cityId": row["city_id"] or "",
        "languages": _safe_json_list(row["languages_json"] or "[]"),
        "bio": {"zh": row["bio_zh"] or "", "en": row["bio_en"] or ""},
        "avatarDataUrl": row["avatar_data_url"] or "",
        "profileVisibility": row["profile_visibility"] or "community",
        "onboardingCompleted": bool(row["onboarding_completed"]),
        "memberSince": row["created_at"],
    }


def update_profile(db_path: Path, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    name = _bounded_text(payload.get("name"), "name", 80, required=True)
    age = payload.get("age")
    if not isinstance(age, int) or isinstance(age, bool) or not 13 <= age <= 99:
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_age", "Age must be between 13 and 99.")
    country = _bounded_text(payload.get("countryId", ""), "countryId", 32, required=True)
    city = _bounded_text(payload.get("cityId", ""), "cityId", 64, required=True)
    languages = payload.get("languages")
    if not isinstance(languages, list) or not languages or any(not isinstance(v, str) or not v or len(v) > 12 for v in languages):
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_languages", "Choose at least one language.")
    languages = list(dict.fromkeys(languages))
    bio = _json_object(payload.get("bio"))
    bio_zh = _bounded_text(bio.get("zh", ""), "bio.zh", 1000)
    bio_en = _bounded_text(bio.get("en", ""), "bio.en", 1000)
    avatar = _bounded_text(payload.get("avatarDataUrl", ""), "avatarDataUrl", 5_500_000)
    visibility = payload.get("profileVisibility", "community")
    if visibility not in {"community", "private"}:
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_visibility", "Unknown profile visibility.")
    completed = payload.get("onboardingCompleted", False)
    if not isinstance(completed, bool):
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_request", "onboardingCompleted must be boolean.")
    now = utc_now_iso()
    with closing(connect_database(db_path)) as connection, connection:
        connection.execute("UPDATE users SET display_name=? WHERE id=?", (name, user_id))
        connection.execute(
            """INSERT INTO user_profiles
               (user_id,age,country_id,city_id,languages_json,bio_zh,bio_en,
                avatar_data_url,profile_visibility,onboarding_completed,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET age=excluded.age,
                country_id=excluded.country_id,city_id=excluded.city_id,
                languages_json=excluded.languages_json,bio_zh=excluded.bio_zh,
                bio_en=excluded.bio_en,avatar_data_url=excluded.avatar_data_url,
                profile_visibility=excluded.profile_visibility,
                onboarding_completed=excluded.onboarding_completed,updated_at=excluded.updated_at""",
            (user_id, age, country, city, json.dumps(languages), bio_zh, bio_en, avatar, visibility, int(completed), now),
        )
    return get_profile(db_path, user_id)


def get_user_skills(db_path: Path, user_id: int) -> dict[str, Any]:
    with closing(connect_database(db_path)) as connection:
        rows = connection.execute("SELECT * FROM user_skills WHERE user_id=? ORDER BY id", (user_id,)).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {"offer": [], "want": []}
    for row in rows:
        grouped[row["direction"]].append({
            "id": str(row["id"]), "skillId": row["skill_id"], "level": row["level"],
            "desc": {"zh": row["description_zh"], "en": row["description_en"]},
        })
    return {"skillsOffered": grouped["offer"], "skillsWanted": grouped["want"]}


def _validate_user_skill_list(value: Any, direction: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "skills_required", "At least one offered and one wanted skill are required.")
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict):
            raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_request", "Invalid skill entry.")
        skill_id = _bounded_text(item.get("skillId"), "skillId", 64, required=True)
        level = item.get("level")
        if level not in SKILL_LEVELS or (direction == "want" and level == "advanced"):
            raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_level", "Unknown skill level.")
        if skill_id in seen:
            raise ApiProblem(HTTPStatus.CONFLICT, "duplicate_user_skill", "Each skill can appear once per list.")
        seen.add(skill_id)
        desc = _json_object(item.get("desc"))
        result.append({
            "skill_id": skill_id, "level": level,
            "description_zh": _bounded_text(desc.get("zh", ""), "desc.zh", 500),
            "description_en": _bounded_text(desc.get("en", ""), "desc.en", 500),
        })
    return result


def replace_user_skills(account_db: Path, skills_db: Path, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    offered = _validate_user_skill_list(payload.get("skillsOffered"), "offer")
    wanted = _validate_user_skill_list(payload.get("skillsWanted"), "want")
    ids = {item["skill_id"] for item in offered + wanted}
    marks = ",".join("?" for _ in ids)
    with closing(connect_database(skills_db)) as connection:
        active = {row["id"] for row in connection.execute(f"SELECT id FROM skills WHERE is_active=1 AND id IN ({marks})", tuple(ids))}
    if active != ids:
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_skill", "One or more skills are unavailable.")
    now = utc_now_iso()
    with closing(connect_database(account_db)) as connection, connection:
        connection.execute("DELETE FROM user_skills WHERE user_id=?", (user_id,))
        for direction, entries in (("offer", offered), ("want", wanted)):
            connection.executemany(
                """INSERT INTO user_skills
                   (user_id,skill_id,direction,level,description_zh,description_en,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [(user_id, item["skill_id"], direction, item["level"], item["description_zh"], item["description_en"], now, now) for item in entries],
            )
    return get_user_skills(account_db, user_id)


def search_catalog_and_users(account_db: Path, skills_db: Path, current_user_id: int, params: dict[str, list[str]]) -> dict[str, Any]:
    first = lambda name, default="": params.get(name, [default])[0].strip()
    query, level, country, city, language, sort = first("q")[:100], first("level"), first("country"), first("city"), first("lang"), first("sort", "newest")
    level = level if level in SKILL_LEVELS else ""
    language = language if language in {"zh", "en"} else ""
    try:
        limit, offset = min(50, max(1, int(first("limit", "20")))), max(0, int(first("offset", "0")))
    except ValueError as error:
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_pagination", "Invalid pagination values.") from error
    skills = list_skills(skills_db, query=query)
    matching_ids = {skill["id"] for skill in skills}
    active_ids = {skill["id"] for skill in list_skills(skills_db)}
    with closing(connect_database(account_db)) as connection:
        profiles = connection.execute(
            """SELECT u.id,u.display_name,u.created_at,p.age,p.country_id,p.city_id,
                      p.languages_json,p.bio_zh,p.bio_en,p.avatar_data_url
               FROM users u JOIN user_profiles p ON p.user_id=u.id
               WHERE u.is_active=1 AND u.role='user' AND p.onboarding_completed=1
                 AND p.profile_visibility='community' AND u.id != ?""", (current_user_id,)
        ).fetchall()
        rows = connection.execute("SELECT * FROM user_skills ORDER BY id").fetchall()
    by_user: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for row in rows:
        if row["skill_id"] not in active_ids:
            continue
        item = {"id": str(row["id"]), "skillId": row["skill_id"], "level": row["level"], "desc": {"zh": row["description_zh"], "en": row["description_en"]}}
        by_user.setdefault(row["user_id"], {"offer": [], "want": []})[row["direction"]].append(item)
    needle, users = query.casefold(), []
    for row in profiles:
        entries = by_user.get(row["id"], {"offer": [], "want": []})
        offered, languages = entries["offer"], _safe_json_list(row["languages_json"])
        profile_text = " ".join([row["display_name"], row["bio_zh"], row["bio_en"]]).casefold()
        if query and not any(item["skillId"] in matching_ids for item in offered) and needle not in profile_text:
            continue
        if level and not any(item["level"] == level for item in offered):
            continue
        if country and row["country_id"] != country or city and row["city_id"] != city or language and language not in languages:
            continue
        users.append({
            "id": str(row["id"]), "name": row["display_name"], "age": row["age"],
            "countryId": row["country_id"], "cityId": row["city_id"], "languages": languages,
            "bio": {"zh": row["bio_zh"], "en": row["bio_en"]}, "avatarDataUrl": row["avatar_data_url"],
            "skillsOffered": offered, "skillsWanted": entries["want"], "likes": 0,
            "publishedAt": row["created_at"], "memberSince": row["created_at"],
            "availability": [], "meetingModes": ["public-place"], "reliability": 100, "interests": [],
        })
    users.sort(key=(lambda user: (user["likes"], user["publishedAt"])) if sort == "liked" else (lambda user: user["publishedAt"]), reverse=True)
    total, page = len(users), users[offset:offset + limit]
    return {"query": query, "skills": skills[:20], "users": page, "pagination": {"limit": limit, "offset": offset, "totalUsers": total, "hasMore": offset + len(page) < total}}


def _parse_user_id(value: Any, field: str = "userId") -> int:
    try:
        user_id = int(value)
    except (TypeError, ValueError) as error:
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_request", f"{field} must be a positive integer.") from error
    if user_id <= 0:
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_request", f"{field} must be a positive integer.")
    return user_id


def _request_user_card(account_db: Path, skills_db: Path, user_id: int) -> dict[str, Any] | None:
    with closing(connect_database(account_db)) as connection:
        row = connection.execute(
            """SELECT u.id,u.display_name,u.created_at,p.age,p.country_id,p.city_id,
                      p.languages_json,p.bio_zh,p.bio_en,p.avatar_data_url
               FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id
               WHERE u.id=? AND u.is_active=1 AND u.role='user'""",
            (user_id,),
        ).fetchone()
        skill_rows = connection.execute(
            "SELECT * FROM user_skills WHERE user_id=? ORDER BY id", (user_id,)
        ).fetchall()
    if row is None:
        return None
    active_ids = {skill["id"] for skill in list_skills(skills_db)}
    grouped: dict[str, list[dict[str, Any]]] = {"offer": [], "want": []}
    for skill in skill_rows:
        if skill["skill_id"] not in active_ids:
            continue
        grouped[skill["direction"]].append({
            "id": str(skill["id"]), "skillId": skill["skill_id"], "level": skill["level"],
            "desc": {"zh": skill["description_zh"], "en": skill["description_en"]},
        })
    return {
        "id": str(row["id"]), "name": row["display_name"], "age": row["age"] or 18,
        "countryId": row["country_id"] or "", "cityId": row["city_id"] or "",
        "languages": _safe_json_list(row["languages_json"] or "[]"),
        "bio": {"zh": row["bio_zh"] or "", "en": row["bio_en"] or ""},
        "avatarDataUrl": row["avatar_data_url"] or "", "skillsOffered": grouped["offer"],
        "skillsWanted": grouped["want"], "likes": 0, "publishedAt": row["created_at"],
        "memberSince": row["created_at"], "availability": [],
        "meetingModes": ["public-place"], "reliability": 100, "interests": [],
    }


def _serialize_swap_request(
    account_db: Path, skills_db: Path, row: sqlite3.Row, current_user_id: int
) -> dict[str, Any]:
    counterpart_id = row["target_user_id"] if row["requester_id"] == current_user_id else row["requester_id"]
    counterpart = _request_user_card(account_db, skills_db, counterpart_id)
    return {
        "id": str(row["id"]), "direction": "sent" if row["requester_id"] == current_user_id else "received",
        "requesterId": str(row["requester_id"]), "targetUserId": str(row["target_user_id"]),
        "offeredSkillId": row["offered_skill_id"], "requestedSkillId": row["requested_skill_id"],
        "meetingPolicy": row["meeting_policy"], "status": row["status"],
        "createdAt": row["created_at"], "acceptedAt": row["accepted_at"],
        "rejectedAt": row["rejected_at"], "cancelledAt": row["cancelled_at"],
        "requesterCompletedAt": row["requester_completed_at"],
        "targetCompletedAt": row["target_completed_at"], "completedAt": row["completed_at"],
        "counterpart": counterpart,
    }


def create_swap_request(
    account_db: Path, skills_db: Path, requester_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    target_id = _parse_user_id(payload.get("targetUserId"), "targetUserId")
    if target_id == requester_id:
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "self_request", "You cannot request a swap with yourself.")
    offered_id = _bounded_text(payload.get("offeredSkillId"), "offeredSkillId", 64, required=True)
    requested_id = _bounded_text(payload.get("requestedSkillId"), "requestedSkillId", 64, required=True)
    meeting_policy = payload.get("meetingPolicy", "flexible")
    if meeting_policy not in {"flexible", "public-place"}:
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_meeting_policy", "Unknown meeting policy.")
    active_ids = {skill["id"] for skill in list_skills(skills_db)}
    if offered_id not in active_ids or requested_id not in active_ids:
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_skill", "One or more skills are unavailable.")
    with closing(connect_database(account_db)) as connection:
        requester = connection.execute(
            """SELECT u.id FROM users u JOIN user_profiles p ON p.user_id=u.id
               WHERE u.id=? AND u.is_active=1 AND u.role='user' AND p.onboarding_completed=1
                 AND p.profile_visibility='community'""",
            (requester_id,),
        ).fetchone()
        target = connection.execute(
            """SELECT u.id FROM users u JOIN user_profiles p ON p.user_id=u.id
               WHERE u.id=? AND u.is_active=1 AND u.role='user' AND p.onboarding_completed=1
                 AND p.profile_visibility='community'""",
            (target_id,),
        ).fetchone()
        requester_offer = connection.execute(
            "SELECT 1 FROM user_skills WHERE user_id=? AND skill_id=? AND direction='offer'",
            (requester_id, offered_id),
        ).fetchone()
        target_offer = connection.execute(
            "SELECT 1 FROM user_skills WHERE user_id=? AND skill_id=? AND direction='offer'",
            (target_id, requested_id),
        ).fetchone()
    if requester is None:
        raise ApiProblem(HTTPStatus.FORBIDDEN, "profile_unavailable", "Complete and publish your profile before requesting a swap.")
    if target is None:
        raise ApiProblem(HTTPStatus.NOT_FOUND, "user_not_found", "The requested user is unavailable.")
    if requester_offer is None or target_offer is None:
        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_skill_pair", "The selected skills do not belong to the participants.")
    now = utc_now_iso()
    try:
        with closing(connect_database(account_db)) as connection, connection:
            duplicate = connection.execute(
                """SELECT 1 FROM swap_requests
                   WHERE status IN ('pending','accepted') AND (
                     (requester_id=? AND target_user_id=? AND offered_skill_id=? AND requested_skill_id=?) OR
                     (requester_id=? AND target_user_id=? AND offered_skill_id=? AND requested_skill_id=?)
                   )""",
                (requester_id, target_id, offered_id, requested_id,
                 target_id, requester_id, requested_id, offered_id),
            ).fetchone()
            if duplicate is not None:
                raise ApiProblem(HTTPStatus.CONFLICT, "request_exists", "An active request with these skills already exists.")
            cursor = connection.execute(
                """INSERT INTO swap_requests
                   (requester_id,target_user_id,offered_skill_id,requested_skill_id,
                    meeting_policy,status,created_at)
                   VALUES (?,?,?,?,?,'pending',?)""",
                (requester_id, target_id, offered_id, requested_id, meeting_policy, now),
            )
            row = connection.execute("SELECT * FROM swap_requests WHERE id=?", (cursor.lastrowid,)).fetchone()
    except sqlite3.IntegrityError as error:
        raise ApiProblem(HTTPStatus.CONFLICT, "request_exists", "An active request with these skills already exists.") from error
    return _serialize_swap_request(account_db, skills_db, row, requester_id)


def list_swap_requests(account_db: Path, skills_db: Path, user_id: int) -> dict[str, Any]:
    with closing(connect_database(account_db)) as connection:
        rows = connection.execute(
            """SELECT * FROM swap_requests
               WHERE requester_id=? OR target_user_id=?
               ORDER BY created_at DESC,id DESC""",
            (user_id, user_id),
        ).fetchall()
    items = [_serialize_swap_request(account_db, skills_db, row, user_id) for row in rows]
    return {
        "received": [item for item in items if item["direction"] == "received"],
        "sent": [item for item in items if item["direction"] == "sent"],
    }


def update_swap_request(
    account_db: Path, skills_db: Path, request_id: int, actor_id: int, action: str
) -> dict[str, Any]:
    now = utc_now_iso()
    with closing(connect_database(account_db)) as connection, connection:
        row = connection.execute("SELECT * FROM swap_requests WHERE id=?", (request_id,)).fetchone()
        if row is None or actor_id not in {row["requester_id"], row["target_user_id"]}:
            raise ApiProblem(HTTPStatus.NOT_FOUND, "request_not_found", "Swap request not found.")
        if action in {"accept", "reject"}:
            if actor_id != row["target_user_id"]:
                raise ApiProblem(HTTPStatus.FORBIDDEN, "request_forbidden", "Only the recipient can perform this action.")
            if row["status"] != "pending":
                raise ApiProblem(HTTPStatus.CONFLICT, "invalid_request_status", "This request is no longer pending.")
            if action == "accept":
                connection.execute("UPDATE swap_requests SET status='accepted',accepted_at=? WHERE id=?", (now, request_id))
            else:
                connection.execute("UPDATE swap_requests SET status='rejected',rejected_at=? WHERE id=?", (now, request_id))
        elif action == "cancel":
            if actor_id != row["requester_id"]:
                raise ApiProblem(HTTPStatus.FORBIDDEN, "request_forbidden", "Only the requester can cancel this request.")
            if row["status"] != "pending":
                raise ApiProblem(HTTPStatus.CONFLICT, "invalid_request_status", "Only pending requests can be cancelled.")
            connection.execute("UPDATE swap_requests SET status='cancelled',cancelled_at=? WHERE id=?", (now, request_id))
        elif action == "complete":
            if row["status"] not in {"accepted", "completed"}:
                raise ApiProblem(HTTPStatus.CONFLICT, "invalid_request_status", "Only accepted requests can be completed.")
            column = "requester_completed_at" if actor_id == row["requester_id"] else "target_completed_at"
            if row[column] is None:
                connection.execute(f"UPDATE swap_requests SET {column}=? WHERE id=?", (now, request_id))
            updated = connection.execute("SELECT * FROM swap_requests WHERE id=?", (request_id,)).fetchone()
            if updated["status"] == "accepted" and updated["requester_completed_at"] and updated["target_completed_at"]:
                connection.execute("UPDATE swap_requests SET status='completed',completed_at=? WHERE id=?", (now, request_id))
        else:
            raise ApiProblem(HTTPStatus.NOT_FOUND, "not_found", "Unknown request action.")
        updated = connection.execute("SELECT * FROM swap_requests WHERE id=?", (request_id,)).fetchone()
    return _serialize_swap_request(account_db, skills_db, updated, actor_id)


def _shanghai_day_bounds(now: datetime | None = None) -> tuple[int, int, str, str]:
    current = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Shanghai"))
    start_local = current.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start_utc, end_utc = start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
    return int(start_utc.timestamp()), int(end_utc.timestamp()), start_utc.isoformat(timespec="seconds"), end_utc.isoformat(timespec="seconds")


def community_stats(account_db: Path, skills_db: Path, *, now: datetime | None = None) -> dict[str, Any]:
    start_epoch, end_epoch, start_iso, end_iso = _shanghai_day_bounds(now)
    with closing(connect_database(account_db)) as connection:
        registered = connection.execute(
            "SELECT COUNT(*) FROM users WHERE is_active=1 AND role='user'"
        ).fetchone()[0]
        online_today = connection.execute(
            """SELECT COUNT(DISTINCT e.user_id) FROM login_events e JOIN users u ON u.id=e.user_id
               WHERE e.created_at>=? AND e.created_at<? AND u.is_active=1
                 AND u.role='user'""", (start_epoch, end_epoch)
        ).fetchone()[0]
        completed = connection.execute(
            """SELECT COUNT(*) FROM swap_requests
               WHERE status='completed' AND completed_at>=? AND completed_at<?""",
            (start_iso, end_iso),
        ).fetchone()[0]
        community_users = connection.execute(
            """SELECT COUNT(*) FROM users u JOIN user_profiles p ON p.user_id=u.id
               WHERE u.is_active=1 AND u.role='user' AND p.onboarding_completed=1
                 AND p.profile_visibility='community'"""
        ).fetchone()[0]
        trend_rows = connection.execute(
            """SELECT us.skill_id,COUNT(*) AS want_count
               FROM user_skills us JOIN users u ON u.id=us.user_id
               JOIN user_profiles p ON p.user_id=u.id
               WHERE us.direction='want' AND u.is_active=1 AND u.role='user'
                 AND p.onboarding_completed=1
                 AND p.profile_visibility='community'
               GROUP BY us.skill_id ORDER BY want_count DESC,us.skill_id ASC"""
        ).fetchall()
    active_ids = {skill["id"] for skill in list_skills(skills_db)}
    trending = [
        {"skillId": row["skill_id"], "wantCount": row["want_count"]}
        for row in trend_rows if row["skill_id"] in active_ids
    ]
    return {
        "onlineToday": online_today, "registeredUsers": registered,
        "communityUsers": community_users, "swapsCompletedToday": completed,
        "trendingSkills": trending,
    }


class AdminLoginRateLimiter:
    def __init__(self) -> None:
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def is_blocked(self, key: str, now: float | None = None) -> bool:
        current = now if now is not None else time.time()
        cutoff = current - ADMIN_RATE_LIMIT_WINDOW_SECONDS
        with self._lock:
            recent = [item for item in self._attempts.get(key, []) if item > cutoff]
            self._attempts[key] = recent
            return len(recent) >= ADMIN_RATE_LIMIT_ATTEMPTS

    def record_failure(self, key: str, now: float | None = None) -> None:
        current = now if now is not None else time.time()
        cutoff = current - ADMIN_RATE_LIMIT_WINDOW_SECONDS
        with self._lock:
            recent = [item for item in self._attempts.get(key, []) if item > cutoff]
            recent.append(current)
            self._attempts[key] = recent

    def reset(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


@dataclass(frozen=True)
class ServerConfig:
    db_path: Path
    skills_db_path: Path = DEFAULT_SKILLS_DB_PATH
    secure_cookie: bool = False
    admin_rate_limiter: AdminLoginRateLimiter = field(
        default_factory=AdminLoginRateLimiter, compare=False
    )


class SkillSwapHandler(BaseHTTPRequestHandler):
    server_version = "SkillSwapServer/2.0"
    config: ServerConfig

    def do_GET(self) -> None:  # noqa: N802
        parsed, path = urlsplit(self.path), urlsplit(self.path).path
        if self._is_admin_path(path):
            if not self._require_loopback(path.startswith("/api/")):
                return
            if path in {"/admin", "/admin/", "/admin.html"}:
                self._send_static(APP_ROOT / "admin.html", "text/html; charset=utf-8")
                return
            self._dispatch_admin_get(path)
            return
        try:
            if path == "/api/health":
                self._send_json(HTTPStatus.OK, {"status": "ok"})
            elif path == "/api/community/stats":
                self._send_json(HTTPStatus.OK, community_stats(self.config.db_path, self.config.skills_db_path))
            elif path == "/api/auth/me":
                user = self._require_user()
                if user is not None: self._send_json(HTTPStatus.OK, {"user": public_user(user)})
            elif path == "/api/users/me/profile":
                user = self._require_user()
                if user is not None: self._send_json(HTTPStatus.OK, {"profile": get_profile(self.config.db_path, user["id"])})
            elif path == "/api/users/me/skills":
                user = self._require_user()
                if user is not None: self._send_json(HTTPStatus.OK, get_user_skills(self.config.db_path, user["id"]))
            elif path == "/api/skills":
                user = self._require_user()
                if user is not None:
                    params = parse_qs(parsed.query)
                    category, query = params.get("category", [""])[0], params.get("q", [""])[0]
                    if category and category not in SKILL_CATEGORIES:
                        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_category", "Unknown skill category.")
                    self._send_json(HTTPStatus.OK, {"skills": list_skills(self.config.skills_db_path, query=query, category=category)})
            elif path.startswith("/api/skills/"):
                user = self._require_user()
                if user is not None:
                    row = skill_by_id(self.config.skills_db_path, unquote(path.removeprefix("/api/skills/")))
                    if row is None or not row["is_active"]:
                        raise ApiProblem(HTTPStatus.NOT_FOUND, "skill_not_found", "Skill not found.")
                    self._send_json(HTTPStatus.OK, {"skill": serialize_skill(row)})
            elif path == "/api/search":
                user = self._require_user()
                if user is not None: self._send_json(HTTPStatus.OK, search_catalog_and_users(self.config.db_path, self.config.skills_db_path, user["id"], parse_qs(parsed.query)))
            elif path == "/api/swap-requests":
                user = self._require_user()
                if user is not None: self._send_json(HTTPStatus.OK, list_swap_requests(self.config.db_path, self.config.skills_db_path, user["id"]))
            elif path in {"/", "/v4.2.html"}:
                self._send_static(APP_ROOT / "v4.2.html", "text/html; charset=utf-8")
            elif path == "/index.html":
                self._send_static(APP_ROOT / "index.html", "text/html; charset=utf-8")
            else:
                self._send_not_found(path.startswith("/api/"))
        except ApiProblem as problem:
            self._send_problem(problem)

    def do_HEAD(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if self._is_admin_path(path):
            if not self._require_loopback(path.startswith("/api/")):
                return
            if path in {"/admin", "/admin/", "/admin.html"}:
                self._send_static(
                    APP_ROOT / "admin.html", "text/html; charset=utf-8", head=True
                )
                return
            self._send_not_found(path.startswith("/api/"), head=True)
            return
        if path in {"/", "/v4.2.html"}:
            self._send_static(APP_ROOT / "v4.2.html", "text/html; charset=utf-8", head=True)
            return
        if path == "/index.html":
            self._send_static(APP_ROOT / "index.html", "text/html; charset=utf-8", head=True)
            return
        self._send_not_found(path.startswith("/api/"), head=True)

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path.startswith("/api/admin/"):
            if not self._require_loopback(True):
                return
            self._dispatch_admin_post(path)
            return
        request_action = re.fullmatch(r"/api/swap-requests/(\d+)/(accept|reject|cancel|complete)", path)
        try:
            if path == "/api/auth/login": self._handle_login()
            elif path == "/api/auth/register": self._handle_register()
            elif path == "/api/auth/logout":
                delete_session(self.config.db_path, self._cookie_value(SESSION_COOKIE))
                self.send_response(HTTPStatus.NO_CONTENT); self._send_security_headers()
                self.send_header("Set-Cookie", self._expired_cookie(SESSION_COOKIE, "Lax")); self.send_header("Cache-Control", "no-store"); self.end_headers()
            elif path == "/api/swap-requests":
                user, payload = self._require_user(), None
                if user is not None: payload = self._read_json_body()
                if user is not None and payload is not None:
                    self._send_json(HTTPStatus.CREATED, {"request": create_swap_request(self.config.db_path, self.config.skills_db_path, user["id"], payload)})
            elif request_action:
                user = self._require_user()
                if user is not None:
                    item = update_swap_request(self.config.db_path, self.config.skills_db_path, int(request_action.group(1)), user["id"], request_action.group(2))
                    self._send_json(HTTPStatus.OK, {"request": item})
            else: self._send_not_found(path.startswith("/api/"))
        except ApiProblem as problem: self._send_problem(problem)

    def do_PUT(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        admin_skill_match = re.fullmatch(r"/api/admin/skills/([a-z0-9]+(?:-[a-z0-9]+)*)", path)
        if path.startswith("/api/admin/"):
            if not self._require_loopback(True):
                return
            if admin_skill_match:
                self._handle_admin_update_skill(admin_skill_match.group(1))
            else:
                self._send_not_found(True)
            return
        try:
            if path == "/api/users/me/profile":
                user, payload = self._require_user(), None
                if user is not None: payload = self._read_json_body()
                if user is not None and payload is not None: self._send_json(HTTPStatus.OK, {"profile": update_profile(self.config.db_path, user["id"], payload)})
            elif path == "/api/users/me/skills":
                user, payload = self._require_user(), None
                if user is not None: payload = self._read_json_body()
                if user is not None and payload is not None: self._send_json(HTTPStatus.OK, replace_user_skills(self.config.db_path, self.config.skills_db_path, user["id"], payload))
            else: self._send_not_found(path.startswith("/api/"))
        except ApiProblem as problem: self._send_problem(problem)

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlsplit(self.path).path
        if path.startswith("/api/admin/"):
            if not self._require_loopback(True):
                return
            match = re.fullmatch(r"/api/admin/users/(\d+)", path)
            if match:
                self._handle_admin_update_user(int(match.group(1)))
                return
        self._send_not_found(path.startswith("/api/"))

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlsplit(self.path).path
        if path.startswith("/api/admin/"):
            if not self._require_loopback(True):
                return
            skill_match = re.fullmatch(
                r"/api/admin/skills/([a-z0-9]+(?:-[a-z0-9]+)*)", path
            )
            if skill_match:
                self._handle_admin_deactivate_skill(skill_match.group(1))
                return
            user_match = re.fullmatch(r"/api/admin/users/(\d+)", path)
            if user_match:
                self._handle_admin_delete_user(int(user_match.group(1)))
                return
            session_match = re.fullmatch(
                r"/api/admin/sessions/([A-Za-z0-9_-]{16,64})", path
            )
            if session_match:
                self._handle_admin_revoke_session(session_match.group(1))
                return
        self._send_not_found(path.startswith("/api/"))

    def _is_admin_path(self, path: str) -> bool:
        return path in {"/admin", "/admin/", "/admin.html"} or path.startswith(
            "/api/admin/"
        )

    def _require_loopback(self, api_request: bool) -> bool:
        if is_loopback_address(self.client_address[0]):
            return True
        if api_request:
            self._send_api_error(
                HTTPStatus.FORBIDDEN,
                "local_access_only",
                "管理员后台仅允许从服务器本机访问。",
            )
        else:
            body = "管理员后台仅允许从服务器本机访问。".encode("utf-8")
            self.send_response(HTTPStatus.FORBIDDEN)
            self._send_security_headers()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        return False

    def _dispatch_admin_get(self, path: str) -> None:
        if path == "/api/admin/auth/me":
            self._handle_admin_me()
        elif path == "/api/admin/overview":
            self._handle_admin_overview()
        elif path == "/api/admin/users":
            self._handle_admin_list_users()
        elif path == "/api/admin/skills":
            self._handle_admin_list_skills()
        elif path == "/api/admin/sessions":
            self._handle_admin_list_sessions()
        elif path == "/api/admin/audit-logs":
            self._handle_admin_list_audit_logs()
        else:
            self._send_not_found(True)

    def _dispatch_admin_post(self, path: str) -> None:
        if path == "/api/admin/auth/login":
            self._handle_admin_login()
            return
        if path == "/api/admin/auth/logout":
            self._handle_admin_logout()
            return
        if path == "/api/admin/users":
            self._handle_admin_create_user()
            return
        if path == "/api/admin/skills":
            self._handle_admin_create_skill()
            return
        password_match = re.fullmatch(r"/api/admin/users/(\d+)/password", path)
        if password_match:
            self._handle_admin_reset_password(int(password_match.group(1)))
            return
        self._send_not_found(True)

    def _handle_login(self) -> None:
        payload = self._read_json_body()
        if payload is None: return
        email, password = payload.get("email"), payload.get("password")
        if not isinstance(email, str) or not isinstance(password, str):
            raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_request", "Email and password are required.")
        user = authenticate_user(self.config.db_path, email, password)
        if user is None:
            raise ApiProblem(HTTPStatus.UNAUTHORIZED, "invalid_credentials", "Invalid email or password.")
        self._send_json(
            HTTPStatus.OK,
            {"user": public_user(user)},
            extra_headers=[("Set-Cookie", self._session_cookie(create_session(self.config.db_path, user["id"])))],
        )

    def _handle_register(self) -> None:
        payload = self._read_json_body()
        if payload is None: return
        email, password = payload.get("email"), payload.get("password")
        if not isinstance(email, str) or not isinstance(password, str):
            raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_request", "Email and password are required.")
        user = register_user(self.config.db_path, email, password)
        self._send_json(
            HTTPStatus.CREATED,
            {"user": public_user(user)},
            extra_headers=[("Set-Cookie", self._session_cookie(create_session(self.config.db_path, user["id"])))],
        )

    def _handle_me(self) -> None:
        user = user_for_session(self.config.db_path, self._cookie_value(SESSION_COOKIE))
        if user is None:
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "not_authenticated", "message": "Authentication required."},
            )
            return
        self._send_json(HTTPStatus.OK, {"user": public_user(user)})

    def _require_user(self) -> sqlite3.Row | None:
        user = user_for_session(self.config.db_path, self._cookie_value(SESSION_COOKIE))
        if user is None: self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "not_authenticated", "message": "Authentication required."})
        return user

    def _handle_admin_login(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return
        email = payload.get("email")
        password = payload.get("password")
        if not isinstance(email, str) or not isinstance(password, str):
            self._send_api_error(
                HTTPStatus.BAD_REQUEST, "invalid_request", "请输入管理员邮箱和密码。"
            )
            return
        normalized_email = normalize_email(email)
        rate_key = f"{self.client_address[0]}|{normalized_email}"
        limiter = self.config.admin_rate_limiter
        if limiter.is_blocked(rate_key):
            self._send_api_error(
                HTTPStatus.TOO_MANY_REQUESTS,
                "rate_limited",
                "登录失败次数过多，请在 15 分钟后重试。",
            )
            return
        user = authenticate_user(self.config.db_path, email, password)
        if user is None or user["role"] != "superadmin":
            limiter.record_failure(rate_key)
            self._send_api_error(
                HTTPStatus.UNAUTHORIZED,
                "invalid_credentials",
                "管理员邮箱或密码不正确。",
            )
            return
        limiter.reset(rate_key)
        token, csrf_token = create_admin_session(self.config.db_path, user["id"])
        with closing(connect_database(self.config.db_path)) as connection, connection:
            _audit_event(
                connection,
                user,
                "admin.login",
                "session",
                "current",
                {"ip": self.client_address[0]},
            )
        self._send_json(
            HTTPStatus.OK,
            {"admin": self._admin_public_user(user)},
            extra_headers=[
                ("Set-Cookie", self._admin_session_cookie(token)),
                ("Set-Cookie", self._admin_csrf_cookie(csrf_token)),
            ],
        )

    def _handle_admin_me(self) -> None:
        admin = self._require_admin()
        if admin is None:
            return
        self._send_json(
            HTTPStatus.OK,
            {"admin": self._admin_public_user(admin), "sessionId": admin["public_id"]},
        )

    def _handle_admin_logout(self) -> None:
        admin = self._require_admin(write=True)
        if admin is None:
            return
        token = self._cookie_value(ADMIN_SESSION_COOKIE)
        with closing(connect_database(self.config.db_path)) as connection, connection:
            _audit_event(
                connection, admin, "admin.logout", "session", admin["public_id"]
            )
        delete_session(self.config.db_path, token)
        self._send_json(
            HTTPStatus.OK,
            {"ok": True},
            extra_headers=[
                (
                    "Set-Cookie",
                    self._expired_cookie(ADMIN_SESSION_COOKIE, "Strict"),
                ),
                ("Set-Cookie", self._expired_cookie(ADMIN_CSRF_COOKIE, "Strict")),
            ],
        )

    def _handle_admin_overview(self) -> None:
        admin = self._require_admin()
        if admin is None:
            return
        now = int(time.time())
        with closing(connect_database(self.config.db_path)) as connection:
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM users) AS total_users,
                    (SELECT COUNT(*) FROM users WHERE is_active = 1) AS active_users,
                    (SELECT COUNT(*) FROM users
                        WHERE role = 'superadmin' AND is_active = 1) AS active_admins,
                    (SELECT COUNT(*) FROM sessions WHERE expires_at > ?) AS active_sessions,
                    (SELECT COUNT(*) FROM admin_audit_log) AS audit_events
                """,
                (now,),
            ).fetchone()
        self._send_json(
            HTTPStatus.OK,
            {
                "overview": {
                    "totalUsers": row["total_users"],
                    "activeUsers": row["active_users"],
                    "activeAdmins": row["active_admins"],
                    "activeSessions": row["active_sessions"],
                    "auditEvents": row["audit_events"],
                }
            },
        )

    def _handle_admin_list_skills(self) -> None:
        admin = self._require_admin()
        if admin is None:
            return
        query = self._query_parameters()
        category = self._query_value(query, "category")
        if category and category not in SKILL_CATEGORIES:
            self._send_api_error(
                HTTPStatus.BAD_REQUEST, "invalid_category", "技能分类无效。"
            )
            return
        items = list_skills(
            self.config.skills_db_path,
            query=self._query_value(query, "query"),
            category=category,
            include_inactive=True,
        )
        self._send_json(HTTPStatus.OK, {"skills": items})

    def _handle_admin_create_skill(self) -> None:
        admin = self._require_admin(write=True)
        if admin is None:
            return
        payload = self._read_json_body()
        if payload is None:
            return
        try:
            skill = create_skill(self.config.skills_db_path, payload)
        except ApiProblem as problem:
            self._send_problem(problem)
            return
        self._audit_admin_skill(admin, "skill.create", skill["id"], skill)
        self._send_json(HTTPStatus.CREATED, {"skill": skill})

    def _handle_admin_update_skill(self, skill_id: str) -> None:
        admin = self._require_admin(write=True)
        if admin is None:
            return
        payload = self._read_json_body()
        if payload is None:
            return
        try:
            skill = update_skill(self.config.skills_db_path, skill_id, payload)
        except ApiProblem as problem:
            self._send_problem(problem)
            return
        self._audit_admin_skill(admin, "skill.update", skill_id, skill)
        self._send_json(HTTPStatus.OK, {"skill": skill})

    def _handle_admin_deactivate_skill(self, skill_id: str) -> None:
        admin = self._require_admin(write=True)
        if admin is None:
            return
        try:
            deactivate_skill(self.config.skills_db_path, skill_id)
        except ApiProblem as problem:
            self._send_problem(problem)
            return
        self._audit_admin_skill(admin, "skill.deactivate", skill_id)
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_security_headers()
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _audit_admin_skill(
        self,
        admin: sqlite3.Row,
        action: str,
        skill_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        with closing(connect_database(self.config.db_path)) as connection, connection:
            _audit_event(connection, admin, action, "skill", skill_id, details)

    def _handle_admin_list_users(self) -> None:
        admin = self._require_admin()
        if admin is None:
            return
        query = self._query_parameters()
        page, page_size = self._pagination(query)
        search = self._query_value(query, "query").strip()
        role = self._query_value(query, "role")
        status = self._query_value(query, "status")
        where: list[str] = []
        parameters: list[Any] = []
        if search:
            where.append("(email LIKE ? OR display_name LIKE ?)")
            pattern = f"%{search}%"
            parameters.extend([pattern, pattern])
        if role in {"user", "superadmin"}:
            where.append("role = ?")
            parameters.append(role)
        if status in {"active", "inactive"}:
            where.append("is_active = ?")
            parameters.append(1 if status == "active" else 0)
        where_sql = f" WHERE {' AND '.join(where)}" if where else ""
        now = int(time.time())
        with closing(connect_database(self.config.db_path)) as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM users{where_sql}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT users.id, users.email, users.display_name, users.role,
                       users.is_active, users.created_at,
                       (SELECT COUNT(*) FROM sessions
                        WHERE sessions.user_id = users.id
                          AND sessions.expires_at > ?) AS active_session_count
                FROM users{where_sql}
                ORDER BY users.id DESC
                LIMIT ? OFFSET ?
                """,
                [now, *parameters, page_size, (page - 1) * page_size],
            ).fetchall()
        self._send_json(
            HTTPStatus.OK,
            self._page_payload(
                [self._admin_public_user(row) for row in rows], page, page_size, total
            ),
        )

    def _handle_admin_create_user(self) -> None:
        admin = self._require_admin(write=True)
        if admin is None:
            return
        payload = self._read_json_body()
        if payload is None:
            return
        validated = self._validated_new_user(payload)
        if validated is None:
            return
        email, display_name, password, role, is_active = validated
        salt, password_digest = hash_password(password)
        try:
            with closing(connect_database(self.config.db_path)) as connection, connection:
                cursor = connection.execute(
                    """
                    INSERT INTO users
                        (email, password_salt, password_hash, display_name,
                         is_active, created_at, role)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        email,
                        salt,
                        password_digest,
                        display_name,
                        int(is_active),
                        utc_now_iso(),
                        role,
                    ),
                )
                user_id = int(cursor.lastrowid)
                _audit_event(
                    connection,
                    admin,
                    "user.create",
                    "user",
                    str(user_id),
                    {"email": email, "role": role, "isActive": is_active},
                )
                created = self._fetch_admin_user(connection, user_id)
        except sqlite3.IntegrityError:
            self._send_api_error(
                HTTPStatus.CONFLICT, "email_exists", "该邮箱已存在。"
            )
            return
        self._send_json(
            HTTPStatus.CREATED, {"user": self._admin_public_user(created)}
        )

    def _handle_admin_update_user(self, user_id: int) -> None:
        admin = self._require_admin(write=True)
        if admin is None:
            return
        payload = self._read_json_body()
        if payload is None:
            return
        allowed = {"email", "displayName", "role", "isActive"}
        if not payload or not set(payload).issubset(allowed):
            self._send_api_error(
                HTTPStatus.BAD_REQUEST, "invalid_fields", "提交了不支持的用户字段。"
            )
            return
        try:
            with closing(connect_database(self.config.db_path)) as connection, connection:
                target = connection.execute(
                    "SELECT * FROM users WHERE id = ?", (user_id,)
                ).fetchone()
                if target is None:
                    self._send_api_error(
                        HTTPStatus.NOT_FOUND, "user_not_found", "用户不存在。"
                    )
                    return
                email = payload.get("email", target["email"])
                display_name = payload.get("displayName", target["display_name"])
                role = payload.get("role", target["role"])
                is_active = payload.get("isActive", bool(target["is_active"]))
                if not isinstance(email, str) or not validate_email(email):
                    self._send_api_error(
                        HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_email", "邮箱格式不正确。"
                    )
                    return
                if (
                    not isinstance(display_name, str)
                    or not display_name.strip()
                    or len(display_name.strip()) > 80
                ):
                    self._send_api_error(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        "invalid_display_name",
                        "显示名称必须为 1-80 个字符。",
                    )
                    return
                if role not in {"user", "superadmin"}:
                    self._send_api_error(
                        HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_role", "用户角色无效。"
                    )
                    return
                if not isinstance(is_active, bool):
                    self._send_api_error(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        "invalid_status",
                        "用户状态无效。",
                    )
                    return
                if user_id == admin["id"] and (
                    role != "superadmin" or not is_active
                ):
                    self._send_api_error(
                        HTTPStatus.CONFLICT,
                        "self_protection",
                        "不能停用或降级当前管理员账户。",
                    )
                    return
                removes_active_admin = (
                    target["role"] == "superadmin"
                    and bool(target["is_active"])
                    and (role != "superadmin" or not is_active)
                )
                if removes_active_admin and self._active_admin_count(connection) <= 1:
                    self._send_api_error(
                        HTTPStatus.CONFLICT,
                        "last_admin",
                        "不能移除最后一个有效超级管理员。",
                    )
                    return
                connection.execute(
                    """
                    UPDATE users
                    SET email = ?, display_name = ?, role = ?, is_active = ?
                    WHERE id = ?
                    """,
                    (
                        normalize_email(email),
                        display_name.strip(),
                        role,
                        int(is_active),
                        user_id,
                    ),
                )
                _audit_event(
                    connection,
                    admin,
                    "user.update",
                    "user",
                    str(user_id),
                    {
                        "email": normalize_email(email),
                        "role": role,
                        "isActive": is_active,
                        "changedFields": sorted(payload),
                    },
                )
                updated = self._fetch_admin_user(connection, user_id)
        except sqlite3.IntegrityError:
            self._send_api_error(
                HTTPStatus.CONFLICT, "email_exists", "该邮箱已存在。"
            )
            return
        self._send_json(HTTPStatus.OK, {"user": self._admin_public_user(updated)})

    def _handle_admin_reset_password(self, user_id: int) -> None:
        admin = self._require_admin(write=True)
        if admin is None:
            return
        payload = self._read_json_body()
        if payload is None:
            return
        password = payload.get("password")
        with closing(connect_database(self.config.db_path)) as connection, connection:
            target = connection.execute(
                "SELECT id, email, role FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if target is None:
                self._send_api_error(
                    HTTPStatus.NOT_FOUND, "user_not_found", "用户不存在。"
                )
                return
            if not isinstance(password, str) or not validate_password_for_role(
                password, target["role"]
            ):
                minimum = 12 if target["role"] == "superadmin" else 8
                self._send_api_error(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "invalid_password",
                    f"密码必须为 {minimum}-128 个字符。",
                )
                return
            salt, password_digest = hash_password(password)
            connection.execute(
                "UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?",
                (salt, password_digest, user_id),
            )
            revoked = connection.execute(
                "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            _audit_event(
                connection,
                admin,
                "user.password_reset",
                "user",
                str(user_id),
                {"email": target["email"], "revokedSessions": revoked},
            )
        self._send_json(HTTPStatus.OK, {"ok": True, "revokedSessions": revoked})

    def _handle_admin_delete_user(self, user_id: int) -> None:
        admin = self._require_admin(write=True)
        if admin is None:
            return
        if user_id == admin["id"]:
            self._send_api_error(
                HTTPStatus.CONFLICT, "self_protection", "不能删除当前管理员账户。"
            )
            return
        with closing(connect_database(self.config.db_path)) as connection, connection:
            target = connection.execute(
                "SELECT id, email, role, is_active FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if target is None:
                self._send_api_error(
                    HTTPStatus.NOT_FOUND, "user_not_found", "用户不存在。"
                )
                return
            if (
                target["role"] == "superadmin"
                and bool(target["is_active"])
                and self._active_admin_count(connection) <= 1
            ):
                self._send_api_error(
                    HTTPStatus.CONFLICT,
                    "last_admin",
                    "不能删除最后一个有效超级管理员。",
                )
                return
            _audit_event(
                connection,
                admin,
                "user.delete",
                "user",
                str(user_id),
                {"email": target["email"], "role": target["role"]},
            )
            connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self._send_json(HTTPStatus.OK, {"ok": True})

    def _handle_admin_list_sessions(self) -> None:
        admin = self._require_admin()
        if admin is None:
            return
        query = self._query_parameters()
        page, page_size = self._pagination(query)
        purpose = self._query_value(query, "purpose")
        status = self._query_value(query, "status")
        user_id = self._query_value(query, "userId")
        where: list[str] = []
        parameters: list[Any] = []
        now = int(time.time())
        if purpose in {"user", "admin"}:
            where.append("sessions.purpose = ?")
            parameters.append(purpose)
        if status in {"active", "expired"}:
            where.append("sessions.expires_at > ?" if status == "active" else "sessions.expires_at <= ?")
            parameters.append(now)
        if user_id.isdigit():
            where.append("sessions.user_id = ?")
            parameters.append(int(user_id))
        where_sql = f" WHERE {' AND '.join(where)}" if where else ""
        with closing(connect_database(self.config.db_path)) as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM sessions{where_sql}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT sessions.public_id, sessions.user_id, sessions.purpose,
                       sessions.created_at, sessions.expires_at,
                       users.email, users.display_name
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                {where_sql}
                ORDER BY sessions.created_at DESC
                LIMIT ? OFFSET ?
                """,
                [*parameters, page_size, (page - 1) * page_size],
            ).fetchall()
        items = [
            {
                "publicId": row["public_id"],
                "userId": row["user_id"],
                "email": row["email"],
                "displayName": row["display_name"],
                "purpose": row["purpose"],
                "createdAt": row["created_at"],
                "expiresAt": row["expires_at"],
                "status": "active" if row["expires_at"] > now else "expired",
                "isCurrent": row["public_id"] == admin["public_id"],
            }
            for row in rows
        ]
        self._send_json(
            HTTPStatus.OK, self._page_payload(items, page, page_size, total)
        )

    def _handle_admin_revoke_session(self, public_id: str) -> None:
        admin = self._require_admin(write=True)
        if admin is None:
            return
        if public_id == admin["public_id"]:
            self._send_api_error(
                HTTPStatus.CONFLICT,
                "current_session",
                "当前管理员会话只能通过退出登录结束。",
            )
            return
        with closing(connect_database(self.config.db_path)) as connection, connection:
            target = connection.execute(
                """
                SELECT sessions.public_id, sessions.user_id, sessions.purpose,
                       users.email
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.public_id = ?
                """,
                (public_id,),
            ).fetchone()
            if target is None:
                self._send_api_error(
                    HTTPStatus.NOT_FOUND, "session_not_found", "会话不存在。"
                )
                return
            connection.execute("DELETE FROM sessions WHERE public_id = ?", (public_id,))
            _audit_event(
                connection,
                admin,
                "session.revoke",
                "session",
                public_id,
                {
                    "userId": target["user_id"],
                    "email": target["email"],
                    "purpose": target["purpose"],
                },
            )
        self._send_json(HTTPStatus.OK, {"ok": True})

    def _handle_admin_list_audit_logs(self) -> None:
        admin = self._require_admin()
        if admin is None:
            return
        query = self._query_parameters()
        page, page_size = self._pagination(query)
        search = self._query_value(query, "query").strip()
        where_sql = ""
        parameters: list[Any] = []
        if search:
            where_sql = (
                " WHERE actor_email LIKE ? OR action LIKE ? OR target_id LIKE ?"
            )
            pattern = f"%{search}%"
            parameters = [pattern, pattern, pattern]
        with closing(connect_database(self.config.db_path)) as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM admin_audit_log{where_sql}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT id, actor_user_id, actor_email, action, target_type,
                       target_id, details_json, created_at
                FROM admin_audit_log{where_sql}
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                [*parameters, page_size, (page - 1) * page_size],
            ).fetchall()
        items = []
        for row in rows:
            try:
                details = json.loads(row["details_json"])
            except json.JSONDecodeError:
                details = {}
            items.append(
                {
                    "id": row["id"],
                    "actorUserId": row["actor_user_id"],
                    "actorEmail": row["actor_email"],
                    "action": row["action"],
                    "targetType": row["target_type"],
                    "targetId": row["target_id"],
                    "details": details,
                    "createdAt": row["created_at"],
                }
            )
        self._send_json(
            HTTPStatus.OK, self._page_payload(items, page, page_size, total)
        )

    def _require_admin(self, *, write: bool = False) -> sqlite3.Row | None:
        token = self._cookie_value(ADMIN_SESSION_COOKIE)
        admin = admin_for_session(self.config.db_path, token)
        if admin is None:
            self._send_api_error(
                HTTPStatus.UNAUTHORIZED, "not_authenticated", "请先登录管理员后台。"
            )
            return None
        if write and not self._valid_admin_write(admin):
            return None
        return admin

    def _valid_admin_write(self, admin: sqlite3.Row) -> bool:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            self._send_api_error(
                HTTPStatus.BAD_REQUEST,
                "json_required",
                "管理员写操作必须使用 application/json。",
            )
            return False
        origin = self.headers.get("Origin", "")
        host = self.headers.get("Host", "")
        parsed_origin = urlsplit(origin)
        if (
            parsed_origin.scheme not in {"http", "https"}
            or not host
            or parsed_origin.netloc.casefold() != host.casefold()
        ):
            self._send_api_error(
                HTTPStatus.FORBIDDEN, "invalid_origin", "管理员请求来源无效。"
            )
            return False
        csrf_header = self.headers.get("X-CSRF-Token", "")
        csrf_cookie = self._cookie_value(ADMIN_CSRF_COOKIE)
        expected_hash = admin["csrf_token_hash"] or ""
        candidate_hash = hashlib.sha256(csrf_header.encode("utf-8")).hexdigest()
        if (
            not csrf_header
            or not csrf_cookie
            or not hmac.compare_digest(csrf_header, csrf_cookie)
            or not hmac.compare_digest(candidate_hash, expected_hash)
        ):
            self._send_api_error(
                HTTPStatus.FORBIDDEN, "invalid_csrf", "安全校验失败，请重新登录。"
            )
            return False
        return True

    def _validated_new_user(
        self, payload: dict[str, Any]
    ) -> tuple[str, str, str, str, bool] | None:
        allowed = {"email", "displayName", "password", "role", "isActive"}
        if not set(payload).issubset(allowed):
            self._send_api_error(
                HTTPStatus.BAD_REQUEST, "invalid_fields", "提交了不支持的用户字段。"
            )
            return None
        email = payload.get("email")
        display_name = payload.get("displayName")
        password = payload.get("password")
        role = payload.get("role", "user")
        is_active = payload.get("isActive", True)
        if not isinstance(email, str) or not validate_email(email):
            self._send_api_error(
                HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_email", "邮箱格式不正确。"
            )
            return None
        if (
            not isinstance(display_name, str)
            or not display_name.strip()
            or len(display_name.strip()) > 80
        ):
            self._send_api_error(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "invalid_display_name",
                "显示名称必须为 1-80 个字符。",
            )
            return None
        if role not in {"user", "superadmin"}:
            self._send_api_error(
                HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_role", "用户角色无效。"
            )
            return None
        if not isinstance(password, str) or not validate_password_for_role(password, role):
            minimum = 12 if role == "superadmin" else 8
            self._send_api_error(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "invalid_password",
                f"密码必须为 {minimum}-128 个字符。",
            )
            return None
        if not isinstance(is_active, bool):
            self._send_api_error(
                HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_status", "用户状态无效。"
            )
            return None
        return (
            normalize_email(email),
            display_name.strip(),
            password,
            role,
            is_active,
        )

    def _active_admin_count(self, connection: sqlite3.Connection) -> int:
        return connection.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'superadmin' AND is_active = 1"
        ).fetchone()[0]

    def _fetch_admin_user(
        self, connection: sqlite3.Connection, user_id: int
    ) -> sqlite3.Row:
        return connection.execute(
            """
            SELECT users.id, users.email, users.display_name, users.role,
                   users.is_active, users.created_at,
                   (SELECT COUNT(*) FROM sessions
                    WHERE sessions.user_id = users.id
                      AND sessions.expires_at > ?) AS active_session_count
            FROM users WHERE users.id = ?
            """,
            (int(time.time()), user_id),
        ).fetchone()

    def _admin_public_user(self, user: sqlite3.Row) -> dict[str, Any]:
        result = {
            "id": user["id"],
            "email": user["email"],
            "displayName": user["display_name"],
            "role": user["role"],
        }
        keys = set(user.keys())
        if "is_active" in keys:
            result["isActive"] = bool(user["is_active"])
        if "created_at" in keys:
            result["createdAt"] = user["created_at"]
        if "active_session_count" in keys:
            result["activeSessionCount"] = user["active_session_count"]
        return result

    def _query_parameters(self) -> dict[str, list[str]]:
        return parse_qs(urlsplit(self.path).query, keep_blank_values=True)

    def _query_value(self, query: dict[str, list[str]], key: str) -> str:
        values = query.get(key, [""])
        return values[0] if values else ""

    def _pagination(self, query: dict[str, list[str]]) -> tuple[int, int]:
        try:
            page = max(1, int(self._query_value(query, "page") or "1"))
        except ValueError:
            page = 1
        try:
            page_size = int(self._query_value(query, "pageSize") or "25")
        except ValueError:
            page_size = 25
        return page, min(100, max(1, page_size))

    def _page_payload(
        self, items: list[dict[str, Any]], page: int, page_size: int, total: int
    ) -> dict[str, Any]:
        return {
            "items": items,
            "page": page,
            "pageSize": page_size,
            "total": total,
            "totalPages": max(1, (total + page_size - 1) // page_size),
        }

    def _read_json_body(self) -> dict[str, Any] | None:
        try: length = int(self.headers.get("Content-Length", "0"))
        except ValueError: length = -1
        if length <= 0 or length > MAX_JSON_BODY_BYTES:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request", "message": "Invalid request body."}); return None
        try: payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json", "message": "Request body must be valid JSON."}); return None
        if not isinstance(payload, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request", "message": "Request body must be an object."}); return None
        return payload

    def _cookie_value(self, name: str) -> str:
        raw_cookie = self.headers.get("Cookie", "")
        try:
            cookie = SimpleCookie(raw_cookie)
            morsel = cookie.get(name)
            return morsel.value if morsel else ""
        except Exception: return ""

    def _session_cookie(self, token: str) -> str:
        return self._cookie_header(
            SESSION_COOKIE, token, SESSION_TTL_SECONDS, "Lax", http_only=True
        )

    def _admin_session_cookie(self, token: str) -> str:
        return self._cookie_header(
            ADMIN_SESSION_COOKIE,
            token,
            ADMIN_SESSION_TTL_SECONDS,
            "Strict",
            http_only=True,
        )

    def _admin_csrf_cookie(self, token: str) -> str:
        return self._cookie_header(
            ADMIN_CSRF_COOKIE,
            token,
            ADMIN_SESSION_TTL_SECONDS,
            "Strict",
            http_only=False,
        )

    def _cookie_header(
        self,
        name: str,
        value: str,
        max_age: int,
        same_site: str,
        *,
        http_only: bool,
    ) -> str:
        parts = [
            f"{name}={value}",
            "Path=/",
            f"SameSite={same_site}",
            f"Max-Age={max_age}",
        ]
        if http_only:
            parts.append("HttpOnly")
        if self.config.secure_cookie:
            parts.append("Secure")
        return "; ".join(parts)

    def _expired_cookie(self, name: str, same_site: str) -> str:
        return self._cookie_header(
            name, "", 0, same_site, http_only=name != ADMIN_CSRF_COOKIE
        )

    def _send_api_error(
        self,
        status: HTTPStatus,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"error": code, "message": message}
        if details:
            payload["details"] = details
        self._send_json(status, payload)

    def _send_problem(self, problem: ApiProblem) -> None:
        self._send_api_error(problem.status, problem.code, problem.message)

    def _send_json(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
        *,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in extra_headers or []:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, path: Path, content_type: str, *, head: bool = False) -> None:
        try: body = path.read_bytes()
        except FileNotFoundError: self._send_not_found(False, head=head); return
        self.send_response(HTTPStatus.OK); self._send_security_headers(); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-cache"); self.end_headers()
        if not head: self.wfile.write(body)

    def _send_not_found(self, api_request: bool, *, head: bool = False) -> None:
        if api_request: self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found", "message": "Endpoint not found."}); return
        body = b"Not found"; self.send_response(HTTPStatus.NOT_FOUND); self._send_security_headers(); self.send_header("Content-Type", "text/plain; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers()
        if not head: self.wfile.write(body)

    def _send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("X-Frame-Options", "DENY"); self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")

    def log_message(self, format_string: str, *args: Any) -> None:
        sys.stderr.write(f"[{self.log_date_time_string()}] {self.address_string()} {format_string % args}\n")


def make_handler(config: ServerConfig) -> type[SkillSwapHandler]:
    class ConfiguredSkillSwapHandler(SkillSwapHandler):
        pass
    ConfiguredSkillSwapHandler.config = config
    return ConfiguredSkillSwapHandler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the SkillSwap development server.")
    parser.add_argument("--host", default=os.environ.get("SKILLSWAP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SKILLSWAP_PORT", "4173")))
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("SKILLSWAP_DB_PATH", DEFAULT_DB_PATH)))
    parser.add_argument("--skills-db", type=Path, default=Path(os.environ.get("SKILLSWAP_SKILLS_DB_PATH", DEFAULT_SKILLS_DB_PATH)))
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    demo_email = os.environ.get("SKILLSWAP_DEMO_EMAIL", "daniel@example.com")
    demo_password = os.environ.get("SKILLSWAP_DEMO_PASSWORD", "SkillSwap123!")
    admin_email = os.environ.get("SKILLSWAP_ADMIN_EMAIL")
    admin_password = os.environ.get("SKILLSWAP_ADMIN_PASSWORD")
    admin_name = os.environ.get("SKILLSWAP_ADMIN_NAME", "超级管理员")
    backup_path = initialize_database(
        args.db,
        demo_email=demo_email,
        demo_password=demo_password,
        admin_email=admin_email,
        admin_password=admin_password,
        admin_name=admin_name,
        admin_sync=env_flag("SKILLSWAP_ADMIN_SYNC"),
    )
    initialize_skill_database(args.skills_db)
    seed_admin_user_skills(args.db)
    config = ServerConfig(
        db_path=args.db,
        skills_db_path=args.skills_db,
        secure_cookie=env_flag("SKILLSWAP_SECURE_COOKIE"),
    )
    server = ThreadingHTTPServer((args.host, args.port), make_handler(config))
    print(f"SkillSwap running at http://{args.host}:{args.port}/")
    print(f"Account database: {args.db.resolve()}")
    print(f"Skill database: {args.skills_db.resolve()}")
    print(f"Demo login: {demo_email}")
    if backup_path:
        print(f"Database backup created before migration: {backup_path.resolve()}")
    if admin_email:
        print(f"Admin dashboard: http://{args.host}:{args.port}/admin")
        print(f"Admin account: {normalize_email(admin_email)}")
    else:
        print(f"Admin dashboard: http://{args.host}:{args.port}/admin")
        print("Admin bootstrap not configured; set SKILLSWAP_ADMIN_EMAIL and SKILLSWAP_ADMIN_PASSWORD for a fresh database")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping SkillSwap server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
