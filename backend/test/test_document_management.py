"""文档下载/删除：内存数据库、本地临时文件和存储替身，不连接开发服务。"""

import hashlib
import importlib
import os
import re
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from minio.error import S3Error
from sqlalchemy import create_engine, inspect, select, text, update
from sqlalchemy.pool import StaticPool

from auth.dependencies import get_current_user
from auth.store import AuthStore, beijing_now, get_auth_store
from config.auth_config import get_auth_config
from test import test_api_frontend as protocol_tests
from utils.import_registry import ImportRegistry, documents
from utils.task_utils import get_task_status, update_task_status


class ObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.responses: list[MagicMock] = []
        self.fail_delete = False

    def get_object(self, bucket: str, key: str) -> MagicMock:
        if key not in self.objects:
            raise S3Error(response=None, code="NoSuchKey", message="missing", resource=key, request_id="test", host_id="test")
        response = MagicMock()
        response.stream.return_value = iter([self.objects[key]])
        self.responses.append(response)
        return response

    def list_objects(self, bucket: str, prefix: str, recursive: bool = False) -> list[types.SimpleNamespace]:
        return [types.SimpleNamespace(object_name=key) for key in list(self.objects) if key.startswith(prefix)]

    def remove_object(self, bucket: str, key: str) -> None:
        if self.fail_delete:
            raise RuntimeError("storage unavailable")
        self.objects.pop(key, None)


class VectorStore:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, str]]] = {"chunks": [], "names": []}
        self.calls: list[tuple[str, str]] = []

    def has_collection(self, name: str) -> bool:
        return name in self.rows

    def delete(self, *, collection_name: str, filter: str) -> None:
        self.calls.append((collection_name, filter))
        if filter.startswith("source_task_id =="):
            task_id = re.search("'([^']+)'", filter).group(1)
            self.rows[collection_name] = [row for row in self.rows[collection_name] if row.get("source_task_id") != task_id]
        else:
            title = re.search("'([^']+)'", filter).group(1)
            self.rows[collection_name] = [row for row in self.rows[collection_name]
                                          if not (row["file_title"] == title and "source_task_id" not in row)]


class DocumentManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        protocol_tests.ApiFrontendTests.setUpClass()
        cls.api = protocol_tests.ApiFrontendTests.import_api

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        self.store = AuthStore(self.engine)
        self.store.create_schema()
        self.user = self.store.create_user("owner@example.com", "test-hash")
        self.other = self.store.create_user("other@example.com", "test-hash")
        self.registry = ImportRegistry(self.engine)
        self.registry.create_schema()
        self.api.app.dependency_overrides[get_current_user] = lambda: self.user
        self.api.app.dependency_overrides[get_auth_store] = lambda: self.store
        self.addCleanup(self.api.app.dependency_overrides.clear)
        self.client = TestClient(self.api.app)
        self.addCleanup(self.client.close)
        (protocol_tests.ROOT / "tmp").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=protocol_tests.ROOT / "tmp", prefix="document-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.objects = ObjectStore()
        self.vectors = VectorStore()
        self._patch(patch.dict(os.environ, {"DATA_BASED_ROOT_DIR": str(self.root)}))
        self._patch(patch.object(self.api, "get_import_registry", return_value=self.registry))
        self._patch(patch.object(self.api, "get_minio_client", return_value=self.objects))
        self._patch(patch.dict("sys.modules", {
            "utils.minio_utils": protocol_tests.stub_module("utils.minio_utils", get_minio_client=lambda: self.objects),
            "config.minio_config": protocol_tests.stub_module("config.minio_config", minio_config=types.SimpleNamespace(bucket_name="test-bucket", img_dir="images")),
            "config.milvus_config": protocol_tests.stub_module("config.milvus_config", milvus_config=types.SimpleNamespace(chunks_collection="chunks", item_name_collection="names")),
            "utils.milvus_utils": protocol_tests.stub_module("utils.milvus_utils", get_milvus_client=lambda: self.vectors, escape_milvus_string=lambda value: value.replace("'", "\\'")),
        }))

    def _patch(self, patcher: Any) -> None:
        patcher.start()
        self.addCleanup(patcher.stop)

    def document(self, filename: str = "manual.pdf", *, owner: int | None = None, status: str = "completed", legacy: bool = False) -> tuple[str, Path, str]:
        task_id = str(uuid.uuid4())
        content = b"%PDF original " + task_id.encode()
        self.registry.claim(task_id, filename, hashlib.sha256(content).hexdigest())
        self.store.remember_import(owner or self.user.id, task_id)
        path = self.root / "20261023" / task_id / filename
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        key = "pdf_files/" + path.relative_to(self.root).as_posix()
        self.objects.objects[key] = content
        self.registry.remember_storage(task_id, path.relative_to(self.root).as_posix(), key)
        self.registry.set_status(task_id, status) if status != "pending" else None
        if legacy:
            with self.engine.begin() as connection:
                connection.execute(update(documents).where(documents.c.task_id == task_id).values(vector_key=None, storage_path=None, object_key=None))
        for rows in self.vectors.rows.values():
            rows.append({"file_title": Path(filename).stem, **({} if legacy else {"source_task_id": task_id})})
        return task_id, path, key

    def test_download_original_with_unicode_name_without_contacting_backup(self) -> None:
        task_id, path, _ = self.document("设备手册.pdf")
        response = self.client.get(f"/documents/{task_id}/download")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, path.read_bytes())
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn("filename*=utf-8", response.headers["content-disposition"])
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertFalse(self.objects.responses)

    def test_backup_download_and_legacy_path_recovery(self) -> None:
        task_id, path, _ = self.document(legacy=True)
        expected = path.read_bytes()
        path.unlink()
        path.parent.rmdir()
        response = self.client.get(f"/documents/{task_id}/download")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, expected)
        self.objects.responses[0].close.assert_called_once()
        self.objects.responses[0].release_conn.assert_called_once()

    def test_missing_file_has_clear_error(self) -> None:
        task_id, path, key = self.document()
        path.unlink()
        self.objects.objects.pop(key)
        response = self.client.get(f"/documents/{task_id}/download")
        self.assertEqual(response.status_code, 404)
        self.assertIn("不存在", response.json()["detail"])

    def test_owner_checks_and_login_required_before_access(self) -> None:
        task_id, path, _ = self.document()
        self.api.app.dependency_overrides[get_current_user] = lambda: self.other
        self.assertEqual(self.client.get(f"/documents/{task_id}/download").status_code, 404)
        self.assertEqual(self.client.delete(f"/documents/{task_id}").status_code, 404)
        self.assertTrue(path.exists())
        self.assertFalse(self.vectors.calls)
        del self.api.app.dependency_overrides[get_current_user]
        self.assertEqual(self.client.get(f"/documents/{task_id}/download").status_code, 401)
        self.assertEqual(self.client.delete(f"/documents/{task_id}").status_code, 401)

    def test_deletion_cleans_resources_and_soft_deletes_document_record(self) -> None:
        task_id, path, key = self.document()
        record = self.registry.get_document(self.user.id, task_id)
        self.objects.objects["images/manual/one.png"] = b"image"
        update_task_status(task_id, "completed")
        response = self.client.delete(f"/documents/{task_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(path.parent.exists())
        self.assertNotIn(key, self.objects.objects)
        self.assertNotIn("images/manual/one.png", self.objects.objects)
        self.assertTrue(all(not rows for rows in self.vectors.rows.values()))
        self.assertTrue(self.store.owns_import(self.user.id, task_id))
        with self.engine.connect() as connection:
            retained = connection.execute(select(documents).where(documents.c.task_id == task_id)).mappings().one()
        self.assertTrue(retained["is_deleted"])
        self.assertIsNone(retained["active_hash"])
        self.assertEqual(retained["status"], record["status"])
        self.assertEqual(retained["file_hash"], record["file_hash"])
        self.assertEqual(retained["created_at"], record["created_at"])
        self.assertEqual(retained["storage_path"], record["storage_path"])
        self.assertFalse(get_task_status(task_id))
        self.assertEqual(self.registry.list_documents(self.user.id)["total"], 0)
        self.assertEqual(self.client.get(f"/documents/{task_id}/download").status_code, 404)
        calls_before = len(self.vectors.calls)
        self.assertEqual(self.client.delete(f"/documents/{task_id}").status_code, 404)
        self.assertEqual(len(self.vectors.calls), calls_before)
        accepted, _ = self.registry.claim(str(uuid.uuid4()), "manual.pdf", record["file_hash"])
        self.assertTrue(accepted)

    def test_deleted_records_are_excluded_from_pagination_and_status_updates(self) -> None:
        first, _, _ = self.document("first.pdf")
        second, _, _ = self.document("second.pdf")
        self.assertEqual(self.client.delete(f"/documents/{first}").status_code, 200)
        response = self.client.get("/documents?page=1&page_size=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["items"][0]["task_id"], second)
        self.assertEqual(self.client.get("/documents?keyword=first").json()["total"], 0)
        self.registry.set_status(first, "processing")
        self.registry.remember_storage(first, "should-not-change")
        with self.engine.connect() as connection:
            retained = connection.execute(select(documents).where(documents.c.task_id == first)).mappings().one()
        self.assertTrue(retained["is_deleted"])
        self.assertEqual(retained["status"], "completed")
        self.assertNotEqual(retained["storage_path"], "should-not-change")

    def test_processing_document_cannot_be_deleted(self) -> None:
        task_id, path, _ = self.document(status="processing")
        response = self.client.delete(f"/documents/{task_id}")
        self.assertEqual(response.status_code, 409)
        self.assertTrue(path.exists())
        self.assertFalse(self.vectors.calls)

    def test_valid_cookie_and_cross_origin_delete_protection(self) -> None:
        task_id, path, _ = self.document()
        del self.api.app.dependency_overrides[get_current_user]
        self.store.create_login(self.user.id, "document-test-token", beijing_now() + timedelta(hours=6), None)
        self.client.cookies.set(get_auth_config().cookie_name, "document-test-token")
        self.assertEqual(self.client.get(f"/documents/{task_id}/download").status_code, 200)
        response = self.client.delete(f"/documents/{task_id}", headers={"Origin": "https://untrusted.example"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(path.exists())

    def test_import_nodes_attach_task_identity_and_preserve_raw_titles(self) -> None:
        # 导入真实节点方法，但替换向量/embedding 模块，避免模型初始化和网络访问。
        with patch.dict("sys.modules", {
            "utils.embedding_utils": protocol_tests.stub_module("utils.embedding_utils", generate_embeddings=MagicMock()),
        }):
            chunk_module = importlib.import_module("processor.import_processor.nodes.node_import_milvus")
            name_module = importlib.import_module("processor.import_processor.nodes.node_item_name_recognition")
            chunk_node = object.__new__(chunk_module.NodeImportMilvus)
            client = MagicMock()
            task_id = str(uuid.uuid4())
            chunks = [{"file_title": "manual", "dense_vector": [1], "sparse_vector": {0: 1}}]
            chunk_node._step_1_check_input = MagicMock(return_value=(chunks, 1))
            chunk_node._step_2_prepare_collection = MagicMock(return_value=client)
            chunk_node._step_4_insert_data = MagicMock(side_effect=lambda _, values: values)
            chunk_node.process({"task_id": task_id})
            self.assertEqual(chunks[0]["source_task_id"], task_id)
            self.assertIn(task_id, client.delete.call_args.kwargs["filter"])

            name_node = object.__new__(name_module.NodeItemNameRecognition)
            name_node.logger = MagicMock()
            with patch.object(name_module, "get_milvus_client", return_value=client):
                name_node._step_6_save_to_milvus({"task_id": task_id}, "manual's title", "device", None, None)
            self.assertEqual(client.insert.call_args.kwargs["data"]["source_task_id"], task_id)
            self.assertEqual(client.insert.call_args.kwargs["data"]["file_title"], "manual's title")

    def test_failure_retains_record_and_allows_idempotent_retry(self) -> None:
        task_id, path, _ = self.document()
        self.objects.fail_delete = True
        self.assertEqual(self.client.delete(f"/documents/{task_id}").status_code, 503)
        self.assertTrue(path.exists())
        self.assertTrue(self.store.owns_import(self.user.id, task_id))
        self.assertEqual(self.registry.get_document(self.user.id, task_id)["status"], "completed")
        self.assertFalse(self.registry.get_document(self.user.id, task_id)["is_deleted"])
        self.objects.fail_delete = False
        self.assertEqual(self.client.delete(f"/documents/{task_id}").status_code, 200)

    def test_same_named_tagged_document_and_shared_images_are_preserved(self) -> None:
        task_id, path, _ = self.document()
        other_id, other_path, _ = self.document(owner=self.other.id)
        self.objects.objects["images/manual/one.png"] = b"shared"
        response = self.client.delete(f"/documents/{task_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(path.exists())
        self.assertTrue(other_path.exists())
        self.assertIn("images/manual/one.png", self.objects.objects)
        self.assertTrue(all(rows[0]["source_task_id"] == other_id for rows in self.vectors.rows.values()))

    def test_legacy_deletion_and_same_title_conflict(self) -> None:
        task_id, path, _ = self.document(legacy=True)
        self.assertEqual(self.client.delete(f"/documents/{task_id}").status_code, 200)
        self.assertFalse(path.exists())
        self.assertTrue(all("not exists source_task_id" in expression for _, expression in self.vectors.calls))
        first, first_path, _ = self.document(legacy=True)
        self.document(owner=self.other.id)
        response = self.client.delete(f"/documents/{first}")
        self.assertEqual(response.status_code, 409)
        self.assertTrue(first_path.exists())

    def test_path_escape_is_rejected_and_external_file_is_not_touched(self) -> None:
        task_id, path, _ = self.document()
        with self.engine.begin() as connection:
            connection.execute(update(documents).where(documents.c.task_id == task_id).values(storage_path="../outside/manual.pdf"))
        self.assertEqual(self.client.get(f"/documents/{task_id}/download").status_code, 409)
        self.assertEqual(self.client.delete(f"/documents/{task_id}").status_code, 409)
        self.assertTrue(path.exists())
        self.assertFalse(self.vectors.calls)

    def test_old_schema_gets_nullable_storage_metadata_without_losing_rows(self) -> None:
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE kb_import_documents (task_id VARCHAR(36) PRIMARY KEY, scope VARCHAR(64), filename TEXT, file_hash VARCHAR(64), active_hash VARCHAR(64), status VARCHAR(16), created_at DATETIME, updated_at DATETIME, error TEXT)"))
            connection.execute(text("INSERT INTO kb_import_documents (task_id, filename) VALUES ('legacy', 'original.pdf')"))
        registry = ImportRegistry(engine)
        registry.create_schema()
        registry.create_schema()
        self.assertTrue({"storage_path", "object_key", "vector_key", "is_deleted"}.issubset({column["name"] for column in inspect(engine).get_columns(documents.name)}))
        with engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT filename FROM kb_import_documents")).scalar_one(), "original.pdf")
            self.assertEqual(connection.execute(text("SELECT is_deleted FROM kb_import_documents")).scalar_one(), 0)


if __name__ == "__main__":
    unittest.main()
