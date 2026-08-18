"""SkillSwap development server with SQLite-backed auth, profiles, and skills."""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
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
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

APP_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = APP_ROOT / "data" / "skillswap.db"
DEFAULT_SKILLS_DB_PATH = APP_ROOT / "data" / "skills.db"
SESSION_COOKIE = "skillswap_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
PBKDF2_ITERATIONS = 600_000
MAX_JSON_BODY_BYTES = 6 * 1024 * 1024
SKILL_CATEGORIES = {"technology", "creative", "academic", "sports", "lifestyle"}
SKILL_LEVELS = {"complete-beginner", "beginner", "intermediate", "advanced"}
SKILL_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

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


def initialize_database(
    db_path: Path,
    *,
    admin_email: str = "daniel@example.com",
    admin_password: str = "SkillSwap123!",
) -> None:
    """Initialize the account database and upgrade older checkouts in place."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
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
                is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
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
            """
        )
        connection.execute(
            """INSERT OR IGNORE INTO login_events(session_token_hash,user_id,created_at)
               SELECT token_hash,user_id,created_at FROM sessions"""
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
        if "is_admin" not in columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"
            )
        normalized = normalize_email(admin_email)
        demo = connection.execute("SELECT id FROM users WHERE email = ?", (normalized,)).fetchone()
        if demo is None:
            salt, digest = hash_password(admin_password)
            cursor = connection.execute(
                """INSERT INTO users
                   (email,password_salt,password_hash,display_name,is_admin,created_at)
                   VALUES (?,?,?,?,1,?)""",
                (normalized, salt, digest, "Daniel Liu", utc_now_iso()),
            )
            demo_user_id = cursor.lastrowid
        else:
            demo_user_id = demo["id"]
            connection.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (demo_user_id,))
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
            """SELECT id,email,password_salt,password_hash,display_name,is_admin
               FROM users WHERE email = ? AND is_active = 1""",
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
                   (email,password_salt,password_hash,display_name,is_admin,created_at)
                   VALUES (?,?,?,?,0,?)""",
                (normalized, salt, digest, parts[0][:80], now),
            )
            user_id = cursor.lastrowid
            connection.execute(
                "INSERT INTO user_profiles (user_id,languages_json,updated_at) VALUES (?,?,?)",
                (user_id, json.dumps(["zh"]), now),
            )
            return connection.execute(
                "SELECT id,email,display_name,is_admin FROM users WHERE id = ?", (user_id,)
            ).fetchone()
    except sqlite3.IntegrityError as error:
        raise ApiProblem(HTTPStatus.CONFLICT, "email_exists", "An account with this email already exists.") from error


def public_user(user: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(user["id"]),
        "email": user["email"],
        "displayName": user["display_name"],
        "isAdmin": bool(user["is_admin"]),
    }


def create_session(db_path: Path, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    now = int(time.time())
    with closing(connect_database(db_path)) as connection, connection:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        connection.execute(
            "INSERT INTO sessions (token_hash,user_id,created_at,expires_at) VALUES (?,?,?,?)",
            (token_hash, user_id, now, now + SESSION_TTL_SECONDS),
        )
        connection.execute(
            "INSERT INTO login_events (session_token_hash,user_id,created_at) VALUES (?,?,?)",
            (token_hash, user_id, now),
        )
    return token


def user_for_session(db_path: Path, token: str) -> sqlite3.Row | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with closing(connect_database(db_path)) as connection:
        return connection.execute(
            """SELECT u.id,u.email,u.display_name,u.is_admin
               FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.expires_at > ? AND u.is_active = 1""",
            (token_hash, int(time.time())),
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
            """SELECT u.id,u.email,u.display_name,u.is_admin,u.created_at,
                      p.age,p.country_id,p.city_id,p.languages_json,p.bio_zh,p.bio_en,
                      p.avatar_data_url,p.profile_visibility,p.onboarding_completed
               FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id WHERE u.id=?""",
            (user_id,),
        ).fetchone()
    if row is None:
        raise ApiProblem(HTTPStatus.NOT_FOUND, "user_not_found", "User not found.")
    return {
        "id": str(row["id"]), "email": row["email"], "name": row["display_name"],
        "isAdmin": bool(row["is_admin"]), "age": row["age"] or 18,
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
               WHERE u.is_active=1 AND p.onboarding_completed=1
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
               WHERE u.id=? AND u.is_active=1""",
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
               WHERE u.id=? AND u.is_active=1 AND p.onboarding_completed=1
                 AND p.profile_visibility='community'""",
            (requester_id,),
        ).fetchone()
        target = connection.execute(
            """SELECT u.id FROM users u JOIN user_profiles p ON p.user_id=u.id
               WHERE u.id=? AND u.is_active=1 AND p.onboarding_completed=1
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
        registered = connection.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
        online_today = connection.execute(
            """SELECT COUNT(DISTINCT e.user_id) FROM login_events e JOIN users u ON u.id=e.user_id
               WHERE e.created_at>=? AND e.created_at<? AND u.is_active=1""", (start_epoch, end_epoch)
        ).fetchone()[0]
        completed = connection.execute(
            """SELECT COUNT(*) FROM swap_requests
               WHERE status='completed' AND completed_at>=? AND completed_at<?""",
            (start_iso, end_iso),
        ).fetchone()[0]
        community_users = connection.execute(
            """SELECT COUNT(*) FROM users u JOIN user_profiles p ON p.user_id=u.id
               WHERE u.is_active=1 AND p.onboarding_completed=1 AND p.profile_visibility='community'"""
        ).fetchone()[0]
        trend_rows = connection.execute(
            """SELECT us.skill_id,COUNT(*) AS want_count
               FROM user_skills us JOIN users u ON u.id=us.user_id
               JOIN user_profiles p ON p.user_id=u.id
               WHERE us.direction='want' AND u.is_active=1 AND p.onboarding_completed=1
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


@dataclass(frozen=True)
class ServerConfig:
    db_path: Path
    skills_db_path: Path = DEFAULT_SKILLS_DB_PATH
    secure_cookie: bool = False


class SkillSwapHandler(BaseHTTPRequestHandler):
    server_version = "SkillSwapServer/2.0"
    config: ServerConfig

    def do_GET(self) -> None:  # noqa: N802
        parsed, path = urlsplit(self.path), urlsplit(self.path).path
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
                    include = params.get("includeInactive", ["false"])[0].lower() == "true"
                    if include and not user["is_admin"]:
                        raise ApiProblem(HTTPStatus.FORBIDDEN, "admin_required", "Administrator access required.")
                    category, query = params.get("category", [""])[0], params.get("q", [""])[0]
                    if category and category not in SKILL_CATEGORIES:
                        raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_category", "Unknown skill category.")
                    self._send_json(HTTPStatus.OK, {"skills": list_skills(self.config.skills_db_path, query=query, category=category, include_inactive=include)})
            elif path.startswith("/api/skills/"):
                user = self._require_user()
                if user is not None:
                    row = skill_by_id(self.config.skills_db_path, unquote(path.removeprefix("/api/skills/")))
                    if row is None or not row["is_active"] and not user["is_admin"]:
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
        if path in {"/", "/v4.2.html"}: self._send_static(APP_ROOT / "v4.2.html", "text/html; charset=utf-8", head=True)
        elif path == "/index.html": self._send_static(APP_ROOT / "index.html", "text/html; charset=utf-8", head=True)
        else: self._send_not_found(path.startswith("/api/"), head=True)

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        request_action = re.fullmatch(r"/api/swap-requests/(\d+)/(accept|reject|cancel|complete)", path)
        try:
            if path == "/api/auth/login": self._handle_login()
            elif path == "/api/auth/register": self._handle_register()
            elif path == "/api/auth/logout":
                delete_session(self.config.db_path, self._session_token())
                self.send_response(HTTPStatus.NO_CONTENT); self._send_security_headers()
                self.send_header("Set-Cookie", self._expired_cookie()); self.send_header("Cache-Control", "no-store"); self.end_headers()
            elif path == "/api/skills":
                user = self._require_admin()
                if user is not None:
                    payload = self._read_json_body()
                    if payload is not None: self._send_json(HTTPStatus.CREATED, {"skill": create_skill(self.config.skills_db_path, payload)})
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
        try:
            if path == "/api/users/me/profile":
                user, payload = self._require_user(), None
                if user is not None: payload = self._read_json_body()
                if user is not None and payload is not None: self._send_json(HTTPStatus.OK, {"profile": update_profile(self.config.db_path, user["id"], payload)})
            elif path == "/api/users/me/skills":
                user, payload = self._require_user(), None
                if user is not None: payload = self._read_json_body()
                if user is not None and payload is not None: self._send_json(HTTPStatus.OK, replace_user_skills(self.config.db_path, self.config.skills_db_path, user["id"], payload))
            elif path.startswith("/api/skills/"):
                user, payload = self._require_admin(), None
                if user is not None: payload = self._read_json_body()
                if user is not None and payload is not None:
                    sid = unquote(path.removeprefix("/api/skills/"))
                    self._send_json(HTTPStatus.OK, {"skill": update_skill(self.config.skills_db_path, sid, payload)})
            else: self._send_not_found(path.startswith("/api/"))
        except ApiProblem as problem: self._send_problem(problem)

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            if path.startswith("/api/skills/"):
                user = self._require_admin()
                if user is not None:
                    deactivate_skill(self.config.skills_db_path, unquote(path.removeprefix("/api/skills/")))
                    self.send_response(HTTPStatus.NO_CONTENT); self._send_security_headers(); self.send_header("Cache-Control", "no-store"); self.end_headers()
            else: self._send_not_found(path.startswith("/api/"))
        except ApiProblem as problem: self._send_problem(problem)

    def _handle_login(self) -> None:
        payload = self._read_json_body()
        if payload is None: return
        email, password = payload.get("email"), payload.get("password")
        if not isinstance(email, str) or not isinstance(password, str):
            raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_request", "Email and password are required.")
        user = authenticate_user(self.config.db_path, email, password)
        if user is None: raise ApiProblem(HTTPStatus.UNAUTHORIZED, "invalid_credentials", "Invalid email or password.")
        self._send_json(HTTPStatus.OK, {"user": public_user(user)}, extra_headers={"Set-Cookie": self._session_cookie(create_session(self.config.db_path, user["id"]))})

    def _handle_register(self) -> None:
        payload = self._read_json_body()
        if payload is None: return
        email, password = payload.get("email"), payload.get("password")
        if not isinstance(email, str) or not isinstance(password, str):
            raise ApiProblem(HTTPStatus.BAD_REQUEST, "invalid_request", "Email and password are required.")
        user = register_user(self.config.db_path, email, password)
        self._send_json(HTTPStatus.CREATED, {"user": public_user(user)}, extra_headers={"Set-Cookie": self._session_cookie(create_session(self.config.db_path, user["id"]))})

    def _require_user(self) -> sqlite3.Row | None:
        user = user_for_session(self.config.db_path, self._session_token())
        if user is None: self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "not_authenticated", "message": "Authentication required."})
        return user

    def _require_admin(self) -> sqlite3.Row | None:
        user = self._require_user()
        if user is not None and not user["is_admin"]: raise ApiProblem(HTTPStatus.FORBIDDEN, "admin_required", "Administrator access required.")
        return user

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

    def _session_token(self) -> str:
        try:
            morsel = SimpleCookie(self.headers.get("Cookie", "")).get(SESSION_COOKIE)
            return morsel.value if morsel else ""
        except Exception: return ""

    def _session_cookie(self, token: str) -> str:
        parts = [f"{SESSION_COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={SESSION_TTL_SECONDS}"]
        if self.config.secure_cookie: parts.append("Secure")
        return "; ".join(parts)

    def _expired_cookie(self) -> str:
        parts = [f"{SESSION_COOKIE}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
        if self.config.secure_cookie: parts.append("Secure")
        return "; ".join(parts)

    def _send_problem(self, problem: ApiProblem) -> None:
        self._send_json(problem.status, {"error": problem.code, "message": problem.message})

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any], *, extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status); self._send_security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items(): self.send_header(key, value)
        self.end_headers(); self.wfile.write(body)

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
    args = parse_args()
    admin_email = os.environ.get("SKILLSWAP_ADMIN_EMAIL", os.environ.get("SKILLSWAP_DEMO_EMAIL", "daniel@example.com"))
    admin_password = os.environ.get("SKILLSWAP_ADMIN_PASSWORD", os.environ.get("SKILLSWAP_DEMO_PASSWORD", "SkillSwap123!"))
    initialize_database(args.db, admin_email=admin_email, admin_password=admin_password)
    initialize_skill_database(args.skills_db); seed_admin_user_skills(args.db)
    config = ServerConfig(args.db, args.skills_db, os.environ.get("SKILLSWAP_SECURE_COOKIE") == "1")
    server = ThreadingHTTPServer((args.host, args.port), make_handler(config))
    print(f"SkillSwap running at http://{args.host}:{args.port}/")
    print(f"Account database: {args.db.resolve()}"); print(f"Skill database: {args.skills_db.resolve()}"); print(f"Admin login: {admin_email}")
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nStopping SkillSwap server.")
    finally: server.server_close()


if __name__ == "__main__":
    main()
