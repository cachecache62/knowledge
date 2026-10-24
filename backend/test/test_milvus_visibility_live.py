"""显式启用的真实 Milvus 只读回归：验证服务端过滤语法，不调用模型或写入数据。

PowerShell: $env:RUN_MILVUS_READONLY_TESTS='1'; .\.venv\Scripts\python.exe -m unittest test.test_milvus_visibility_live
"""

import json
import os
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.getenv("RUN_MILVUS_READONLY_TESTS") == "1", "需显式启用真实数据库的只读检查")
class LiveVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from pymilvus import MilvusClient
        from sqlalchemy import create_engine
        from config.auth_config import get_auth_config
        from config.milvus_config import milvus_config
        from utils.import_registry import ImportRegistry

        cls.engine = create_engine(get_auth_config().database_url(), connect_args={"connect_timeout": 5})
        cls.addClassCleanup(cls.engine.dispose)
        cls.registry = ImportRegistry(cls.engine)  # 不运行 create_schema，保持只读。
        cls.client = MilvusClient(uri=milvus_config.milvus_url, timeout=5)
        cls.addClassCleanup(cls.client.close)
        cls.collections = [milvus_config.item_name_collection, milvus_config.chunks_collection]

    def count(self, collection: str, expression: str) -> int:
        result = self.client.query(collection_name=collection, filter=expression, output_fields=["count(*)"], timeout=5)
        return result[0]["count(*)"]

    def test_current_visible_documents_filter_is_accepted_by_server(self) -> None:
        from utils import document_visibility as visibility

        with patch.object(visibility, "get_import_registry", return_value=self.registry):
            expression = visibility.build_retrieval_filter()
            ids, titles = visibility.visible_sources()
        for collection in self.collections:
            with self.subTest(collection=collection):
                count = self.count(collection, expression)
                records = self.client.query(collection_name=collection, filter=expression,
                                            output_fields=["source_task_id", "file_title"], limit=10, timeout=5)
                self.assertGreaterEqual(count, len(records))
                for record in records:
                    self.assertTrue(record.get("source_task_id") in ids or (
                        record.get("source_task_id") is None and record.get("file_title") in titles))

    def test_empty_and_legacy_sources_and_combined_filter_parse_correctly(self) -> None:
        from utils import document_visibility as visibility

        for collection in self.collections:
            with self.subTest(collection=collection):
                with patch.object(visibility, "visible_sources", return_value=(set(), set())):
                    self.assertEqual(self.count(collection, visibility.build_retrieval_filter()), 0)
                records = self.client.query(collection_name=collection, filter="not (exists source_task_id)",
                                            output_fields=["file_title", "item_name"], limit=1, timeout=5)
                if not records:
                    continue
                title = records[0]["file_title"]
                item_name = records[0]["item_name"]
                additional = "item_name in " + json.dumps([item_name], ensure_ascii=False)
                with patch.object(visibility, "visible_sources", return_value=(set(), {title})):
                    expression = visibility.build_retrieval_filter(additional)
                self.assertGreater(self.count(collection, expression), 0)
