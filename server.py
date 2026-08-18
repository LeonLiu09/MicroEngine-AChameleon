"""SkillSwap development server with SQLite-backed email authentication.

The server intentionally uses only Python's standard library. Starting it creates
the database schema and a demo account automatically, then serves v4.2.html and
the authentication API from the same origin.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


APP_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = APP_ROOT / "data" / "skillswap.db"
SESSION_COOKIE = "skillswap_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
PBKDF2_ITERATIONS = 600_000
MAX_JSON_BODY_BYTES = 16 * 1024


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
    demo_email: str = "daniel@example.com",
    demo_password: str = "SkillSwap123!",
) -> None:
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
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
            """
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


def authenticate_user(
    db_path: Path, email: str, password: str
) -> sqlite3.Row | None:
    if not email or not password or len(email) > 254 or len(password) > 1024:
        return None
    with closing(connect_database(db_path)) as connection, connection:
        user = connection.execute(
            """
            SELECT id, email, password_salt, password_hash, display_name
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
    }


def create_session(db_path: Path, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    now = int(time.time())
    with closing(connect_database(db_path)) as connection, connection:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        connection.execute(
            """
            INSERT INTO sessions (token_hash, user_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (token_hash, user_id, now, now + SESSION_TTL_SECONDS),
        )
    return token


def user_for_session(db_path: Path, token: str) -> sqlite3.Row | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = int(time.time())
    with closing(connect_database(db_path)) as connection, connection:
        user = connection.execute(
            """
            SELECT users.id, users.email, users.display_name
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ?
              AND sessions.expires_at > ?
              AND users.is_active = 1
            """,
            (token_hash, now),
        ).fetchone()
    return user


def delete_session(db_path: Path, token: str) -> None:
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with closing(connect_database(db_path)) as connection, connection:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


@dataclass(frozen=True)
class ServerConfig:
    db_path: Path
    secure_cookie: bool = False


class SkillSwapHandler(BaseHTTPRequestHandler):
    server_version = "SkillSwapServer/1.0"
    config: ServerConfig

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlsplit(self.path).path
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
        if path in {"/", "/v4.2.html"}:
            self._send_static(APP_ROOT / "v4.2.html", "text/html; charset=utf-8", head=True)
            return
        if path == "/index.html":
            self._send_static(APP_ROOT / "index.html", "text/html; charset=utf-8", head=True)
            return
        self._send_not_found(path.startswith("/api/"), head=True)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlsplit(self.path).path
        if path == "/api/auth/login":
            self._handle_login()
            return
        if path == "/api/auth/logout":
            token = self._session_token()
            delete_session(self.config.db_path, token)
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_security_headers()
            self.send_header("Set-Cookie", self._expired_cookie())
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self._send_not_found(path.startswith("/api/"))

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
            extra_headers={"Set-Cookie": self._session_cookie(token)},
        )

    def _handle_me(self) -> None:
        user = user_for_session(self.config.db_path, self._session_token())
        if user is None:
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "not_authenticated", "message": "Authentication required."},
            )
            return
        self._send_json(HTTPStatus.OK, {"user": public_user(user)})

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

    def _session_token(self) -> str:
        raw_cookie = self.headers.get("Cookie", "")
        try:
            cookie = SimpleCookie(raw_cookie)
            morsel = cookie.get(SESSION_COOKIE)
            return morsel.value if morsel else ""
        except Exception:
            return ""

    def _session_cookie(self, token: str) -> str:
        parts = [
            f"{SESSION_COOKIE}={token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            f"Max-Age={SESSION_TTL_SECONDS}",
        ]
        if self.config.secure_cookie:
            parts.append("Secure")
        return "; ".join(parts)

    def _expired_cookie(self) -> str:
        parts = [
            f"{SESSION_COOKIE}=",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            "Max-Age=0",
        ]
        if self.config.secure_cookie:
            parts.append("Secure")
        return "; ".join(parts)

    def _send_json(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
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
    initialize_database(args.db, demo_email=demo_email, demo_password=demo_password)
    config = ServerConfig(
        db_path=args.db,
        secure_cookie=os.environ.get("SKILLSWAP_SECURE_COOKIE") == "1",
    )
    server = ThreadingHTTPServer((args.host, args.port), make_handler(config))
    print(f"SkillSwap running at http://{args.host}:{args.port}/")
    print(f"SQLite database: {args.db.resolve()}")
    print(f"Demo login: {demo_email}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping SkillSwap server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
