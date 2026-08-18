"""SkillSwap server with SQLite-backed email and administrator authentication.

The server intentionally uses only Python's standard library. Starting it creates
or migrates the database schema, then serves the user application, the local-only
administrator dashboard, and their isolated authentication APIs.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


APP_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = APP_ROOT / "data" / "skillswap.db"
SESSION_COOKIE = "skillswap_session"
ADMIN_SESSION_COOKIE = "skillswap_admin_session"
ADMIN_CSRF_COOKIE = "skillswap_admin_csrf"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
ADMIN_SESSION_TTL_SECONDS = 8 * 60 * 60
PBKDF2_ITERATIONS = 600_000
MAX_JSON_BODY_BYTES = 16 * 1024
CURRENT_SCHEMA_VERSION = 2
ADMIN_RATE_LIMIT_ATTEMPTS = 5
ADMIN_RATE_LIMIT_WINDOW_SECONDS = 15 * 60
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


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
) -> Path | None:
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
                "SELECT id, email, role FROM users WHERE email = ?",
                (normalized_admin_email,),
            ).fetchone()
            if existing_admin is not None and existing_admin["role"] != "superadmin":
                raise ValueError(
                    "Admin email already belongs to a regular user; refusing privilege escalation"
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
    return backup_path


def authenticate_user(
    db_path: Path, email: str, password: str
) -> sqlite3.Row | None:
    if not email or not password or len(email) > 254 or len(password) > 1024:
        return None
    with closing(connect_database(db_path)) as connection, connection:
        user = connection.execute(
            """
            SELECT id, email, password_salt, password_hash, display_name, role
            FROM users
            WHERE email = ? AND is_active = 1
            """,
            (normalize_email(email),),
        ).fetchone()
    if user is None:
        # Keep missing-account requests close to the password-check timing.
        hash_password(password, bytes(16))
        return None
    if not verify_password(password, user["password_salt"], user["password_hash"]):
        return None
    return user


def public_user(user: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": user["id"],
        "email": user["email"],
        "displayName": user["display_name"],
        "role": user["role"],
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
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with closing(connect_database(db_path)) as connection, connection:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


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
    secure_cookie: bool = False
    admin_rate_limiter: AdminLoginRateLimiter = field(
        default_factory=AdminLoginRateLimiter, compare=False
    )


class SkillSwapHandler(BaseHTTPRequestHandler):
    server_version = "SkillSwapServer/2.0"
    config: ServerConfig

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlsplit(self.path).path
        if self._is_admin_path(path):
            if not self._require_loopback(path.startswith("/api/")):
                return
            if path in {"/admin", "/admin/", "/admin.html"}:
                self._send_static(APP_ROOT / "admin.html", "text/html; charset=utf-8")
                return
            self._dispatch_admin_get(path)
            return
        if path == "/api/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/api/auth/me":
            self._handle_me()
            return
        if path in {"/", "/v4.2.html"}:
            self._send_static(APP_ROOT / "v4.2.html", "text/html; charset=utf-8")
            return
        if path == "/index.html":
            self._send_static(APP_ROOT / "index.html", "text/html; charset=utf-8")
            return
        self._send_not_found(path.startswith("/api/"))

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
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

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlsplit(self.path).path
        if path.startswith("/api/admin/"):
            if not self._require_loopback(True):
                return
            self._dispatch_admin_post(path)
            return
        if path == "/api/auth/login":
            self._handle_login()
            return
        if path == "/api/auth/logout":
            token = self._cookie_value(SESSION_COOKIE)
            delete_session(self.config.db_path, token)
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_security_headers()
            self.send_header("Set-Cookie", self._expired_cookie(SESSION_COOKIE, "Lax"))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self._send_not_found(path.startswith("/api/"))

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
        password_match = re.fullmatch(r"/api/admin/users/(\d+)/password", path)
        if password_match:
            self._handle_admin_reset_password(int(password_match.group(1)))
            return
        self._send_not_found(True)

    def _handle_login(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return
        email = payload.get("email")
        password = payload.get("password")
        if not isinstance(email, str) or not isinstance(password, str):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_request", "message": "Email and password are required."},
            )
            return
        user = authenticate_user(self.config.db_path, email, password)
        if user is None:
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "invalid_credentials", "message": "Invalid email or password."},
            )
            return
        token = create_session(self.config.db_path, user["id"])
        self._send_json(
            HTTPStatus.OK,
            {"user": public_user(user)},
            extra_headers=[("Set-Cookie", self._session_cookie(token))],
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
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_JSON_BODY_BYTES:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_request", "message": "Invalid request body."},
            )
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_json", "message": "Request body must be valid JSON."},
            )
            return None
        if not isinstance(payload, dict):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_request", "message": "Request body must be an object."},
            )
            return None
        return payload

    def _cookie_value(self, name: str) -> str:
        raw_cookie = self.headers.get("Cookie", "")
        try:
            cookie = SimpleCookie(raw_cookie)
            morsel = cookie.get(name)
            return morsel.value if morsel else ""
        except Exception:
            return ""

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
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self._send_not_found(False, head=head)
            return
        self.send_response(HTTPStatus.OK)
        self._send_security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _send_not_found(self, api_request: bool, *, head: bool = False) -> None:
        if api_request:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "not_found", "message": "Endpoint not found."},
            )
            return
        body = b"Not found"
        self.send_response(HTTPStatus.NOT_FOUND)
        self._send_security_headers()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")

    def log_message(self, format_string: str, *args: Any) -> None:
        sys.stderr.write(
            f"[{self.log_date_time_string()}] {self.address_string()} "
            f"{format_string % args}\n"
        )


def make_handler(config: ServerConfig) -> type[SkillSwapHandler]:
    class ConfiguredSkillSwapHandler(SkillSwapHandler):
        pass

    ConfiguredSkillSwapHandler.config = config
    return ConfiguredSkillSwapHandler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the SkillSwap email-login server.")
    parser.add_argument("--host", default=os.environ.get("SKILLSWAP_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("SKILLSWAP_PORT", "4173"))
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.environ.get("SKILLSWAP_DB_PATH", DEFAULT_DB_PATH)),
    )
    return parser.parse_args()


def main() -> None:
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
    )
    config = ServerConfig(
        db_path=args.db,
        secure_cookie=os.environ.get("SKILLSWAP_SECURE_COOKIE") == "1",
    )
    server = ThreadingHTTPServer((args.host, args.port), make_handler(config))
    print(f"SkillSwap running at http://{args.host}:{args.port}/")
    print(f"SQLite database: {args.db.resolve()}")
    print(f"Demo login: {demo_email}")
    if backup_path:
        print(f"Database backup created before migration: {backup_path.resolve()}")
    if admin_email:
        print(f"Admin dashboard: http://{args.host}:{args.port}/admin")
        print(f"Admin account: {normalize_email(admin_email)}")
    else:
        print("Admin dashboard disabled: set SKILLSWAP_ADMIN_EMAIL and SKILLSWAP_ADMIN_PASSWORD")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping SkillSwap server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
