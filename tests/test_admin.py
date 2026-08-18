from contextlib import closing
import hashlib
import http.client
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

import server


ADMIN_EMAIL = "root@example.com"
ADMIN_PASSWORD = "VerySecure123!"
ORIGIN = "http://127.0.0.1"


def contains_sensitive_key(value):
    sensitive = {
        "password_hash",
        "password_salt",
        "token_hash",
        "csrf_token_hash",
        "passwordHash",
        "passwordSalt",
        "tokenHash",
        "csrfTokenHash",
    }
    if isinstance(value, dict):
        return bool(sensitive.intersection(value)) or any(
            contains_sensitive_key(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(contains_sensitive_key(item) for item in value)
    return False


class AdminMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "legacy.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_legacy_database(self):
        salt, digest = server.hash_password("LegacyPassword!")
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                """
            )
            connection.execute(
                """
                INSERT INTO users
                    (email, password_salt, password_hash, display_name, created_at)
                VALUES ('legacy@example.com', ?, ?, 'Legacy', '2026-08-01T00:00:00Z')
                """,
                (salt, digest),
            )

    def test_legacy_database_is_backed_up_and_migrated_without_password_reset(self):
        self.create_legacy_database()
        backup = server.initialize_database(
            self.db_path,
            admin_email=ADMIN_EMAIL,
            admin_password=ADMIN_PASSWORD,
        )
        self.assertIsNotNone(backup)
        self.assertTrue(backup.exists())
        with closing(sqlite3.connect(backup)) as connection:
            legacy_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(users)")
            }
        self.assertNotIn("role", legacy_columns)

        legacy = server.authenticate_user(
            self.db_path, "legacy@example.com", "LegacyPassword!"
        )
        admin = server.authenticate_user(self.db_path, ADMIN_EMAIL, ADMIN_PASSWORD)
        self.assertEqual(legacy["role"], "user")
        self.assertEqual(admin["role"], "superadmin")
        with closing(sqlite3.connect(self.db_path)) as connection:
            session_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(sessions)")
            }
            versions = {
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            }
        self.assertTrue(
            {"public_id", "purpose", "csrf_token_hash"}.issubset(session_columns)
        )
        self.assertIn(server.CURRENT_SCHEMA_VERSION, versions)
        self.assertIsNone(
            server.initialize_database(
                self.db_path,
                admin_email=ADMIN_EMAIL,
                admin_password="DifferentPassword123!",
            )
        )
        self.assertIsNotNone(
            server.authenticate_user(self.db_path, ADMIN_EMAIL, ADMIN_PASSWORD)
        )
        self.assertIsNone(
            server.authenticate_user(
                self.db_path, ADMIN_EMAIL, "DifferentPassword123!"
            )
        )

    def test_admin_bootstrap_configuration_is_strict(self):
        with self.assertRaisesRegex(ValueError, "must be set together"):
            server.initialize_database(self.db_path, admin_email=ADMIN_EMAIL)

        other_path = Path(self.temp_dir.name) / "conflict.db"
        server.initialize_database(other_path, demo_email=ADMIN_EMAIL)
        with self.assertRaisesRegex(ValueError, "refusing privilege escalation"):
            server.initialize_database(
                other_path,
                demo_email=ADMIN_EMAIL,
                admin_email=ADMIN_EMAIL,
                admin_password=ADMIN_PASSWORD,
            )

    def test_loopback_detection_does_not_trust_remote_addresses(self):
        self.assertTrue(server.is_loopback_address("127.0.0.1"))
        self.assertTrue(server.is_loopback_address("::1"))
        self.assertTrue(server.is_loopback_address("::ffff:127.0.0.1"))
        self.assertFalse(server.is_loopback_address("192.168.1.20"))
        self.assertFalse(server.is_loopback_address("203.0.113.10"))


class AdminHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "admin-http.db"
        server.initialize_database(
            self.db_path,
            admin_email=ADMIN_EMAIL,
            admin_password=ADMIN_PASSWORD,
            admin_name="Root Admin",
        )
        config = server.ServerConfig(db_path=self.db_path)
        self.httpd = server.ThreadingHTTPServer(
            ("127.0.0.1", 0), server.make_handler(config)
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.httpd.server_address
        self.origin = f"http://{self.host}:{self.port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def request(self, method, path, payload=None, cookie="", csrf="", origin=None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=10)
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload)
            headers["Content-Type"] = "application/json"
        elif method in {"POST", "PATCH", "DELETE"}:
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        if csrf:
            headers["X-CSRF-Token"] = csrf
        if origin is not None:
            headers["Origin"] = origin
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            response_headers = response.getheaders()
            raw = response.read()
            if not raw:
                parsed = None
            else:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = raw.decode("utf-8")
            return response.status, response_headers, parsed
        finally:
            connection.close()

    def login_admin(self):
        status, headers, body = self.request(
            "POST",
            "/api/admin/auth/login",
            {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        self.assertEqual(status, 200, body)
        cookies = {}
        for key, value in headers:
            if key.lower() == "set-cookie":
                name_value = value.split(";", 1)[0]
                name, cookie_value = name_value.split("=", 1)
                cookies[name] = cookie_value
        cookie_header = "; ".join(f"{key}={value}" for key, value in cookies.items())
        return cookie_header, cookies[server.ADMIN_CSRF_COOKIE], body

    def admin_request(self, method, path, payload=None, cookie=None, csrf=None):
        if cookie is None or csrf is None:
            cookie, csrf, _ = self.login_admin()
        return self.request(
            method,
            path,
            payload,
            cookie=cookie,
            csrf=csrf,
            origin=self.origin,
        )

    def test_admin_page_login_and_session_are_isolated_and_safe(self):
        status, _, page = self.request("GET", "/admin")
        self.assertEqual(status, 200)
        self.assertIn("管理员登录", page if isinstance(page, str) else "")

        status, _, rejected = self.request(
            "POST",
            "/api/admin/auth/login",
            {"email": "daniel@example.com", "password": "SkillSwap123!"},
        )
        self.assertEqual(status, 401)
        self.assertEqual(rejected["error"], "invalid_credentials")

        cookie, csrf, login = self.login_admin()
        self.assertFalse(contains_sensitive_key(login))
        self.assertTrue(csrf)
        status, _, me = self.request(
            "GET", "/api/admin/auth/me", cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(me["admin"]["role"], "superadmin")
        self.assertFalse(contains_sensitive_key(me))

        admin_session_cookie = next(
            item
            for item in cookie.split("; ")
            if item.startswith(f"{server.ADMIN_SESSION_COOKIE}=")
        )
        status, _, body = self.request(
            "GET", "/api/auth/me", cookie=admin_session_cookie
        )
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "not_authenticated")

        status, _, _ = self.admin_request(
            "POST",
            "/api/admin/auth/logout",
            {},
            cookie,
            csrf,
        )
        self.assertEqual(status, 200)
        status, _, _ = self.request("GET", "/api/admin/auth/me", cookie=cookie)
        self.assertEqual(status, 401)

    def test_csrf_origin_and_self_protection(self):
        cookie, csrf, login = self.login_admin()
        root_id = login["admin"]["id"]
        status, _, body = self.request(
            "POST",
            "/api/admin/users",
            {
                "email": "person@example.com",
                "displayName": "Person",
                "password": "Password123!",
                "role": "user",
                "isActive": True,
            },
            cookie=cookie,
            origin=self.origin,
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "invalid_csrf")

        status, _, body = self.request(
            "POST",
            "/api/admin/users",
            {
                "email": "person@example.com",
                "displayName": "Person",
                "password": "Password123!",
                "role": "user",
                "isActive": True,
            },
            cookie=cookie,
            csrf=csrf,
            origin="http://evil.example",
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "invalid_origin")

        status, _, body = self.admin_request(
            "PATCH",
            f"/api/admin/users/{root_id}",
            {"role": "user"},
            cookie,
            csrf,
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "self_protection")
        status, _, body = self.admin_request(
            "DELETE",
            f"/api/admin/users/{root_id}",
            cookie=cookie,
            csrf=csrf,
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "self_protection")

    def test_full_user_crud_session_revoke_and_audit_flow(self):
        cookie, csrf, _ = self.login_admin()
        status, _, created = self.admin_request(
            "POST",
            "/api/admin/users",
            {
                "email": "new.user@example.com",
                "displayName": "New User",
                "password": "Password123!",
                "role": "user",
                "isActive": True,
            },
            cookie,
            csrf,
        )
        self.assertEqual(status, 201, created)
        self.assertFalse(contains_sensitive_key(created))
        user_id = created["user"]["id"]

        status, _, updated = self.admin_request(
            "PATCH",
            f"/api/admin/users/{user_id}",
            {"displayName": "Updated User", "isActive": True},
            cookie,
            csrf,
        )
        self.assertEqual(status, 200, updated)
        self.assertEqual(updated["user"]["displayName"], "Updated User")

        status, login_headers, _ = self.request(
            "POST",
            "/api/auth/login",
            {"email": "new.user@example.com", "password": "Password123!"},
        )
        self.assertEqual(status, 200)
        user_cookie = next(
            value.split(";", 1)[0]
            for key, value in login_headers
            if key.lower() == "set-cookie"
        )

        status, _, sessions = self.request(
            "GET", "/api/admin/sessions?purpose=user", cookie=cookie
        )
        self.assertEqual(status, 200)
        target_session = next(
            item for item in sessions["items"] if item["userId"] == user_id
        )
        self.assertNotIn("token", json.dumps(target_session).lower())
        status, _, _ = self.admin_request(
            "DELETE",
            f"/api/admin/sessions/{target_session['publicId']}",
            cookie=cookie,
            csrf=csrf,
        )
        self.assertEqual(status, 200)
        status, _, _ = self.request("GET", "/api/auth/me", cookie=user_cookie)
        self.assertEqual(status, 401)

        status, second_login_headers, _ = self.request(
            "POST",
            "/api/auth/login",
            {"email": "new.user@example.com", "password": "Password123!"},
        )
        self.assertEqual(status, 200)
        second_user_cookie = next(
            value.split(";", 1)[0]
            for key, value in second_login_headers
            if key.lower() == "set-cookie"
        )

        status, _, reset_result = self.admin_request(
            "POST",
            f"/api/admin/users/{user_id}/password",
            {"password": "ChangedPassword123!"},
            cookie,
            csrf,
        )
        self.assertEqual(status, 200)
        self.assertEqual(reset_result["revokedSessions"], 1)
        status, _, _ = self.request(
            "GET", "/api/auth/me", cookie=second_user_cookie
        )
        self.assertEqual(status, 401)
        self.assertIsNotNone(
            server.authenticate_user(
                self.db_path, "new.user@example.com", "ChangedPassword123!"
            )
        )

        status, _, filtered = self.request(
            "GET",
            "/api/admin/users?query=updated&role=user&status=active&pageSize=1",
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["pageSize"], 1)

        status, _, _ = self.admin_request(
            "DELETE",
            f"/api/admin/users/{user_id}",
            cookie=cookie,
            csrf=csrf,
        )
        self.assertEqual(status, 200)
        status, _, audit = self.request(
            "GET", "/api/admin/audit-logs?query=user.", cookie=cookie
        )
        self.assertEqual(status, 200)
        actions = {item["action"] for item in audit["items"]}
        self.assertTrue(
            {"user.create", "user.update", "user.password_reset", "user.delete"}.issubset(
                actions
            )
        )
        self.assertFalse(contains_sensitive_key(audit))

    def test_other_superadmins_can_be_managed_with_password_policy(self):
        cookie, csrf, _ = self.login_admin()
        status, _, too_short = self.admin_request(
            "POST",
            "/api/admin/users",
            {
                "email": "admin.two@example.com",
                "displayName": "Admin Two",
                "password": "short123",
                "role": "superadmin",
                "isActive": True,
            },
            cookie,
            csrf,
        )
        self.assertEqual(status, 422)
        self.assertEqual(too_short["error"], "invalid_password")

        status, _, created = self.admin_request(
            "POST",
            "/api/admin/users",
            {
                "email": "admin.two@example.com",
                "displayName": "Admin Two",
                "password": "AdminPassword123!",
                "role": "superadmin",
                "isActive": True,
            },
            cookie,
            csrf,
        )
        self.assertEqual(status, 201)
        second_id = created["user"]["id"]
        status, _, updated = self.admin_request(
            "PATCH",
            f"/api/admin/users/{second_id}",
            {"isActive": False},
            cookie,
            csrf,
        )
        self.assertEqual(status, 200)
        self.assertFalse(updated["user"]["isActive"])
        status, _, _ = self.admin_request(
            "DELETE",
            f"/api/admin/users/{second_id}",
            cookie=cookie,
            csrf=csrf,
        )
        self.assertEqual(status, 200)

    def test_current_admin_session_cannot_be_revoked_from_session_list(self):
        cookie, csrf, _ = self.login_admin()
        status, _, sessions = self.request(
            "GET", "/api/admin/sessions?purpose=admin", cookie=cookie
        )
        current = next(item for item in sessions["items"] if item["isCurrent"])
        status, _, body = self.admin_request(
            "DELETE",
            f"/api/admin/sessions/{current['publicId']}",
            cookie=cookie,
            csrf=csrf,
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "current_session")

    def test_rate_limit_blocks_sixth_failed_login(self):
        for _ in range(server.ADMIN_RATE_LIMIT_ATTEMPTS):
            status, _, _ = self.request(
                "POST",
                "/api/admin/auth/login",
                {"email": ADMIN_EMAIL, "password": "wrong-password"},
            )
            self.assertEqual(status, 401)
        status, _, body = self.request(
            "POST",
            "/api/admin/auth/login",
            {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        self.assertEqual(status, 429)
        self.assertEqual(body["error"], "rate_limited")

    def test_expired_admin_session_is_rejected(self):
        cookie, _, _ = self.login_admin()
        token = next(
            item.split("=", 1)[1]
            for item in cookie.split("; ")
            if item.startswith(f"{server.ADMIN_SESSION_COOKIE}=")
        )
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with closing(server.connect_database(self.db_path)) as connection, connection:
            connection.execute(
                "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                (int(time.time()) - 1, token_hash),
            )
        status, _, body = self.request(
            "GET", "/api/admin/auth/me", cookie=cookie
        )
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "not_authenticated")


if __name__ == "__main__":
    unittest.main()
