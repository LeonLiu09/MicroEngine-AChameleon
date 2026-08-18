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
from unittest import mock

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

    def test_local_is_admin_database_preserves_business_data(self):
        salt, digest = server.hash_password("LegacyAdmin123!")
        member_salt, member_digest = server.hash_password("MemberPassword123!")
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE user_profiles (
                    user_id INTEGER PRIMARY KEY,
                    age INTEGER NOT NULL DEFAULT 18,
                    country_id TEXT NOT NULL DEFAULT '',
                    city_id TEXT NOT NULL DEFAULT '',
                    languages_json TEXT NOT NULL DEFAULT '[]',
                    bio_zh TEXT NOT NULL DEFAULT '',
                    bio_en TEXT NOT NULL DEFAULT '',
                    avatar_data_url TEXT NOT NULL DEFAULT '',
                    profile_visibility TEXT NOT NULL DEFAULT 'community',
                    onboarding_completed INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE user_skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    skill_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    level TEXT NOT NULL,
                    description_zh TEXT NOT NULL DEFAULT '',
                    description_en TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (user_id, skill_id, direction),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE swap_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    requester_id INTEGER NOT NULL,
                    target_user_id INTEGER NOT NULL,
                    offered_skill_id TEXT NOT NULL,
                    requested_skill_id TEXT NOT NULL,
                    meeting_policy TEXT NOT NULL DEFAULT 'flexible',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    accepted_at TEXT,
                    rejected_at TEXT,
                    cancelled_at TEXT,
                    requester_completed_at TEXT,
                    target_completed_at TEXT,
                    completed_at TEXT,
                    FOREIGN KEY (requester_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (target_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                """
            )
            connection.execute(
                """INSERT INTO users
                   (email,password_salt,password_hash,display_name,is_admin,created_at)
                   VALUES ('legacy-admin@example.com',?,?, 'Legacy Admin',1,'2026-08-01T00:00:00Z')""",
                (salt, digest),
            )
            connection.execute(
                """INSERT INTO users
                   (email,password_salt,password_hash,display_name,is_admin,created_at)
                   VALUES ('member@example.com',?,?, 'Member',0,'2026-08-02T00:00:00Z')""",
                (member_salt, member_digest),
            )
            connection.execute(
                """INSERT INTO user_profiles
                   (user_id,age,country_id,city_id,languages_json,bio_zh,
                    onboarding_completed,updated_at)
                   VALUES (1,22,'cn','tianjin','["zh"]','保留资料',1,'2026-08-03T00:00:00Z')"""
            )
            connection.execute(
                """INSERT INTO user_skills
                   (user_id,skill_id,direction,level,created_at,updated_at)
                   VALUES (1,'python','offer','advanced','2026-08-03T00:00:00Z','2026-08-03T00:00:00Z')"""
            )
            connection.execute(
                """INSERT INTO swap_requests
                   (requester_id,target_user_id,offered_skill_id,requested_skill_id,
                    status,created_at)
                   VALUES (1,2,'python','cooking','pending','2026-08-04T00:00:00Z')"""
            )

        backup = server.initialize_database(self.db_path)
        self.assertIsNotNone(backup)
        with closing(server.connect_database(self.db_path)) as connection:
            admin = connection.execute(
                "SELECT role FROM users WHERE email = 'legacy-admin@example.com'"
            ).fetchone()
            user_columns = server._column_names(connection, "users")
            profile = connection.execute(
                "SELECT bio_zh FROM user_profiles WHERE user_id = 1"
            ).fetchone()
            skill_count = connection.execute("SELECT COUNT(*) FROM user_skills").fetchone()[0]
            request_count = connection.execute("SELECT COUNT(*) FROM swap_requests").fetchone()[0]
        self.assertEqual(admin["role"], "superadmin")
        self.assertIn("is_admin", user_columns)
        self.assertEqual(profile["bio_zh"], "保留资料")
        self.assertEqual(skill_count, 1)
        self.assertEqual(request_count, 1)

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

    def test_admin_sync_updates_the_single_configured_admin(self):
        server.initialize_database(
            self.db_path,
            admin_email=ADMIN_EMAIL,
            admin_password=ADMIN_PASSWORD,
            admin_name="Old Name",
        )
        new_email = "new-root@example.com"
        new_password = "DifferentPassword123!"
        server.initialize_database(
            self.db_path,
            admin_email=new_email,
            admin_password=new_password,
            admin_name="New Name",
            admin_sync=True,
        )

        self.assertIsNone(
            server.authenticate_user(self.db_path, ADMIN_EMAIL, ADMIN_PASSWORD)
        )
        admin = server.authenticate_user(self.db_path, new_email, new_password)
        self.assertIsNotNone(admin)
        self.assertEqual(admin["display_name"], "New Name")
        with closing(server.connect_database(self.db_path)) as connection:
            audit = connection.execute(
                """
                SELECT action, details_json FROM admin_audit_log
                WHERE action = 'admin.config_sync' ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        self.assertEqual(audit["action"], "admin.config_sync")
        details = json.loads(audit["details_json"])
        self.assertEqual(details["email"], new_email)
        self.assertEqual(
            set(details["changedFields"]), {"email", "displayName", "password"}
        )
        self.assertNotIn(new_password, audit["details_json"])

    def test_admin_sync_refuses_to_guess_between_multiple_admins(self):
        server.initialize_database(
            self.db_path,
            admin_email=ADMIN_EMAIL,
            admin_password=ADMIN_PASSWORD,
        )
        server.initialize_database(
            self.db_path,
            admin_email="second-root@example.com",
            admin_password="SecondSecure123!",
        )
        with self.assertRaisesRegex(ValueError, "multiple superadmins"):
            server.initialize_database(
                self.db_path,
                admin_email="unknown-root@example.com",
                admin_password="UnknownSecure123!",
                admin_sync=True,
            )

    def test_loopback_detection_does_not_trust_remote_addresses(self):
        self.assertTrue(server.is_loopback_address("127.0.0.1"))
        self.assertTrue(server.is_loopback_address("::1"))
        self.assertTrue(server.is_loopback_address("::ffff:127.0.0.1"))
        self.assertFalse(server.is_loopback_address("192.168.1.20"))
        self.assertFalse(server.is_loopback_address("203.0.113.10"))


class DotenvConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dotenv_path = Path(self.temp_dir.name) / ".env"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dotenv_loads_project_settings_and_preserves_process_environment(self):
        self.dotenv_path.write_text(
            """
            # local settings
            export SKILLSWAP_ADMIN_EMAIL=from-file@example.com
            SKILLSWAP_ADMIN_NAME='本地 管理员'
            SKILLSWAP_ADMIN_PASSWORD="Secure\\tPassword123!" # comment
            SKILLSWAP_PORT=4174 # comment
            OTHER_SETTING=ignored
            """,
            encoding="utf-8",
        )
        environment = {"SKILLSWAP_ADMIN_EMAIL": "from-process@example.com"}
        loaded = server.load_dotenv(self.dotenv_path, environ=environment)

        self.assertEqual(loaded["SKILLSWAP_ADMIN_EMAIL"], "from-file@example.com")
        self.assertEqual(environment["SKILLSWAP_ADMIN_EMAIL"], "from-process@example.com")
        self.assertEqual(environment["SKILLSWAP_ADMIN_NAME"], "本地 管理员")
        self.assertEqual(environment["SKILLSWAP_ADMIN_PASSWORD"], "Secure\tPassword123!")
        self.assertEqual(environment["SKILLSWAP_PORT"], "4174")
        self.assertNotIn("OTHER_SETTING", environment)

    def test_dotenv_rejects_malformed_lines(self):
        self.dotenv_path.write_text("SKILLSWAP_ADMIN_EMAIL\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "line 1"):
            server.load_dotenv(self.dotenv_path, environ={})

    def test_environment_flags_accept_explicit_values(self):
        with mock.patch.dict("os.environ", {"SKILLSWAP_ADMIN_SYNC": "yes"}):
            self.assertTrue(server.env_flag("SKILLSWAP_ADMIN_SYNC"))
        with mock.patch.dict("os.environ", {"SKILLSWAP_ADMIN_SYNC": "invalid"}):
            with self.assertRaisesRegex(ValueError, "must be one of"):
                server.env_flag("SKILLSWAP_ADMIN_SYNC")


class AdminHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "admin-http.db"
        self.skills_db_path = Path(self.temp_dir.name) / "skills-http.db"
        server.initialize_database(
            self.db_path,
            admin_email=ADMIN_EMAIL,
            admin_password=ADMIN_PASSWORD,
            admin_name="Root Admin",
        )
        server.initialize_skill_database(self.skills_db_path)
        config = server.ServerConfig(
            db_path=self.db_path, skills_db_path=self.skills_db_path
        )
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

    def request(
        self, method, path, payload=None, cookie="", csrf="", origin=None, tab_id=""
    ):
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
        if tab_id:
            headers[server.ADMIN_TAB_HEADER] = tab_id
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

    def login_admin(
        self, email=ADMIN_EMAIL, password=ADMIN_PASSWORD, tab_id=""
    ):
        status, headers, body = self.request(
            "POST",
            "/api/admin/auth/login",
            {"email": email, "password": password},
            tab_id=tab_id,
        )
        self.assertEqual(status, 200, body)
        cookies = {}
        for key, value in headers:
            if key.lower() == "set-cookie":
                name_value = value.split(";", 1)[0]
                name, cookie_value = name_value.split("=", 1)
                cookies[name] = cookie_value
        cookie_header = "; ".join(f"{key}={value}" for key, value in cookies.items())
        csrf_cookie_name = (
            f"{server.ADMIN_CSRF_COOKIE}_{tab_id}"
            if tab_id
            else server.ADMIN_CSRF_COOKIE
        )
        return cookie_header, cookies[csrf_cookie_name], body

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
        self.assertIn('data-view="skills"', page if isinstance(page, str) else "")

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

    def test_admin_tabs_keep_independent_sessions_and_csrf(self):
        second = server.register_user(
            self.db_path, "second-admin@example.com", "SecondAdmin123!"
        )
        with closing(server.connect_database(self.db_path)) as connection, connection:
            connection.execute(
                "UPDATE users SET role = 'superadmin' WHERE id = ?", (second["id"],)
            )

        first_tab = "c" * 32
        second_tab = "d" * 32
        first_cookie, first_csrf, first_login = self.login_admin(tab_id=first_tab)
        second_cookie, second_csrf, second_login = self.login_admin(
            "second-admin@example.com", "SecondAdmin123!", second_tab
        )
        all_cookies = f"{first_cookie}; {second_cookie}"
        self.assertNotEqual(first_login["admin"]["email"], second_login["admin"]["email"])

        status, _, first_me = self.request(
            "GET", "/api/admin/auth/me", cookie=all_cookies, tab_id=first_tab
        )
        self.assertEqual(status, 200)
        status, _, second_me = self.request(
            "GET", "/api/admin/auth/me", cookie=all_cookies, tab_id=second_tab
        )
        self.assertEqual(status, 200)
        self.assertNotEqual(first_me["admin"]["email"], second_me["admin"]["email"])

        status, _, _ = self.request(
            "POST",
            "/api/admin/auth/logout",
            {},
            cookie=all_cookies,
            csrf=first_csrf,
            origin=self.origin,
            tab_id=first_tab,
        )
        self.assertEqual(status, 200)
        status, _, _ = self.request(
            "GET", "/api/admin/auth/me", cookie=all_cookies, tab_id=first_tab
        )
        self.assertEqual(status, 401)
        status, _, second_me = self.request(
            "GET", "/api/admin/auth/me", cookie=all_cookies, tab_id=second_tab
        )
        self.assertEqual(status, 200)
        self.assertEqual(second_me["admin"]["email"], "second-admin@example.com")

        status, _, _ = self.request(
            "POST",
            "/api/admin/auth/logout",
            {},
            cookie=all_cookies,
            csrf=second_csrf,
            origin=self.origin,
            tab_id=second_tab,
        )
        self.assertEqual(status, 200)

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

    def test_admin_skill_crud_soft_delete_restore_and_audit(self):
        cookie, csrf, _ = self.login_admin()
        status, _, body = self.admin_request(
            "GET", "/api/admin/skills", cookie=cookie, csrf=csrf
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body["skills"]), len(server.SEED_SKILLS))

        payload = {
            "id": "woodworking",
            "category": "creative",
            "names": {"zh": "木工", "en": "Woodworking"},
        }
        status, _, body = self.admin_request(
            "POST", "/api/admin/skills", payload, cookie, csrf
        )
        self.assertEqual(status, 201, body)
        self.assertTrue(body["skill"]["isActive"])

        updated = {
            **payload,
            "names": {"zh": "基础木工", "en": "Practical Woodworking"},
        }
        status, _, body = self.request(
            "PUT",
            "/api/admin/skills/woodworking",
            updated,
            cookie=cookie,
            csrf=csrf,
            origin=self.origin,
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["skill"]["names"]["zh"], "基础木工")

        status, _, body = self.admin_request(
            "DELETE", "/api/admin/skills/woodworking", cookie=cookie, csrf=csrf
        )
        self.assertEqual(status, 204, body)
        status, _, body = self.admin_request(
            "GET", "/api/admin/skills?query=wood", cookie=cookie, csrf=csrf
        )
        self.assertEqual(status, 200, body)
        self.assertFalse(body["skills"][0]["isActive"])

        updated["isActive"] = True
        status, _, body = self.request(
            "PUT",
            "/api/admin/skills/woodworking",
            updated,
            cookie=cookie,
            csrf=csrf,
            origin=self.origin,
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(body["skill"]["isActive"])

        status, _, body = self.admin_request(
            "GET", "/api/admin/audit-logs?query=skill.", cookie=cookie, csrf=csrf
        )
        self.assertEqual(status, 200, body)
        actions = {item["action"] for item in body["items"]}
        self.assertTrue(
            {"skill.create", "skill.update", "skill.deactivate"}.issubset(actions)
        )

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
