from contextlib import closing
from datetime import datetime, timezone
import http.client
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

import server


class AuthenticationDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "nested" / "skillswap.db"
        server.initialize_database(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_startup_creates_schema_and_demo_account(self):
        self.assertTrue(self.db_path.exists())
        with closing(sqlite3.connect(self.db_path)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            account = connection.execute(
                "SELECT email, password_hash, role FROM users WHERE email = ?",
                ("daniel@example.com",),
            ).fetchone()
        self.assertTrue({"users", "sessions", "login_events", "swap_requests"}.issubset(tables))
        self.assertEqual(account[0], "daniel@example.com")
        self.assertNotEqual(account[1], "SkillSwap123!")
        self.assertEqual(account[2], "user")

    def test_database_initialization_is_idempotent(self):
        server.initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        self.assertEqual(count, 1)

    def test_v4_migration_creates_backup_and_preserves_existing_data(self):
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("DROP TABLE chat_messages")
            connection.execute(
                "UPDATE users SET display_name='Preserved Daniel' WHERE email='daniel@example.com'"
            )
        backup = server.initialize_database(self.db_path)
        self.assertIsNotNone(backup)
        self.assertTrue(backup.exists())
        with closing(sqlite3.connect(self.db_path)) as connection:
            name = connection.execute(
                "SELECT display_name FROM users WHERE email='daniel@example.com'"
            ).fetchone()[0]
            chat_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_messages'"
            ).fetchone()
            version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
        self.assertEqual(name, "Preserved Daniel")
        self.assertIsNotNone(chat_table)
        self.assertEqual(version, 4)

    def test_valid_email_login_is_case_insensitive(self):
        user = server.authenticate_user(
            self.db_path, "  DANIEL@example.com ", "SkillSwap123!"
        )
        self.assertIsNotNone(user)
        self.assertEqual(user["display_name"], "Daniel Liu")

    def test_invalid_password_is_rejected(self):
        user = server.authenticate_user(
            self.db_path, "daniel@example.com", "not-the-password"
        )
        self.assertIsNone(user)

    def test_session_can_be_created_resolved_and_deleted(self):
        user = server.authenticate_user(
            self.db_path, "daniel@example.com", "SkillSwap123!"
        )
        token = server.create_session(self.db_path, user["id"])
        self.assertEqual(
            server.user_for_session(self.db_path, token)["email"],
            "daniel@example.com",
        )
        server.delete_session(self.db_path, token)
        self.assertIsNone(server.user_for_session(self.db_path, token))


class AuthenticationHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "skillswap-http.db"
        self.skills_db_path = Path(self.temp_dir.name) / "skills-http.db"
        server.initialize_database(self.db_path)
        server.initialize_skill_database(self.skills_db_path)
        server.seed_admin_user_skills(self.db_path)
        config = server.ServerConfig(
            db_path=self.db_path, skills_db_path=self.skills_db_path
        )
        self.httpd = server.ThreadingHTTPServer(
            ("127.0.0.1", 0), server.make_handler(config)
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.httpd.server_address

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_login_me_and_logout_http_flow(self):
        body = json.dumps(
            {"email": "daniel@example.com", "password": "SkillSwap123!"}
        )
        status, headers, response_body = self.request(
            "POST",
            "/api/auth/login",
            body,
            {"Content-Type": "application/json"},
        )
        payload = json.loads(response_body)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertEqual(status, 200)
        self.assertEqual(payload["user"]["email"], "daniel@example.com")
        self.assertIn("HttpOnly", headers["Set-Cookie"])

        status, _, response_body = self.request(
            "GET", "/api/auth/me", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response_body)["user"]["displayName"], "Daniel Liu")

        status, _, _ = self.request(
            "POST", "/api/auth/logout", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 204)
        status, _, _ = self.request("GET", "/api/auth/me", headers={"Cookie": cookie})
        self.assertEqual(status, 401)
        status, _, response_body = self.request("GET", "/api/community/stats")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response_body)["onlineToday"], 1)

    def test_invalid_password_returns_generic_unauthorized_error(self):
        body = json.dumps({"email": "daniel@example.com", "password": "wrong"})
        status, _, response_body = self.request(
            "POST",
            "/api/auth/login",
            body,
            {"Content-Type": "application/json"},
        )
        payload = json.loads(response_body)
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "invalid_credentials")

    def test_tabs_keep_independent_user_sessions(self):
        server.register_user(self.db_path, "second@example.com", "Password123!")
        first_tab = "a" * 32
        second_tab = "b" * 32

        status, first_headers, _ = self.request(
            "POST",
            "/api/auth/login",
            json.dumps(
                {"email": "daniel@example.com", "password": "SkillSwap123!"}
            ),
            {"Content-Type": "application/json", server.USER_TAB_HEADER: first_tab},
        )
        self.assertEqual(status, 200)
        first_cookie = first_headers["Set-Cookie"].split(";", 1)[0]
        self.assertTrue(first_cookie.startswith(f"{server.SESSION_COOKIE}_{first_tab}="))

        status, second_headers, _ = self.request(
            "POST",
            "/api/auth/login",
            json.dumps(
                {"email": "second@example.com", "password": "Password123!"}
            ),
            {"Content-Type": "application/json", server.USER_TAB_HEADER: second_tab},
        )
        self.assertEqual(status, 200)
        second_cookie = second_headers["Set-Cookie"].split(";", 1)[0]
        all_cookies = f"{first_cookie}; {second_cookie}"

        status, _, body = self.request(
            "GET",
            "/api/auth/me",
            headers={"Cookie": all_cookies, server.USER_TAB_HEADER: first_tab},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["user"]["email"], "daniel@example.com")

        status, _, body = self.request(
            "GET",
            "/api/auth/me",
            headers={"Cookie": all_cookies, server.USER_TAB_HEADER: second_tab},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["user"]["email"], "second@example.com")

        status, _, _ = self.request(
            "POST",
            "/api/auth/logout",
            headers={"Cookie": all_cookies, server.USER_TAB_HEADER: first_tab},
        )
        self.assertEqual(status, 204)
        status, _, _ = self.request(
            "GET",
            "/api/auth/me",
            headers={"Cookie": all_cookies, server.USER_TAB_HEADER: first_tab},
        )
        self.assertEqual(status, 401)
        status, _, body = self.request(
            "GET",
            "/api/auth/me",
            headers={"Cookie": all_cookies, server.USER_TAB_HEADER: second_tab},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["user"]["email"], "second@example.com")

    def test_root_serves_backend_integrated_v42_frontend(self):
        status, _, response_body = self.request("GET", "/")
        page = response_body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn('SkillSwap v5.0', page)
        self.assertIn('registerWithEmail', page)
        self.assertIn('/api/search?', page)
        self.assertIn('admin-dashboard', page)
        self.assertIn('/api/community/stats', page)
        self.assertIn('loadSwapRequests', page)
        self.assertIn('SWAP_REQUEST_POLL_INTERVAL_MS = 5000', page)
        self.assertIn('window.setInterval(syncSwapRequests,SWAP_REQUEST_POLL_INTERVAL_MS)', page)
        self.assertIn('document.addEventListener("visibilitychange",onVisibilityChange)', page)
        self.assertIn('refreshSwapRequests({silent:true})', page)
        self.assertIn('/api/chat/events?', page)
        self.assertIn('sendBackendChatMessage', page)
        self.assertIn('chatConnectionText', page)
        self.assertIn('const canChat=["accepted","completed"].includes(request.status)', page)
        self.assertNotIn('CHAT_REPLIES', page)
        self.assertNotIn('MOCK_USERS', page)
        self.assertNotIn('DEMO_USER', page)

        status, _, response_body = self.request("GET", "/index.html")
        legacy_page = response_body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn('v4.2.html', legacy_page)
        self.assertNotIn('MOCK_USERS', legacy_page)


class SkillDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.account_db = root / "accounts.db"
        self.skills_db = root / "skills.db"
        server.initialize_database(self.account_db)
        server.initialize_skill_database(self.skills_db)
        server.seed_admin_user_skills(self.account_db)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_skill_database_is_separate_seeded_and_idempotent(self):
        self.assertNotEqual(self.account_db, self.skills_db)
        server.initialize_skill_database(self.skills_db)
        with closing(sqlite3.connect(self.skills_db)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
        with closing(sqlite3.connect(self.account_db)) as connection:
            skill_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='skills'"
            ).fetchone()
        self.assertEqual(count, 28)
        self.assertIsNone(skill_table)

    def test_bilingual_skill_search_and_soft_delete(self):
        self.assertEqual(
            server.list_skills(self.skills_db, query="PY")[0]["id"], "python"
        )
        self.assertEqual(
            server.list_skills(self.skills_db, query="摄影")[0]["id"],
            "photography",
        )
        server.deactivate_skill(self.skills_db, "python")
        self.assertEqual(server.list_skills(self.skills_db, query="python"), [])
        inactive = server.list_skills(
            self.skills_db, query="python", include_inactive=True
        )
        self.assertFalse(inactive[0]["isActive"])

    def test_replacing_user_skills_rejects_inactive_skill(self):
        user = server.register_user(self.account_db, "learner@example.com", "Password123!")
        server.deactivate_skill(self.skills_db, "python")
        with self.assertRaises(server.ApiProblem) as problem:
            server.replace_user_skills(
                self.account_db,
                self.skills_db,
                user["id"],
                {
                    "skillsOffered": [
                        {"skillId": "python", "level": "beginner", "desc": {}}
                    ],
                    "skillsWanted": [
                        {"skillId": "cooking", "level": "beginner", "desc": {}}
                    ],
                },
            )
        self.assertEqual(problem.exception.code, "invalid_skill")


class SkillHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "accounts.db"
        self.skills_db_path = root / "skills.db"
        server.initialize_database(
            self.db_path,
            admin_email="admin@example.com",
            admin_password="SecureAdmin123!",
            admin_name="Test Admin",
        )
        server.initialize_skill_database(self.skills_db_path)
        server.seed_admin_user_skills(self.db_path)
        config = server.ServerConfig(self.db_path, self.skills_db_path)
        self.httpd = server.ThreadingHTTPServer(
            ("127.0.0.1", 0), server.make_handler(config)
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.httpd.server_address

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def request(self, method, path, payload=None, cookie="", csrf="", origin=""):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        body = json.dumps(payload) if payload is not None else None
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        elif method in {"POST", "PUT", "PATCH", "DELETE"}:
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        if csrf:
            headers["X-CSRF-Token"] = csrf
        if origin:
            headers["Origin"] = origin
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            decoded = json.loads(raw) if raw else None
            return response.status, dict(response.getheaders()), decoded
        finally:
            connection.close()

    def login(self, email="daniel@example.com", password="SkillSwap123!"):
        status, headers, payload = self.request(
            "POST", "/api/auth/login", {"email": email, "password": password}
        )
        self.assertEqual(status, 200, payload)
        return headers["Set-Cookie"].split(";", 1)[0]

    def admin_auth(self):
        with closing(server.connect_database(self.db_path)) as connection:
            admin_id = connection.execute(
                "SELECT id FROM users WHERE email = 'admin@example.com'"
            ).fetchone()["id"]
        token, csrf = server.create_admin_session(self.db_path, admin_id)
        cookie = (
            f"{server.ADMIN_SESSION_COOKIE}={token}; "
            f"{server.ADMIN_CSRF_COOKIE}={csrf}"
        )
        return cookie, csrf

    def admin_request(self, method, path, payload=None, cookie=None, csrf=None):
        if cookie is None or csrf is None:
            cookie, csrf = self.admin_auth()
        return self.request(
            method,
            path,
            payload,
            cookie,
            csrf,
            f"http://{self.host}:{self.port}",
        )

    def register(self, email):
        status, headers, payload = self.request(
            "POST", "/api/auth/register", {"email": email, "password": "Password123!"}
        )
        self.assertEqual(status, 201, payload)
        return headers["Set-Cookie"].split(";", 1)[0], payload["user"]

    def complete_member(self, email, name, offered="photography", wanted="chemistry"):
        cookie, user = self.register(email)
        status, _, payload = self.request(
            "PUT",
            "/api/users/me/profile",
            {
                "name": name,
                "age": 21,
                "countryId": "cn",
                "cityId": "tianjin",
                "languages": ["zh", "en"],
                "bio": {"zh": "真实技能交换用户", "en": "A real skill swap member"},
                "avatarDataUrl": "",
                "profileVisibility": "community",
                "onboardingCompleted": True,
            },
            cookie,
        )
        self.assertEqual(status, 200, payload)
        status, _, payload = self.request(
            "PUT",
            "/api/users/me/skills",
            {
                "skillsOffered": [{"skillId": offered, "level": "advanced", "desc": {}}],
                "skillsWanted": [{"skillId": wanted, "level": "beginner", "desc": {}}],
            },
            cookie,
        )
        self.assertEqual(status, 200, payload)
        return cookie, user

    def test_skill_endpoints_require_login_and_reject_main_site_writes(self):
        status, _, _ = self.request("GET", "/api/skills")
        self.assertEqual(status, 401)
        user_cookie, _ = self.register("member@example.com")
        status, _, payload = self.request(
            "POST",
            "/api/skills",
            {
                "id": "woodworking",
                "category": "creative",
                "names": {"zh": "木工", "en": "Woodworking"},
            },
            user_cookie,
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "not_found")

    def test_admin_can_create_update_deactivate_and_restore_skill(self):
        cookie, csrf = self.admin_auth()
        create_payload = {
            "id": "woodworking",
            "category": "creative",
            "names": {"zh": "木工", "en": "Woodworking"},
        }
        status, _, payload = self.admin_request(
            "POST", "/api/admin/skills", create_payload, cookie, csrf
        )
        self.assertEqual(status, 201)
        self.assertTrue(payload["skill"]["isActive"])

        status, _, payload = self.admin_request(
            "POST", "/api/admin/skills", create_payload, cookie, csrf
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "skill_exists")

        updated = {
            **create_payload,
            "names": {"zh": "基础木工", "en": "Practical Woodworking"},
        }
        status, _, payload = self.admin_request(
            "PUT", "/api/admin/skills/woodworking", updated, cookie, csrf
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["skill"]["names"]["zh"], "基础木工")

        status, _, _ = self.admin_request(
            "DELETE", "/api/admin/skills/woodworking", cookie=cookie, csrf=csrf
        )
        self.assertEqual(status, 204)
        status, _, payload = self.request("GET", "/api/skills?q=wood", cookie=self.login())
        self.assertEqual(payload["skills"], [])

        updated["isActive"] = True
        status, _, payload = self.admin_request(
            "PUT", "/api/admin/skills/woodworking", updated, cookie, csrf
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["skill"]["isActive"])

    def test_registration_profile_skills_and_real_user_search(self):
        member_cookie, member = self.register("teacher@example.com")
        profile = {
            "name": "Real Teacher",
            "age": 22,
            "countryId": "cn",
            "cityId": "beijing",
            "languages": ["zh", "en"],
            "bio": {"zh": "教 Python", "en": "I teach Python"},
            "avatarDataUrl": "",
            "profileVisibility": "community",
            "onboardingCompleted": True,
        }
        status, _, _ = self.request(
            "PUT", "/api/users/me/profile", profile, member_cookie
        )
        self.assertEqual(status, 200)
        status, _, _ = self.request(
            "PUT",
            "/api/users/me/skills",
            {
                "skillsOffered": [
                    {
                        "skillId": "python",
                        "level": "intermediate",
                        "desc": {"zh": "Python 入门", "en": "Python basics"},
                    }
                ],
                "skillsWanted": [
                    {
                        "skillId": "cooking",
                        "level": "beginner",
                        "desc": {"zh": "家常菜", "en": "Home cooking"},
                    }
                ],
            },
            member_cookie,
        )
        self.assertEqual(status, 200)

        admin_cookie = self.login()
        status, _, payload = self.request(
            "GET", "/api/search?q=PY&country=cn&lang=en", cookie=admin_cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["skills"][0]["id"], "python")
        self.assertEqual([user["id"] for user in payload["users"]], [member["id"]])

        status, _, payload = self.request(
            "GET", "/api/search?q=cooking", cookie=admin_cookie
        )
        self.assertEqual(status, 200)
        self.assertNotIn(member["id"], [user["id"] for user in payload["users"]])

    def test_search_excludes_private_and_incomplete_profiles(self):
        private_cookie, _ = self.register("private@example.com")
        profile = {
            "name": "Private Teacher",
            "age": 20,
            "countryId": "cn",
            "cityId": "tianjin",
            "languages": ["zh"],
            "bio": {"zh": "摄影", "en": "Photography"},
            "avatarDataUrl": "",
            "profileVisibility": "private",
            "onboardingCompleted": True,
        }
        self.request("PUT", "/api/users/me/profile", profile, private_cookie)
        self.request(
            "PUT",
            "/api/users/me/skills",
            {
                "skillsOffered": [
                    {"skillId": "photography", "level": "advanced", "desc": {}}
                ],
                "skillsWanted": [
                    {"skillId": "python", "level": "beginner", "desc": {}}
                ],
            },
            private_cookie,
        )
        incomplete_cookie, _ = self.register("incomplete@example.com")
        self.request(
            "PUT",
            "/api/users/me/skills",
            {
                "skillsOffered": [
                    {"skillId": "photography", "level": "advanced", "desc": {}}
                ],
                "skillsWanted": [
                    {"skillId": "python", "level": "beginner", "desc": {}}
                ],
            },
            incomplete_cookie,
        )
        status, _, payload = self.request(
            "GET", "/api/search?q=photography", cookie=self.login()
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["users"], [])

    def test_search_pagination_reports_total_and_has_more(self):
        for index in range(2):
            cookie, _ = self.register(f"paged-{index}@example.com")
            self.request(
                "PUT",
                "/api/users/me/profile",
                {
                    "name": f"Paged Teacher {index}",
                    "age": 20 + index,
                    "countryId": "cn",
                    "cityId": "tianjin",
                    "languages": ["zh"],
                    "bio": {"zh": "摄影", "en": "Photography"},
                    "avatarDataUrl": "",
                    "profileVisibility": "community",
                    "onboardingCompleted": True,
                },
                cookie,
            )
            self.request(
                "PUT",
                "/api/users/me/skills",
                {
                    "skillsOffered": [
                        {"skillId": "photography", "level": "advanced", "desc": {}}
                    ],
                    "skillsWanted": [
                        {"skillId": "python", "level": "beginner", "desc": {}}
                    ],
                },
                cookie,
            )

        admin_cookie = self.login()
        status, _, first = self.request(
            "GET", "/api/search?q=photography&limit=1&offset=0", cookie=admin_cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(first["users"]), 1)
        self.assertEqual(first["pagination"]["totalUsers"], 2)
        self.assertTrue(first["pagination"]["hasMore"])

        status, _, second = self.request(
            "GET", "/api/search?q=photography&limit=1&offset=1", cookie=admin_cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(second["users"]), 1)
        self.assertFalse(second["pagination"]["hasMore"])

    def test_swap_request_lifecycle_requires_both_completion_confirmations(self):
        target_cookie, target = self.complete_member(
            "swap-target@example.com", "Swap Target"
        )
        requester_cookie = self.login()
        request_payload = {
            "targetUserId": target["id"],
            "offeredSkillId": "chemistry",
            "requestedSkillId": "photography",
            "meetingPolicy": "public-place",
        }
        status, _, created = self.request(
            "POST", "/api/swap-requests", request_payload, requester_cookie
        )
        self.assertEqual(status, 201, created)
        item = created["request"]
        request_id = item["id"]
        self.assertEqual(item["status"], "pending")
        self.assertTrue(item["createdAt"].endswith("+00:00"))

        status, _, duplicate = self.request(
            "POST", "/api/swap-requests", request_payload, requester_cookie
        )
        self.assertEqual(status, 409)
        self.assertEqual(duplicate["error"], "request_exists")

        status, _, reversed_duplicate = self.request(
            "POST",
            "/api/swap-requests",
            {
                "targetUserId": "1",
                "offeredSkillId": "photography",
                "requestedSkillId": "chemistry",
                "meetingPolicy": "public-place",
            },
            target_cookie,
        )
        self.assertEqual(status, 409)
        self.assertEqual(reversed_duplicate["error"], "request_exists")

        status, _, sent = self.request(
            "GET", "/api/swap-requests", cookie=requester_cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(sent["sent"][0]["counterpart"]["id"], target["id"])
        status, _, received = self.request(
            "GET", "/api/swap-requests", cookie=target_cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(received["received"][0]["direction"], "received")

        status, _, forbidden = self.request(
            "POST", f"/api/swap-requests/{request_id}/accept", cookie=requester_cookie
        )
        self.assertEqual(status, 403)
        self.assertEqual(forbidden["error"], "request_forbidden")

        status, _, accepted = self.request(
            "POST", f"/api/swap-requests/{request_id}/accept", cookie=target_cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(accepted["request"]["status"], "accepted")

        status, _, first_confirmation = self.request(
            "POST", f"/api/swap-requests/{request_id}/complete", cookie=requester_cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(first_confirmation["request"]["status"], "accepted")
        self.assertIsNotNone(first_confirmation["request"]["requesterCompletedAt"])
        self.assertIsNone(first_confirmation["request"]["targetCompletedAt"])

        status, _, stats = self.request("GET", "/api/community/stats")
        self.assertEqual(status, 200)
        self.assertEqual(stats["swapsCompletedToday"], 0)

        status, _, completed = self.request(
            "POST", f"/api/swap-requests/{request_id}/complete", cookie=target_cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(completed["request"]["status"], "completed")
        self.assertIsNotNone(completed["request"]["completedAt"])

        status, _, stats = self.request("GET", "/api/community/stats")
        self.assertEqual(status, 200)
        self.assertEqual(stats["registeredUsers"], 2)
        self.assertEqual(stats["onlineToday"], 2)
        self.assertEqual(stats["swapsCompletedToday"], 1)
        trend = {item["skillId"]: item["wantCount"] for item in stats["trendingSkills"]}
        self.assertEqual(trend["chemistry"], 1)

    def test_private_requester_cannot_create_swap_request(self):
        requester_cookie, _ = self.complete_member(
            "private-requester@example.com", "Private Requester"
        )
        status, _, profile_payload = self.request(
            "GET", "/api/users/me/profile", cookie=requester_cookie
        )
        self.assertEqual(status, 200)
        private_profile = {
            **profile_payload["profile"],
            "profileVisibility": "private",
            "onboardingCompleted": True,
        }
        status, _, _ = self.request(
            "PUT", "/api/users/me/profile", private_profile, requester_cookie
        )
        self.assertEqual(status, 200)
        status, _, payload = self.request(
            "POST",
            "/api/swap-requests",
            {
                "targetUserId": "1",
                "offeredSkillId": "photography",
                "requestedSkillId": "chemistry",
                "meetingPolicy": "public-place",
            },
            requester_cookie,
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "profile_unavailable")

    def test_swap_requests_can_be_rejected_or_cancelled(self):
        target_cookie, target = self.complete_member(
            "request-actions@example.com", "Request Actions"
        )
        requester_cookie = self.login()
        payload = {
            "targetUserId": target["id"],
            "offeredSkillId": "chemistry",
            "requestedSkillId": "photography",
        }
        status, _, created = self.request(
            "POST", "/api/swap-requests", payload, requester_cookie
        )
        self.assertEqual(status, 201)
        request_id = created["request"]["id"]
        status, _, rejected = self.request(
            "POST", f"/api/swap-requests/{request_id}/reject", cookie=target_cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(rejected["request"]["status"], "rejected")

        status, _, created = self.request(
            "POST", "/api/swap-requests", payload, requester_cookie
        )
        self.assertEqual(status, 201)
        request_id = created["request"]["id"]
        status, _, cancelled = self.request(
            "POST", f"/api/swap-requests/{request_id}/cancel", cookie=requester_cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(cancelled["request"]["status"], "cancelled")

    def test_community_stats_use_shanghai_day_boundary_and_unique_logins(self):
        now = datetime(2026, 8, 18, 16, 30, tzinfo=timezone.utc)
        start_epoch, _, start_iso, _ = server._shanghai_day_bounds(now)
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            user_id = connection.execute(
                "SELECT id FROM users WHERE email='daniel@example.com'"
            ).fetchone()[0]
            connection.execute("DELETE FROM sessions")
            connection.execute("DELETE FROM login_events")
            connection.executemany(
                "INSERT INTO login_events(session_token_hash,user_id,created_at) VALUES(?,?,?)",
                [
                    ("same-user-1", user_id, start_epoch + 10),
                    ("same-user-2", user_id, start_epoch + 20),
                    ("previous-day", user_id, start_epoch - 10),
                ],
            )
            connection.execute(
                """INSERT INTO swap_requests
                   (requester_id,target_user_id,offered_skill_id,requested_skill_id,
                    status,created_at,requester_completed_at,target_completed_at,completed_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (user_id, user_id + 1, "chemistry", "photography", "completed", start_iso, start_iso, start_iso, start_iso),
            )
        stats = server.community_stats(self.db_path, self.skills_db_path, now=now)
        self.assertEqual(stats["onlineToday"], 1)
        self.assertEqual(stats["swapsCompletedToday"], 1)


class ChatHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "chat.db"
        self.skills_db_path = Path(self.temp_dir.name) / "skills.db"
        server.initialize_database(self.db_path)
        server.initialize_skill_database(self.skills_db_path)
        config = server.ServerConfig(self.db_path, self.skills_db_path)
        self.httpd = server.ThreadingHTTPServer(
            ("127.0.0.1", 0), server.make_handler(config)
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.httpd.server_address
        self.first_cookie, self.first_id = self.login(
            "daniel@example.com", "SkillSwap123!"
        )
        self.second_cookie, self.second_id = self.register("chat-peer@example.com")
        self.third_cookie, self.third_id = self.register("not-connected@example.com")
        self.connect(self.first_id, self.second_id)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def request(self, method, path, payload=None, cookie="", timeout=5):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        body = json.dumps(payload) if payload is not None else None
        headers = {"Cookie": cookie} if cookie else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
        finally:
            connection.close()

    def login(self, email, password):
        status, payload = self.request(
            "POST", "/api/auth/login", {"email": email, "password": password}
        )
        self.assertEqual(status, 200, payload)
        user = server.authenticate_user(self.db_path, email, password)
        token = server.create_session(self.db_path, user["id"])
        return f"{server.SESSION_COOKIE}={token}", user["id"]

    def register(self, email):
        status, payload = self.request(
            "POST",
            "/api/auth/register",
            {"email": email, "password": "Password123!"},
        )
        self.assertEqual(status, 201, payload)
        user = server.authenticate_user(self.db_path, email, "Password123!")
        token = server.create_session(self.db_path, user["id"])
        return f"{server.SESSION_COOKIE}={token}", user["id"]

    def connect(self, first_id, second_id, status="accepted"):
        now = server.utc_now_iso()
        with closing(server.connect_database(self.db_path)) as connection, connection:
            connection.execute(
                """INSERT INTO swap_requests
                   (requester_id,target_user_id,offered_skill_id,requested_skill_id,
                    status,created_at,accepted_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (first_id, second_id, "chemistry", "photography", status, now, now),
            )

    def send(self, cookie, recipient_id, text, client_id):
        return self.request(
            "POST",
            "/api/chat/messages",
            {
                "recipientUserId": recipient_id,
                "text": text,
                "clientMessageId": client_id,
            },
            cookie,
        )

    def test_chat_endpoints_require_login_and_partner_access(self):
        status, payload = self.request("GET", "/api/chat/conversations")
        self.assertEqual(status, 401)
        status, payload = self.send(
            self.first_cookie, self.third_id, "hello", "notconnected0001"
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "chat_not_connected")
        status, payload = self.send(
            self.first_cookie, self.first_id, "hello", "selfmessage000001"
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "self_message")

    def test_send_is_idempotent_and_rejects_conflicting_retry(self):
        client_id = "idempotentmessage01"
        status, first = self.send(self.first_cookie, self.second_id, "  hello  ", client_id)
        self.assertEqual(status, 201)
        self.assertEqual(first["message"]["text"], "hello")
        status, duplicate = self.send(
            self.first_cookie, self.second_id, "hello", client_id
        )
        self.assertEqual(status, 200)
        self.assertFalse(duplicate["created"])
        self.assertEqual(duplicate["message"]["id"], first["message"]["id"])
        status, conflict = self.send(
            self.first_cookie, self.second_id, "different", client_id
        )
        self.assertEqual(status, 409)
        self.assertEqual(conflict["error"], "client_message_id_conflict")

    def test_message_validation_and_inactive_recipient(self):
        status, _ = self.send(
            self.first_cookie, self.second_id, "   ", "emptymessage00001"
        )
        self.assertEqual(status, 400)
        status, _ = self.send(
            self.first_cookie, self.second_id, "x" * 2001, "toolongmessage001"
        )
        self.assertEqual(status, 400)
        with closing(server.connect_database(self.db_path)) as connection, connection:
            connection.execute(
                "UPDATE users SET is_active=0 WHERE id=?", (self.second_id,)
            )
        status, payload = self.send(
            self.first_cookie, self.second_id, "hello", "inactiveuser00001"
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "user_not_found")

    def test_history_order_pagination_and_conversation_preview(self):
        ids = []
        for index in range(3):
            status, payload = self.send(
                self.first_cookie,
                self.second_id,
                f"message {index}",
                f"orderedmessage{index:04d}",
            )
            self.assertEqual(status, 201)
            ids.append(payload["message"]["id"])
        status, history = self.request(
            "GET",
            f"/api/chat/messages?peerId={self.second_id}&limit=2",
            cookie=self.first_cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in history["messages"]], ids[1:])
        self.assertTrue(history["hasMore"])
        status, older = self.request(
            "GET",
            f"/api/chat/messages?peerId={self.second_id}&beforeId={history['nextBeforeId']}&limit=2",
            cookie=self.first_cookie,
        )
        self.assertEqual([item["id"] for item in older["messages"]], ids[:1])
        status, conversations = self.request(
            "GET", "/api/chat/conversations", cookie=self.second_cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(conversations["conversations"][0]["lastMessage"]["text"], "message 2")

    def test_expired_messages_are_hidden_and_physically_purged_on_send(self):
        with closing(server.connect_database(self.db_path)) as connection, connection:
            connection.execute(
                """INSERT INTO chat_messages
                   (sender_id,recipient_id,body,client_message_id,created_at)
                   VALUES (?,?,?,?,?)""",
                (
                    self.first_id,
                    self.second_id,
                    "expired",
                    "expiredmessage001",
                    int(time.time()) - server.CHAT_RETENTION_SECONDS - 5,
                ),
            )
        status, history = self.request(
            "GET",
            f"/api/chat/messages?peerId={self.second_id}",
            cookie=self.first_cookie,
        )
        self.assertEqual(history["messages"], [])
        self.send(self.first_cookie, self.second_id, "fresh", "freshmessage00001")
        with closing(server.connect_database(self.db_path)) as connection:
            expired = connection.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE body='expired'"
            ).fetchone()[0]
        self.assertEqual(expired, 0)

    def test_message_history_survives_server_restart(self):
        status, sent = self.send(
            self.first_cookie,
            self.second_id,
            "persist after restart",
            "restartmessage001",
        )
        self.assertEqual(status, 201)
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

        server.initialize_database(self.db_path)
        config = server.ServerConfig(self.db_path, self.skills_db_path)
        self.httpd = server.ThreadingHTTPServer(
            ("127.0.0.1", 0), server.make_handler(config)
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.httpd.server_address

        status, history = self.request(
            "GET",
            f"/api/chat/messages?peerId={self.second_id}",
            cookie=self.first_cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(history["messages"][-1]["id"], sent["message"]["id"])
        self.assertEqual(history["messages"][-1]["text"], "persist after restart")

    def test_long_poll_wakes_for_new_message_and_times_out_empty(self):
        started = threading.Event()
        result = {}

        def poll():
            started.set()
            result["response"] = self.request(
                "GET", "/api/chat/events?afterId=0&wait=2", cookie=self.second_cookie
            )

        thread = threading.Thread(target=poll)
        thread.start()
        started.wait(1)
        time.sleep(0.1)
        self.send(self.first_cookie, self.second_id, "wake", "longpollmessage01")
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        status, payload = result["response"]
        self.assertEqual(status, 200)
        self.assertEqual(payload["messages"][0]["text"], "wake")

        after_id = payload["cursor"]
        started_at = time.monotonic()
        status, timeout_payload = self.request(
            "GET",
            f"/api/chat/events?afterId={after_id}&wait=0.1",
            cookie=self.second_cookie,
        )
        elapsed = time.monotonic() - started_at
        self.assertEqual(status, 200)
        self.assertEqual(timeout_payload["messages"], [])
        self.assertGreaterEqual(elapsed, 0.08)


if __name__ == "__main__":
    unittest.main()
