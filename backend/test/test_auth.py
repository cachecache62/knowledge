"""认证和资源访问测试；默认 SQLite，可切换到隔离的 MySQL 测试库。"""

import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select, text, update
from sqlalchemy.pool import StaticPool

from api.auth_service import app, password_hasher
from auth.store import AuthStore, get_auth_store, login_sessions, metadata, token_digest, users, utcnow
from config.auth_config import get_auth_config
from test import test_api_frontend as protocol_tests
from utils import sse_utils, task_utils


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        protocol_tests.ApiFrontendTests.setUpClass()
        cls.query_api = protocol_tests.ApiFrontendTests.query_api
        cls.import_api = protocol_tests.ApiFrontendTests.import_api
        test_url = os.getenv("AUTH_TEST_MYSQL_URL")
        if test_url:
            cls.engine = create_engine(test_url, pool_pre_ping=True)
            if not (cls.engine.url.database or "").startswith("kb_auth_test_"):
                raise ValueError("只允许在 kb_auth_test_ 开头的独立 MySQL 测试库运行测试")
        else:
            cls.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        cls.store = AuthStore(cls.engine)
        cls.store.create_schema()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def setUp(self) -> None:
        with self.engine.begin() as connection:
            for table in reversed(metadata.sorted_tables):
                connection.execute(delete(table))
        for service in (app, self.query_api.app, self.import_api.app):
            service.dependency_overrides.clear()
            service.dependency_overrides[get_auth_store] = lambda: self.store
            self.addCleanup(service.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        sse_utils._session_stream.clear()
        for task in list(task_utils._tasks_status):
            task_utils.clear_task(task)

    def register(self, email: str = "reader@example.com", client: TestClient | None = None) -> dict:
        response = (client or self.client).post("/auth/register", json={"email": email, "password": "A-long-test-password!"})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["user"]

    def business_client(self, service: object, token: str | None = None) -> TestClient:
        client = TestClient(service.app)
        self.addCleanup(client.close)
        client.cookies.set(get_auth_config().cookie_name, token or self.client.cookies.get(get_auth_config().cookie_name))
        return client

    def test_registration_hashes_password_and_sets_http_only_cookie(self) -> None:
        response = self.client.post("/auth/register", json={"email": "Reader@Example.com", "password": "A-long-test-password!"})
        self.assertEqual(response.status_code, 201, response.text)
        record = self.store.find_user("reader@example.com")
        self.assertEqual(record["email"], "reader@example.com")
        self.assertNotEqual(record["password_hash"], "A-long-test-password!")
        self.assertTrue(password_hasher.verify("A-long-test-password!", record["password_hash"]))
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("password", response.text)
        token = self.client.cookies.get(get_auth_config().cookie_name)
        with self.engine.connect() as connection:
            stored = connection.execute(select(login_sessions.c.token_hash)).scalar_one()
        self.assertEqual(stored, token_digest(token))
        self.assertNotEqual(stored, token)
        self.assertEqual(self.client.get("/auth/me").json()["user"]["id"], record["id"])

    def test_duplicate_email_and_input_validation(self) -> None:
        self.register()
        duplicate = self.client.post("/auth/register", json={"email": "READER@example.com", "password": "A-long-test-password!"})
        self.assertEqual(duplicate.status_code, 409)
        for email, password in [("not-an-email", "valid-password"), ("other@example.com", "short"), ("other@example.com", "x" * 129)]:
            self.assertEqual(self.client.post("/auth/register", json={"email": email, "password": password}).status_code, 422)

    def test_invalid_credentials_have_same_error(self) -> None:
        self.register()
        wrong = self.client.post("/auth/login", json={"email": "reader@example.com", "password": "wrong"})
        missing = self.client.post("/auth/login", json={"email": "unknown@example.com", "password": "wrong"})
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(wrong.json(), missing.json())

    def test_login_rotates_cookie_and_logout_revokes_server_session(self) -> None:
        self.register()
        old_token = self.client.cookies.get(get_auth_config().cookie_name)
        response = self.client.post("/auth/login", json={"email": "reader@example.com", "password": "A-long-test-password!"})
        self.assertEqual(response.status_code, 200)
        new_token = self.client.cookies.get(get_auth_config().cookie_name)
        self.assertNotEqual(new_token, old_token)
        self.assertIsNone(self.store.authenticated_user(old_token))
        self.assertIsNotNone(AuthStore(self.engine).authenticated_user(new_token))
        self.assertEqual(self.client.post("/auth/logout").status_code, 200)
        self.assertIsNone(self.store.authenticated_user(new_token))
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_registration_and_login_persist_cookie_for_six_hours(self) -> None:
        created_at = utcnow().replace(microsecond=0)
        payload = {"email": "reader@example.com", "password": "A-long-test-password!"}
        with patch("api.auth_service.utcnow", return_value=created_at), patch("auth.store.utcnow", return_value=created_at):
            for path, status in [("/auth/register", 201), ("/auth/login", 200)]:
                with self.subTest(path=path):
                    response = self.client.post(path, json=payload)
                    self.assertEqual(response.status_code, status, response.text)
                    self.assertIn("max-age=21600", response.headers["set-cookie"].lower())
                    cookie = next(item for item in self.client.cookies.jar if item.name == get_auth_config().cookie_name)
                    self.assertFalse(cookie.discard)
                    self.assertIsNotNone(cookie.expires)
                    with self.engine.connect() as connection:
                        session = connection.execute(select(login_sessions)).mappings().one()
                    self.assertEqual(session["created_at"], created_at)
                    self.assertEqual(session["expires_at"], created_at + timedelta(hours=6))

    def test_saved_cookie_restores_login_until_the_six_hour_boundary(self) -> None:
        created_at = utcnow().replace(microsecond=0)
        with patch("api.auth_service.utcnow", return_value=created_at), patch("auth.store.utcnow", return_value=created_at):
            user = self.register()
        with TestClient(app) as reopened:
            reopened.cookies.update(self.client.cookies)
            with patch("auth.store.utcnow", return_value=created_at + timedelta(hours=6, seconds=-1)):
                response = reopened.get("/auth/me")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["user"]["id"], user["id"])
            self.assertNotIn("set-cookie", response.headers)
            with patch("auth.store.utcnow", return_value=created_at + timedelta(hours=6)):
                self.assertEqual(reopened.get("/auth/me").status_code, 401)

    def test_old_seven_day_session_cannot_outlive_six_hours(self) -> None:
        created_at = utcnow().replace(microsecond=0)
        with patch("api.auth_service.utcnow", return_value=created_at), patch("auth.store.utcnow", return_value=created_at):
            self.register()
        with self.engine.begin() as connection:
            connection.execute(update(login_sessions).values(expires_at=created_at + timedelta(days=7)))
        with patch("auth.store.utcnow", return_value=created_at + timedelta(hours=6, seconds=-1)):
            self.assertEqual(self.client.get("/auth/me").status_code, 200)
        with patch("auth.store.utcnow", return_value=created_at + timedelta(hours=6)):
            self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_expired_and_disabled_sessions_are_rejected(self) -> None:
        user = self.register()
        with self.engine.begin() as connection:
            connection.execute(update(login_sessions).values(expires_at=utcnow() - timedelta(seconds=1)))
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        self.client.post("/auth/login", json={"email": user["email"], "password": "A-long-test-password!"})
        with self.engine.begin() as connection:
            connection.execute(update(users).where(users.c.id == user["id"]).values(is_active=False))
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        self.assertEqual(self.client.post("/auth/login", json={"email": user["email"], "password": "A-long-test-password!"}).status_code, 401)

    def test_unauthenticated_business_endpoints_return_401(self) -> None:
        for service, method, path, options in [
            (self.query_api, "post", "/query", {"json": {"query": "question"}}),
            (self.query_api, "get", "/history/other-session", {}),
            (self.query_api, "delete", "/history/other-session", {}),
            (self.query_api, "get", "/stream/other-session", {}),
            (self.import_api, "post", "/upload", {"files": {"files": ("manual.md", b"# Manual")}}),
            (self.import_api, "get", "/status/other-task", {}),
        ]:
            with TestClient(service.app) as client:
                response = getattr(client, method)(path, **options)
                self.assertEqual(response.status_code, 401, f"{method} {path}: {response.text}")

    def test_chat_ids_are_scoped_to_each_user_and_legacy_history_is_not_claimed(self) -> None:
        first = self.register()
        token_a = self.client.cookies.get(get_auth_config().cookie_name)
        with TestClient(app) as other:
            second = self.register("other@example.com", other)
            token_b = other.cookies.get(get_auth_config().cookie_name)
        first_id = self.store.chat_id(first["id"], "shared-browser-id", create=True)
        second_id = self.store.chat_id(second["id"], "shared-browser-id", create=True)
        self.assertNotEqual(first_id, second_id)
        self.assertNotEqual(first_id, "shared-browser-id")
        client_a = self.business_client(self.query_api, token_a)
        client_b = self.business_client(self.query_api, token_b)
        with patch.object(self.query_api, "get_recent_messages", return_value=[]) as get_messages:
            self.assertEqual(client_a.get("/history/shared-browser-id").status_code, 200)
            self.assertEqual(get_messages.call_args.args[0], first_id)
            self.assertEqual(client_b.get("/history/shared-browser-id").status_code, 200)
            self.assertEqual(get_messages.call_args.args[0], second_id)
            get_messages.reset_mock()
            self.assertEqual(client_b.get("/history/old-anonymous-session").json()["items"], [])
            get_messages.assert_not_called()
        self.store.chat_id(first["id"], "first-user-only", create=True)
        self.assertEqual(client_b.get("/stream/first-user-only").status_code, 404)
        with patch.object(self.query_api, "clear_history") as clear:
            self.assertEqual(client_b.delete("/history/first-user-only").json()["deleted_count"], 0)
            clear.assert_not_called()

    def test_authenticated_query_uses_internal_id_but_returns_browser_id(self) -> None:
        user = self.register()
        client = self.business_client(self.query_api)
        with patch.object(self.query_api, "run_query_graph", return_value={"answer": "Answer"}) as run:
            response = client.post("/query", json={"query": "Question", "session_id": "browser-chat"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["session_id"], "browser-chat")
        self.assertEqual(run.call_args.args[0], self.store.chat_id(user["id"], "browser-chat"))

    def test_import_progress_is_only_visible_to_its_owner(self) -> None:
        user = self.register()
        token_a = self.client.cookies.get(get_auth_config().cookie_name)
        task_id = "00000000-0000-0000-0000-000000000001"
        self.store.remember_import(user["id"], task_id)
        task_utils.update_task_status(task_id, "completed")
        with TestClient(app) as other:
            self.register("other@example.com", other)
            token_b = other.cookies.get(get_auth_config().cookie_name)
        self.assertEqual(self.business_client(self.import_api, token_a).get(f"/status/{task_id}").status_code, 200)
        self.assertEqual(self.business_client(self.import_api, token_b).get(f"/status/{task_id}").status_code, 404)

    def test_csrf_origin_is_checked_and_credentialed_cors_is_explicit(self) -> None:
        blocked = self.client.post("/auth/register", headers={"Origin": "https://evil.example"}, json={"email": "reader@example.com", "password": "valid-password"})
        self.assertEqual(blocked.status_code, 403)
        self.register()
        self.assertEqual(self.client.post("/auth/logout", headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/auth/me").status_code, 200)
        allowed = self.client.options("/auth/login", headers={"Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "POST"})
        self.assertEqual(allowed.headers["access-control-allow-origin"], "http://127.0.0.1:5173")
        self.assertEqual(allowed.headers["access-control-allow-credentials"], "true")
        blocked = self.client.options("/auth/login", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
        self.assertEqual(blocked.status_code, 400)

    def test_sql_parameters_do_not_turn_email_into_sql(self) -> None:
        self.register()
        self.assertIsNone(self.store.find_user("' OR 1=1 --"))
        self.assertIsNotNone(self.store.find_user("reader@example.com"))

    def test_user_updated_at_tracks_user_changes_but_not_login(self) -> None:
        user = self.register()
        initial = self.store.find_user(user["email"])
        self.assertEqual(initial["updated_at"], initial["created_at"])
        with self.engine.begin() as connection:
            connection.execute(update(users).where(users.c.id == user["id"]).values(updated_at=datetime(2000, 1, 1)))
            connection.execute(update(users).where(users.c.id == user["id"]).values(email="updated@example.com"))
        changed = self.store.find_user("updated@example.com")
        self.assertGreater(changed["updated_at"], datetime(2000, 1, 1))
        self.assertEqual(changed["created_at"], initial["created_at"])
        self.client.post("/auth/login", json={"email": "updated@example.com", "password": "A-long-test-password!"})
        self.assertEqual(self.store.find_user("updated@example.com")["updated_at"], changed["updated_at"])

    def test_existing_user_table_is_migrated_without_resetting_timestamp_on_retry(self) -> None:
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE kb_users (id INTEGER PRIMARY KEY, email VARCHAR(254) UNIQUE NOT NULL, password_hash VARCHAR(512) NOT NULL, password_plaintext VARCHAR(128), is_active BOOLEAN NOT NULL, created_at DATETIME NOT NULL)"))
            connection.execute(text("INSERT INTO kb_users (id, email, password_hash, is_active, created_at) VALUES (1, 'legacy@example.com', 'existing-hash', 1, '2020-01-02 03:04:05')"))
        store = AuthStore(engine)
        store.create_schema()
        record = store.find_user("legacy@example.com")
        self.assertEqual(record["updated_at"], record["created_at"])
        self.assertEqual(record["password_hash"], "existing-hash")
        with engine.begin() as connection:
            connection.execute(update(users).where(users.c.id == 1).values(updated_at=datetime(2024, 1, 1)))
        store.create_schema()
        self.assertEqual(store.find_user("legacy@example.com")["updated_at"], datetime(2024, 1, 1))


if __name__ == "__main__":
    unittest.main()
