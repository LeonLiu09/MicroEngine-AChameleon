from contextlib import closing
import http.client
import json
import sqlite3
import tempfile
import threading
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
                "SELECT email, password_hash FROM users WHERE email = ?",
                ("daniel@example.com",),
            ).fetchone()
        self.assertTrue({"users", "sessions"}.issubset(tables))
        self.assertEqual(account[0], "daniel@example.com")
        self.assertNotEqual(account[1], "SkillSwap123!")

    def test_database_initialization_is_idempotent(self):
        server.initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        self.assertEqual(count, 1)

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
        server.initialize_database(self.db_path)
        config = server.ServerConfig(db_path=self.db_path)
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


if __name__ == "__main__":
    unittest.main()
