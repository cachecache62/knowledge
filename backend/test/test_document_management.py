"""文档下载/删除：内存数据库、本地临时文件和存储替身，不连接开发服务。"""

import hashlib
import importlib
import importlib.util
import os
import re
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
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
from utils import document_visibility
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

    def stat_object(self, bucket: str, key: str) -> object:
        if self.fail_delete:
            raise RuntimeError("storage unavailable")
        if key not in self.objects:
            raise S3Error(response=None, code="NoSuchKey", message="missing", resource=key, request_id="test", host_id="test")
        return object()


class VectorStore:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, str]]] = {"chunks": [], "names": []}
        self.calls: list[tuple[str, str]] = []

    def has_collection(self, name: str) -> bool:
        return name in self.rows

    def query(self, *, collection_name: str, filter: str, **kwargs: Any) -> list[dict[str, str]]:
        value = re.search("'([^']+)'", filter).group(1)
        if filter.startswith("source_task_id =="):
            return [row for row in self.rows[collection_name] if row.get("source_task_id") == value][:1]
        return [row for row in self.rows[collection_name] if row["file_title"] == value and "source_task_id" not in row][:1]

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
        self._patch(patch.object(document_visibility, "get_import_registry", return_value=self.registry))
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
        self.assertEqual(self.client.post(f"/documents/{task_id}/restore").status_code, 404)
        self.assertEqual(self.client.get("/documents/trash").json()["total"], 0)
        self.assertTrue(path.exists())
        self.assertFalse(self.vectors.calls)
        del self.api.app.dependency_overrides[get_current_user]
        self.assertEqual(self.client.get(f"/documents/{task_id}/download").status_code, 401)
        self.assertEqual(self.client.delete(f"/documents/{task_id}").status_code, 401)
        self.assertEqual(self.client.post(f"/documents/{task_id}/restore").status_code, 401)
        self.assertEqual(self.client.get("/documents/trash").status_code, 401)

    def test_deletion_preserves_resources_and_is_idempotent(self) -> None:
        task_id, path, key = self.document()
        record = self.registry.get_document(self.user.id, task_id)
        self.objects.objects["images/manual/one.png"] = b"image"
        update_task_status(task_id, "completed")
        response = self.client.delete(f"/documents/{task_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(path.is_file())
        self.assertIn(key, self.objects.objects)
        self.assertIn("images/manual/one.png", self.objects.objects)
        self.assertTrue(all(rows[0]["source_task_id"] == task_id for rows in self.vectors.rows.values()))
        self.assertFalse(self.vectors.calls)
        self.assertTrue(self.store.owns_import(self.user.id, task_id))
        with self.engine.connect() as connection:
            retained = connection.execute(select(documents).where(documents.c.task_id == task_id)).mappings().one()
        self.assertTrue(retained["is_deleted"])
        self.assertIsNone(retained["active_hash"])
        self.assertIsNotNone(retained["deleted_at"])
        self.assertEqual(retained["deleted_by"], self.user.id)
        self.assertEqual(retained["status"], record["status"])
        self.assertEqual(retained["file_hash"], record["file_hash"])
        self.assertEqual(retained["created_at"], record["created_at"])
        self.assertEqual(retained["storage_path"], record["storage_path"])
        self.assertFalse(get_task_status(task_id))
        self.assertEqual(self.registry.list_documents(self.user.id)["total"], 0)
        self.assertEqual(self.client.get(f"/documents/{task_id}/download").status_code, 404)
        calls_before = len(self.vectors.calls)
        self.assertEqual(self.client.delete(f"/documents/{task_id}").status_code, 200)
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
        self.assertEqual(self.client.delete(f"/documents/{task_id}").status_code, 200)
        self.assertEqual(self.client.post(f"/documents/{task_id}/restore", headers={"Origin": "https://untrusted.example"}).status_code, 403)
        self.assertEqual(self.client.get("/documents/trash").json()["total"], 1)

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

    def test_move_to_trash_does_not_depend_on_external_storage(self) -> None:
        task_id, path, _ = self.document()
        self.objects.fail_delete = True
        self.assertEqual(self.client.delete(f"/documents/{task_id}").status_code, 200)
        self.assertTrue(path.exists())
        self.assertTrue(self.store.owns_import(self.user.id, task_id))
        self.assertEqual(self.registry.list_documents(self.user.id, deleted=True)["total"], 1)
        self.objects.fail_delete = False
        self.assertEqual(self.client.delete(f"/documents/{task_id}").status_code, 200)

    def test_same_named_tagged_document_and_shared_images_are_preserved(self) -> None:
        task_id, path, _ = self.document()
        other_id, other_path, _ = self.document(owner=self.other.id)
        self.objects.objects["images/manual/one.png"] = b"shared"
        response = self.client.delete(f"/documents/{task_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(path.exists())
        self.assertTrue(other_path.exists())
        self.assertIn("images/manual/one.png", self.objects.objects)
        self.assertTrue(all({row["source_task_id"] for row in rows} == {task_id, other_id} for rows in self.vectors.rows.values()))
        hits = [{"entity": row} for row in self.vectors.rows["chunks"]]
        self.assertEqual([hit["entity"]["source_task_id"] for hit in document_visibility.filter_visible_documents(hits)], [other_id])

    def test_legacy_deletion_and_same_title_conflict(self) -> None:
        task_id, path, _ = self.document(legacy=True)
        self.assertEqual(self.client.delete(f"/documents/{task_id}").status_code, 200)
        self.assertTrue(path.exists())
        self.assertFalse(self.vectors.calls)
        self.assertEqual(self.client.post(f"/documents/{task_id}/restore").status_code, 200)
        self.assertIn("manual", document_visibility.visible_sources()[1])
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
        self.assertEqual(self.client.delete(f"/documents/{task_id}").status_code, 200)
        self.assertEqual(self.client.post(f"/documents/{task_id}/restore").status_code, 409)
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
        self.assertTrue({"storage_path", "object_key", "vector_key", "is_deleted", "deleted_at", "deleted_by"}.issubset({column["name"] for column in inspect(engine).get_columns(documents.name)}))
        with engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT filename FROM kb_import_documents")).scalar_one(), "original.pdf")
            self.assertEqual(connection.execute(text("SELECT is_deleted FROM kb_import_documents")).scalar_one(), 0)

    def test_trash_search_pagination_and_owner_isolation(self) -> None:
        first, _, _ = self.document("first.pdf")
        second, _, _ = self.document("second.pdf", status="failed")
        other, _, _ = self.document("other.pdf", owner=self.other.id)
        self.client.delete(f"/documents/{first}")
        self.client.delete(f"/documents/{second}")
        self.registry.delete_document(self.other.id, other)
        page = self.client.get("/documents/trash?page_size=1").json()
        self.assertEqual(page["total"], 2)
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(page["items"][0]["task_id"], second)
        self.assertTrue(page["items"][0]["can_restore"])
        with self.engine.connect() as connection:
            stored_time = connection.execute(select(documents.c.deleted_at).where(documents.c.task_id == second)).scalar_one()
        # API 可携带 UTC 或北京时间偏移，比较实际时间点。
        self.assertEqual(datetime.fromisoformat(page["items"][0]["deleted_at"]).astimezone(timezone.utc).replace(tzinfo=None), stored_time)
        self.assertEqual(self.client.get("/documents/trash?keyword=first").json()["items"][0]["task_id"], first)
        self.assertEqual(self.client.get("/documents/trash?status=failed").json()["total"], 1)
        self.assertEqual(self.client.get("/documents/trash?page_size=101").status_code, 422)

    def test_restore_returns_same_file_and_reenables_retrieval(self) -> None:
        task_id, path, _ = self.document()
        content = path.read_bytes()
        local = {"source": "local", "source_task_id": task_id, "content": "knowledge"}
        web = {"source": "web", "content": "web"}
        self.assertEqual(document_visibility.filter_visible_documents([local, web]), [local, web])
        self.client.delete(f"/documents/{task_id}")
        self.assertNotIn(task_id, document_visibility.build_retrieval_filter())
        self.assertEqual(document_visibility.filter_visible_documents([local, web]), [web])
        response = self.client.post(f"/documents/{task_id}/restore")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get("/documents/trash").json()["total"], 0)
        self.assertEqual(self.client.get(f"/documents/{task_id}/download").content, content)
        restored = self.registry.get_document(self.user.id, task_id)
        self.assertIsNone(restored["deleted_at"])
        self.assertIsNone(restored["deleted_by"])
        self.assertEqual(restored["active_hash"], restored["file_hash"])
        self.assertEqual(document_visibility.filter_visible_documents([local, web]), [local, web])
        self.assertEqual(self.client.post(f"/documents/{task_id}/restore").status_code, 200)

    def test_restore_conflict_rolls_back_and_failed_document_keeps_status(self) -> None:
        task_id, _, _ = self.document()
        file_hash = self.registry.get_document(self.user.id, task_id)["file_hash"]
        self.client.delete(f"/documents/{task_id}")
        self.registry.claim(str(uuid.uuid4()), "duplicate.pdf", file_hash)
        response = self.client.post(f"/documents/{task_id}/restore")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.client.get("/documents/trash").json()["total"], 1)
        failed, _, _ = self.document("failed.pdf", status="failed")
        self.client.delete(f"/documents/{failed}")
        self.assertEqual(self.client.post(f"/documents/{failed}/restore").status_code, 200)
        restored = self.registry.get_document(self.user.id, failed)
        self.assertEqual(restored["status"], "failed")
        self.assertIsNone(restored["active_hash"])
        self.assertNotIn(failed, document_visibility.visible_sources()[0])

    def test_old_physical_deletion_is_not_restorable(self) -> None:
        task_id, _, _ = self.document()
        with self.engine.begin() as connection:
            connection.execute(update(documents).where(documents.c.task_id == task_id).values(is_deleted=True, active_hash=None))
        item = self.client.get("/documents/trash").json()["items"][0]
        self.assertFalse(item["can_restore"])
        self.assertIsNone(item["deleted_at"])
        self.assertEqual(self.client.post(f"/documents/{task_id}/restore").status_code, 409)

    def test_restore_checks_backup_missing_files_vectors_and_storage_failure(self) -> None:
        task_id, path, key = self.document()
        self.client.delete(f"/documents/{task_id}")
        path.unlink()
        self.objects.fail_delete = True
        self.assertEqual(self.client.post(f"/documents/{task_id}/restore").status_code, 503)
        self.assertEqual(self.client.get("/documents/trash").json()["total"], 1)
        self.objects.fail_delete = False
        content = self.objects.objects.pop(key)
        self.assertEqual(self.client.post(f"/documents/{task_id}/restore").status_code, 409)
        self.objects.objects[key] = content
        chunks = self.vectors.rows["chunks"]
        self.vectors.rows["chunks"] = []
        self.assertEqual(self.client.post(f"/documents/{task_id}/restore").status_code, 409)
        self.vectors.rows["chunks"] = chunks
        self.assertEqual(self.client.post(f"/documents/{task_id}/restore").status_code, 200)
        self.assertEqual(self.client.get(f"/documents/{task_id}/download").content, content)

    def test_retrieval_filter_excludes_ambiguous_legacy_and_failed_sources(self) -> None:
        legacy, _, _ = self.document("设备'手册.pdf", legacy=True)
        tagged, _, _ = self.document("new.pdf")
        failed, _, _ = self.document("failed.pdf", status="failed")
        expr = document_visibility.build_retrieval_filter('item_name in ["设备"]')
        self.assertIn("(exists source_task_id)", expr)
        self.assertIn("(not (exists source_task_id))", expr)
        self.assertIn('item_name in ["设备"]', expr)
        self.assertIn(tagged, expr)
        self.assertNotIn(failed, expr)
        self.assertIn("设备'手册", expr)
        self.document("设备'手册.md")
        self.assertFalse(document_visibility.visible_sources()[1])
        self.assertNotIn(legacy, document_visibility.visible_sources()[0])

    def test_shared_hybrid_search_filters_both_requests_and_rechecks_results(self) -> None:
        task_id, _, _ = self.document()
        deleted, _, _ = self.document("deleted.pdf")
        self.registry.delete_document(self.user.id, deleted)
        # 执行真实封装，只替换 SDK，不连接 Milvus 或加载任何模型。
        with patch.dict("sys.modules", {
            "pymilvus": protocol_tests.stub_module(
                "pymilvus", MilvusClient=MagicMock(), WeightedRanker=MagicMock(),
                AnnSearchRequest=lambda **kwargs: types.SimpleNamespace(**kwargs),
            ),
        }):
            spec = importlib.util.spec_from_file_location(
                "visibility_milvus", protocol_tests.ROOT / "utils" / "milvus_utils.py",
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        reqs = module.create_hybrid_search_requests([1], {0: 1}, expr='item_name in ["设备"]')
        self.assertEqual(len(reqs), 2)
        self.assertEqual(reqs[0].expr, reqs[1].expr)
        self.assertIn(task_id, reqs[0].expr)
        self.assertNotIn(deleted, reqs[0].expr)
        self.assertIn('item_name in ["设备"]', reqs[0].expr)
        client = MagicMock()
        hits = [{"entity": {"source_task_id": value}} for value in (task_id, deleted)]
        client.hybrid_search.return_value = [hits]
        fields = ["item_name"]
        result = module.hybrid_search(client, "names", reqs, output_fields=fields)
        self.assertEqual(result, [[hits[0]]])
        self.assertEqual(fields, ["item_name"])
        self.assertIn("source_task_id", client.hybrid_search.call_args.kwargs["output_fields"])

        def deleted_during_search(**kwargs: Any) -> list:
            self.registry.delete_document(self.user.id, task_id)
            return [hits]

        client.hybrid_search.side_effect = deleted_during_search
        self.assertEqual(module.hybrid_search(client, "chunks", reqs), [[]])
        with patch.object(self.registry, "retrieval_sources", side_effect=RuntimeError("database unavailable")):
            with self.assertRaises(RuntimeError):
                module.create_hybrid_search_requests([1], {0: 1})
            with self.assertRaises(document_visibility.KnowledgeRetrievalError):
                module.hybrid_search(client, "chunks", reqs)

    def test_deleted_after_rerank_is_removed_before_prompt_generation(self) -> None:
        task_id, _, _ = self.document()
        with patch.dict("sys.modules", {
            "utils.reranker_http_utils": protocol_tests.stub_module("utils.reranker_http_utils", rerank_documents=MagicMock()),
            "utils.llm_utils": protocol_tests.stub_module("utils.llm_utils", get_llm_client=MagicMock()),
            "utils.mongo_history_utils": protocol_tests.stub_module("utils.mongo_history_utils", save_chat_message=MagicMock()),
            "langchain_core.prompts": protocol_tests.stub_module("langchain_core.prompts", load_prompt=MagicMock()),
        }):
            rerank_module = importlib.import_module("processor.query_processor.nodes.node_rerank")
            answer_module = importlib.import_module("processor.query_processor.nodes.node_answer_output")
        rerank = object.__new__(rerank_module.NodeRerank)
        state = {
            "session_id": str(uuid.uuid4()), "item_names": [], "original_query": "question",
            "rrf_chunks": [{"source_task_id": task_id, "file_title": "manual", "content": "deleted-secret", "chunk_id": 1}],
            "web_search_docs": [{"snippet": "web-reference", "title": "web", "url": "https://example.com"}],
        }
        state["reranked_docs"] = rerank._step_1_merge_multi_source_docs(state)
        self.assertEqual(state["reranked_docs"][0]["source_task_id"], task_id)
        self.registry.delete_document(self.user.id, task_id)
        answer = object.__new__(answer_module.NodeAnswerOutput)
        answer._step_3_generate_response = MagicMock()
        with patch.object(answer_module, "add_done_task"):
            answer.process(state)
        self.assertNotIn("deleted-secret", state["prompt"])
        self.assertIn("web-reference", state["prompt"])
        self.assertEqual(len(state["reranked_docs"]), 1)

    def test_product_lookup_error_is_not_reported_as_no_matching_product(self) -> None:
        failure = document_visibility.KnowledgeRetrievalError("检索不可用")
        with patch.dict("sys.modules", {
            "utils.embedding_utils": protocol_tests.stub_module("utils.embedding_utils", generate_embeddings=MagicMock(return_value={"dense": [[1]], "sparse": [{0: 1}]})),
            "utils.milvus_utils": protocol_tests.stub_module("utils.milvus_utils", get_milvus_client=MagicMock(return_value=object()),
                create_hybrid_search_requests=MagicMock(return_value=[]), hybrid_search=MagicMock(side_effect=failure)),
            "utils.mongo_history_utils": protocol_tests.stub_module("utils.mongo_history_utils", get_recent_messages=MagicMock(),
                save_chat_message=MagicMock(), update_message_item_names=MagicMock()),
        }):
            spec = importlib.util.spec_from_file_location("lookup_error_test", protocol_tests.ROOT / "processor/query_processor/nodes/node_item_name_confirm.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        node = object.__new__(module.NodeItemNameConfirm)
        with self.assertRaises(document_visibility.KnowledgeRetrievalError):
            node._step_5_vectorize_and_query(["设备"])


if __name__ == "__main__":
    unittest.main()
