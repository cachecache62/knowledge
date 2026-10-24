"""导入轨迹使用真实数据库操作和节点包装，外部存储与模型均隔离。"""

import json
import logging
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from auth.dependencies import get_current_user
from auth.store import AuthStore, get_auth_store
from processor.import_processor.base import BaseNode
from processor.import_processor.exceptions import ImportProcessError
from processor.import_processor.nodes.node_enrty import NodeEntry
from processor.import_processor.state import ImportGraphState
from test import test_api_frontend as protocol
from utils.import_registry import ImportRegistry
from utils.import_trace import ImportTraceRecorder, ImportTraceStore, bounded_snapshot, now, safe_text, snapshot, trace_scope, traces


class SplitNode(BaseNode):
    name = "node_document_split"

    def process(self, state: dict) -> dict:
        state["chunks"] = [{"content": "设备说明", "dense_vector": [0.1] * 1024}]
        return state


class FailedNode(BaseNode):
    name = "node_document_split"

    def process(self, state: dict) -> dict:
        raise ValueError("文档切分失败")


class ImportTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        protocol.ApiFrontendTests.setUpClass()
        cls.api = protocol.ApiFrontendTests.import_api

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        self.auth = AuthStore(self.engine)
        self.auth.create_schema()
        self.user = self.auth.create_user("trace@example.com", "test-hash")
        self.other = self.auth.create_user("other-trace@example.com", "test-hash")
        self.registry = ImportRegistry(self.engine)
        self.registry.create_schema()
        self.store = ImportTraceStore(self.engine)
        self.store.create_schema()
        self.api.app.dependency_overrides[get_current_user] = lambda: self.user
        self.api.app.dependency_overrides[get_auth_store] = lambda: self.auth
        self.addCleanup(self.api.app.dependency_overrides.clear)
        self.client = TestClient(self.api.app)
        self.addCleanup(self.client.close)
        (protocol.ROOT / "tmp").mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=protocol.ROOT / "tmp", prefix="trace-test-")
        self.addCleanup(self.directory.cleanup)
        self.patch(patch.dict(os.environ, {"DATA_BASED_ROOT_DIR": self.directory.name}))
        self.patch(patch.object(self.api, "get_import_registry", return_value=self.registry))
        self.minio = MagicMock()
        self.patch(patch.object(self.api, "get_minio_client", return_value=self.minio))

    def patch(self, patcher: object) -> None:
        patcher.start()
        self.addCleanup(patcher.stop)

    def upload(self, filename: str = "设备.md", failure: bool = False) -> str:
        def run(state: dict, stream: bool):
            state = NodeEntry()(state)
            yield {"node_entry": state}
            state = (FailedNode() if failure else SplitNode())(state)
            yield {"node_document_split": state}

        workflow = MagicMock()
        workflow.run.side_effect = run
        with patch.object(self.api, "KBImportWorkflow", return_value=workflow):
            response = self.client.post("/upload", files={"files": (filename, b"# manual " + uuid.uuid4().bytes)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["task_ids"]), 1)
        return response.json()["task_ids"][0]

    def test_upload_persists_timeline_input_output_and_markdown_route(self) -> None:
        task_id = self.upload()
        response = self.client.get(f"/import-traces/{task_id}")
        self.assertEqual(response.status_code, 200, response.text)
        trace = response.json()
        self.assertEqual(trace["status"], "completed")
        self.assertEqual(trace["route"], "markdown")
        self.assertEqual(trace["user_id"], self.user.id)
        self.assertEqual(len(trace["trace_id"]), 32)
        self.assertTrue(trace["started_at"].endswith("+00:00"))
        nodes = {node["key"]: node for node in trace["nodes"]}
        self.assertEqual(nodes["upload_file"]["status"], "success")
        self.assertEqual(nodes["backup_file"]["output"], {"stored": True})
        self.assertEqual(nodes["node_pdf_to_md"]["status"], "skipped")
        self.assertEqual(nodes["node_entry"]["status"], "success")
        self.assertEqual(nodes["node_entry"]["upstream"], "backup_file")
        self.assertEqual(nodes["node_entry"]["input"]["import_file_path"], "设备.md")
        self.assertNotIn("file_title", nodes["node_entry"]["input"])
        self.assertEqual(nodes["node_entry"]["output"]["file_title"], "设备")
        self.assertEqual(nodes["node_document_split"]["output"]["chunks"][0]["dense_vector"]["size"], 1024)
        self.assertGreaterEqual(trace["duration_ms"], 0)
        # 新存储实例仍能读取，记录不依赖进程内任务字典。
        self.assertEqual(ImportTraceStore(self.engine).get(self.user.id, task_id)["trace_id"], trace["trace_id"])
        self.assertNotIn(self.directory.name, response.text)

    def test_node_failure_records_error_and_skips_remaining_nodes(self) -> None:
        task_id = self.upload("failed.pdf", failure=True)
        trace = self.client.get(f"/import-traces/{task_id}").json()
        self.assertEqual(trace["status"], "failed")
        self.assertIn("文档切分失败", trace["error"])
        nodes = {node["key"]: node for node in trace["nodes"]}
        self.assertEqual(nodes["node_document_split"]["status"], "failed")
        self.assertIn("文档切分失败", nodes["node_document_split"]["error"])
        self.assertEqual(nodes["node_bge_embedding"]["status"], "skipped")
        self.assertEqual(self.client.get(f"/status/{task_id}").json()["status"], "failed")

    def test_backup_fallback_and_handled_node_warnings_are_visible(self) -> None:
        self.minio.fput_object.side_effect = RuntimeError("备份不可用 token=hidden-value")
        task_id = self.upload()
        trace = self.store.get(self.user.id, task_id)
        self.assertEqual(trace["status"], "completed")
        self.assertEqual(trace["warning_count"], 1)
        backup = next(node for node in trace["nodes"] if node["key"] == "backup_file")
        self.assertEqual(backup["status"], "warning")
        self.assertNotIn("hidden-value", json.dumps(trace))

        class WarningNode(BaseNode):
            name = "node_item_name_recognition"

            def process(self, state: dict) -> dict:
                self.logger.warning("主体识别失败，使用文件标题")
                return state

        with trace_scope(ImportTraceRecorder(self.store, task_id)):
            WarningNode()({"task_id": task_id})
        node = next(node for node in self.store.get(self.user.id, task_id)["nodes"] if node["key"] == WarningNode.name)
        self.assertEqual(node["status"], "warning")
        self.assertIn("主体识别失败", node["warnings"][0])

    def test_list_search_pagination_export_and_account_isolation(self) -> None:
        first = self.upload("first.md")
        second = self.upload("second.pdf", failure=True)
        page = self.client.get("/import-traces?page_size=1").json()
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["items"][0]["task_id"], second)
        self.assertNotIn("nodes", page["items"][0])
        self.assertEqual(self.client.get("/import-traces?keyword=first").json()["total"], 1)
        self.assertEqual(self.client.get("/import-traces?status=failed").json()["total"], 1)
        export = self.client.get(f"/import-traces/{first}/export")
        self.assertEqual(export.status_code, 200)
        self.assertIn("attachment", export.headers["content-disposition"])
        self.assertEqual(export.json()["task_id"], first)
        self.assertEqual(export.headers["cache-control"], "private, no-store")
        self.api.app.dependency_overrides[get_current_user] = lambda: self.other
        self.assertEqual(self.client.get("/import-traces").json()["total"], 0)
        self.assertEqual(self.client.get(f"/import-traces/{first}").status_code, 404)
        self.assertEqual(self.client.get(f"/import-traces/{first}/export").status_code, 404)
        del self.api.app.dependency_overrides[get_current_user]
        self.assertEqual(self.client.get("/import-traces").status_code, 401)
        self.assertEqual(self.client.get(f"/import-traces/{first}/export").status_code, 401)

    def test_validation_history_and_soft_deletion_do_not_fabricate_or_remove_traces(self) -> None:
        self.assertEqual(self.client.get("/import-traces?page_size=101").status_code, 422)
        self.assertEqual(self.client.get("/import-traces/not-a-uuid").status_code, 422)
        self.assertEqual(self.client.get(f"/import-traces/{uuid.uuid4()}").status_code, 404)
        self.assertEqual(self.client.get("/import-traces").json()["total"], 0)
        task_id = self.upload()
        self.assertEqual(self.client.delete(f"/documents/{task_id}").status_code, 200)
        self.assertEqual(self.client.get(f"/import-traces/{task_id}").status_code, 200)

    def test_snapshots_are_bounded_redacted_and_do_not_mutate_state(self) -> None:
        state = {"md_content": "资料" * 50000, "chunks": [{"dense_vector": list(range(1024))}] * 100,
                 "api_key": "private", "nested": {"password": "private"},
                 "download": "https://example.com/file?token=private&signature=private"}
        captured = bounded_snapshot(state)
        encoded = json.dumps(captured, ensure_ascii=False)
        self.assertLess(len(encoded.encode("utf-8")), 24000)
        self.assertNotIn("private", encoded)
        self.assertEqual(captured["chunks"]["count"], 100)
        self.assertEqual(len(state["chunks"][0]["dense_vector"]), 1024)
        self.assertTrue(captured["md_content"]["truncated"])
        self.assertEqual(snapshot({"sparse_vector": {1: .1, 100: .3}})["sparse_vector"]["size"], 2)
        error = safe_text('request {"api_key": "hidden-json-value"} https://user:hidden-auth@example.com/file?signature=hidden-signature')
        for secret in ("hidden-json-value", "hidden-auth", "hidden-signature"):
            self.assertNotIn(secret, error)

    def test_mysql_schema_keeps_microsecond_timing_and_large_node_snapshots(self) -> None:
        from sqlalchemy.dialects import mysql
        from sqlalchemy.schema import CreateTable

        ddl = str(CreateTable(traces).compile(dialect=mysql.dialect()))
        self.assertIn("DATETIME(6)", ddl)
        self.assertIn("MEDIUMTEXT", ddl)

    def test_context_and_input_snapshot_work_through_real_langgraph(self) -> None:
        from langgraph.graph import END, StateGraph

        task_id = str(uuid.uuid4())
        path = Path(self.directory.name) / "graph.md"
        path.write_text("# manual", encoding="utf-8")
        self.store.start(task_id, self.user.id, path.name, now())
        graph = StateGraph(ImportGraphState)
        graph.add_node("entry", NodeEntry())
        graph.add_node("split", SplitNode())
        graph.set_entry_point("entry")
        graph.add_edge("entry", "split")
        graph.add_edge("split", END)
        with trace_scope(ImportTraceRecorder(self.store, task_id)):
            list(graph.compile().stream({"task_id": task_id, "import_file_path": str(path)}))
        nodes = {node["key"]: node for node in self.store.get(self.user.id, task_id)["nodes"]}
        self.assertEqual(nodes["node_entry"]["status"], "success")
        self.assertEqual(nodes["node_document_split"]["status"], "success")

    def test_trace_write_failure_keeps_original_node_behavior(self) -> None:
        task_id = str(uuid.uuid4())
        recorder = ImportTraceRecorder(self.store, task_id)
        with patch.object(self.store, "change", side_effect=RuntimeError("trace unavailable")), trace_scope(recorder):
            state = SplitNode()({"task_id": task_id})
            self.assertEqual(len(state["chunks"]), 1)
            with self.assertRaises(ImportProcessError):
                FailedNode()({"task_id": task_id})


if __name__ == "__main__":
    unittest.main()
