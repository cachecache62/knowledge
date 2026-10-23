"""验证 Vue 使用的实际 FastAPI 协议，隔离模型与外部存储。"""

import asyncio
import importlib
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from api import frontend
from auth.dependencies import get_current_user
from auth.store import User, get_auth_store, utcnow
from utils import sse_utils, task_utils
from utils.import_registry import ImportRegistry

ROOT = Path(__file__).resolve().parent.parent


def stub_module(name: str, **attributes: object) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    return module


class ApiFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # API 模块导入时也不加载模型或连接 MongoDB/MinIO。
        stubs = {
            "processor.query_processor.main_graph": stub_module(
                "processor.query_processor.main_graph", KBQueryWorkflow=MagicMock()
            ),
            "processor.import_processor.main_graph": stub_module(
                "processor.import_processor.main_graph", KBImportWorkflow=MagicMock()
            ),
            "utils.mongo_history_utils": stub_module(
                "utils.mongo_history_utils", clear_history=MagicMock(), get_recent_messages=MagicMock()
            ),
            "utils.minio_utils": stub_module("utils.minio_utils", get_minio_client=MagicMock()),
            "config.minio_config": stub_module(
                "config.minio_config", minio_config=types.SimpleNamespace(bucket_name="test-bucket")
            ),
        }
        with patch.dict("sys.modules", stubs):
            cls.query_api = importlib.import_module("api.query_service")
            cls.import_api = importlib.import_module("api.import_service")

    def setUp(self) -> None:
        # 上传登记也必须隔离，不能让协议测试写入开发环境的 MySQL。
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        registry = ImportRegistry(engine)
        registry.create_schema()
        registry_patch = patch.object(self.import_api, "get_import_registry", return_value=registry)
        registry_patch.start()
        self.addCleanup(registry_patch.stop)
        store = MagicMock()
        store.chat_id.side_effect = lambda user_id, client_id, **kwargs: client_id
        store.owns_import.return_value = True
        for app in (self.query_api.app, self.import_api.app):
            app.dependency_overrides[get_current_user] = lambda: User(1, "protocol@example.com", utcnow())
            app.dependency_overrides[get_auth_store] = lambda: store
            self.addCleanup(app.dependency_overrides.clear)
        for session_id in list(task_utils._tasks_status):
            task_utils.clear_task(session_id)
        sse_utils._session_stream.clear()
        (ROOT / "tmp").mkdir(exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="api-test-")
        self.addCleanup(self.temp_dir.cleanup)
        self.query_client = TestClient(self.query_api.app)
        self.import_client = TestClient(self.import_api.app)
        self.addCleanup(self.query_client.close)
        self.addCleanup(self.import_client.close)

    def test_sync_query_uses_final_state_images_and_fresh_progress(self) -> None:
        session_id = "sync-session"
        task_utils.add_done_task(session_id, "stale-node")
        task_utils.set_task_result(session_id, "answer", "旧答案")
        workflow = MagicMock()
        workflow.node_answer_output._extract_images_from_docs.return_value = ["https://example.com/a.png"]

        def invoke(state: dict, stream: bool) -> dict:
            with self.assertRaises(RuntimeError):
                asyncio.get_running_loop()
            self.assertFalse(stream)
            task_utils.add_done_task(session_id, "node_answer_output")
            return {**state, "answer": "新的完整答案", "reranked_docs": [{"content": "图片资料"}]}

        workflow.run.side_effect = invoke
        with patch.object(self.query_api, "KBQueryWorkflow", return_value=workflow):
            response = self.query_client.post("/query", json={"query": " 测试问题 ", "session_id": session_id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "新的完整答案")
        self.assertEqual(response.json()["image_urls"], ["https://example.com/a.png"])
        self.assertEqual(response.json()["done_list"], ["生成答案"])
        self.assertEqual(workflow.run.call_args.args[0]["original_query"], "测试问题")

    def test_stream_events_finish_and_release_queue(self) -> None:
        workflow = MagicMock()

        def stream_graph(state: dict, stream: bool):
            self.assertTrue(stream)
            session_id = state["session_id"]
            sse_utils.push_to_session(session_id, "delta", {"delta": "增量"})
            sse_utils.push_to_session(session_id, "final", {
                "answer": "完整答案", "image_urls": ["https://example.com/a.png"], "status": "completed",
            })
            yield {"node_answer_output": {"answer": "完整答案"}}

        workflow.run.side_effect = stream_graph
        with patch.object(self.query_api, "KBQueryWorkflow", return_value=workflow):
            submitted = self.query_client.post("/query", json={"query": "问题", "is_stream": True})
        self.assertEqual(submitted.status_code, 200)
        session_id = submitted.json()["session_id"]
        response = self.query_client.get(f"/stream/{session_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertIn("event: delta", response.text)
        self.assertIn('"answer": "完整答案"', response.text)
        self.assertIn('"image_urls": ["https://example.com/a.png"]', response.text)
        self.assertNotIn("event: __close__", response.text)
        self.assertIsNone(sse_utils.get_sse_queue(session_id))
        self.assertEqual(self.query_client.get(f"/stream/{session_id}").status_code, 404)

    def test_query_failures_return_http_or_sse_errors(self) -> None:
        workflow = MagicMock()
        workflow.run.side_effect = RuntimeError("模拟模型异常")
        with patch.object(self.query_api, "KBQueryWorkflow", return_value=workflow):
            failed = self.query_client.post("/query", json={"query": "问题", "session_id": "sync-failed"})
            streamed = self.query_client.post("/query", json={"query": "问题", "session_id": "stream-failed", "is_stream": True})
        self.assertEqual(failed.status_code, 500)
        self.assertIn("模拟模型异常", failed.json()["detail"])
        self.assertEqual(streamed.status_code, 200)
        response = self.query_client.get("/stream/stream-failed")
        self.assertIn("event: error", response.text)
        self.assertIn("模拟模型异常", response.text)
        self.assertIsNone(sse_utils.get_sse_queue("stream-failed"))

    def test_validation_and_busy_session_do_not_overwrite_queue(self) -> None:
        self.assertEqual(self.query_client.post("/query", json={"query": "   "}).status_code, 422)
        self.assertEqual(self.query_client.post("/query", json={}).status_code, 422)
        queue = sse_utils.create_sse_queue("busy")
        task_utils.update_task_status("busy", "processing")
        response = self.query_client.post("/query", json={"query": "第二次", "session_id": "busy", "is_stream": True})
        self.assertEqual(response.status_code, 409)
        self.assertIs(sse_utils.get_sse_queue("busy"), queue)
        self.assertEqual(self.query_client.delete("/history/busy").status_code, 409)

    def test_history_returns_images_and_clear_result(self) -> None:
        records = [{"_id": "message-1", "role": "assistant", "text": "答案", "image_urls": ["https://example.com/a.png"], "ts": 1}]
        with patch.object(self.query_api, "get_recent_messages", return_value=records):
            response = self.query_client.get("/history/history-session")
        self.assertEqual(response.json()["items"][0]["image_urls"], records[0]["image_urls"])
        self.assertEqual(self.query_client.get("/history/history-session?limit=0").status_code, 422)
        with patch.object(self.query_api, "clear_history", return_value=1):
            self.assertEqual(self.query_client.delete("/history/history-session").json()["deleted_count"], 1)

    def test_multi_upload_matches_frontend_fields_and_status(self) -> None:
        workflow = MagicMock()
        workflow.run.return_value = iter(())
        minio = MagicMock()
        with patch.dict(os.environ, {"DATA_BASED_ROOT_DIR": self.temp_dir.name}), \
                patch.object(self.import_api, "KBImportWorkflow", return_value=workflow), \
                patch.object(self.import_api, "get_minio_client", return_value=minio):
            response = self.import_client.post("/upload", files=[
                ("files", ("../../manual.pdf", b"%PDF demo", "application/pdf")),
                ("files", ("notes.md", b"# Manual", "text/markdown")),
            ])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["task_ids"]), 2)
        for task_id in response.json()["task_ids"]:
            status = self.import_client.get(f"/status/{task_id}").json()
            self.assertEqual(status["status"], "completed")
            self.assertEqual(status["done_list"], ["开始上传文件"])
            self.assertEqual(status["error"], "")
        self.assertEqual(minio.fput_object.call_count, 2)
        self.assertEqual(len(list(Path(self.temp_dir.name).rglob("manual.pdf"))), 1)
        self.assertEqual(self.import_client.get("/status/unknown-task").status_code, 404)

    def test_upload_failure_details_and_minio_fallback(self) -> None:
        self.assertEqual(self.import_client.post("/upload", files={"files": ("bad.txt", b"text")}).status_code, 400)
        workflow = MagicMock()
        workflow.run.side_effect = RuntimeError("解析文档失败")
        minio = MagicMock()
        minio.fput_object.side_effect = RuntimeError("模拟 MinIO 不可用")
        with patch.dict(os.environ, {"DATA_BASED_ROOT_DIR": self.temp_dir.name}), \
                patch.object(self.import_api, "KBImportWorkflow", return_value=workflow), \
                patch.object(self.import_api, "get_minio_client", return_value=minio):
            response = self.import_client.post("/upload", files={"files": ("manual.pdf", b"%PDF demo")})
        self.assertEqual(response.status_code, 200)
        status = self.import_client.get(f"/status/{response.json()['task_ids'][0]}").json()
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["error"], "解析文档失败")

    def test_vue_page_redirects_index_and_assets(self) -> None:
        dist = Path(self.temp_dir.name)
        (dist / "assets").mkdir()
        (dist / "index.html").write_text('<div id="app"></div>', encoding="utf-8")
        (dist / "assets" / "app.js").write_text('console.log("Vue")', encoding="utf-8")
        app = FastAPI()
        with patch.object(frontend, "FRONTEND_DIST", dist):
            frontend.register_frontend(app, "import")
            with TestClient(app) as client:
                self.assertEqual(client.get("/", follow_redirects=False).headers["location"], "/index.html#/import")
                self.assertEqual(client.get("/chat.html", follow_redirects=False).headers["location"], "/index.html#/chat")
                self.assertEqual(client.get("/import.html", follow_redirects=False).headers["location"], "/index.html#/import")
                self.assertIn('id="app"', client.get("/index.html").text)
                self.assertEqual(client.get("/assets/app.js").status_code, 200)

    def test_unbuilt_vue_does_not_prevent_api_startup(self) -> None:
        app = FastAPI()
        with patch.object(frontend, "FRONTEND_DIST", Path(self.temp_dir.name) / "missing"):
            frontend.register_frontend(app, "chat")
            with TestClient(app) as client:
                response = client.get("/index.html")
                self.assertEqual(response.status_code, 503)
                self.assertIn("npm run build", response.json()["detail"])

    def test_vite_cross_origin_preflight(self) -> None:
        for client, path, method in [(self.query_client, "/query", "POST"), (self.import_client, "/upload", "POST")]:
            response = client.options(path, headers={
                "Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "content-type",
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["access-control-allow-origin"], "http://127.0.0.1:5173")
            self.assertEqual(response.headers["access-control-allow-credentials"], "true")


if __name__ == "__main__":
    unittest.main()
