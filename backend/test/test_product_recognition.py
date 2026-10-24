"""产品识别异常不能伪装成未找到产品；隔离模型、向量服务和 MongoDB。"""

import importlib.util
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from test.test_api_frontend import stub_module
from utils.document_visibility import KnowledgeRetrievalError


class RecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with patch.dict("sys.modules", {
            "utils.embedding_utils": stub_module("utils.embedding_utils", generate_embeddings=MagicMock()),
            "utils.milvus_utils": stub_module("utils.milvus_utils", get_milvus_client=MagicMock(),
                create_hybrid_search_requests=MagicMock(), hybrid_search=MagicMock()),
            "utils.mongo_history_utils": stub_module("utils.mongo_history_utils", get_recent_messages=MagicMock(return_value=[]),
                save_chat_message=MagicMock(return_value="message-id"), update_message_item_names=MagicMock()),
        }):
            path = Path(__file__).resolve().parents[1] / "processor/query_processor/nodes/node_item_name_confirm.py"
            spec = importlib.util.spec_from_file_location("recognition_unit_test", path)
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)

    def setUp(self) -> None:
        self.node = object.__new__(self.module.NodeItemNameConfirm)
        self.model = MagicMock()
        patcher = patch.object(self.module, "ChatOpenAI", return_value=self.model)
        patcher.start()
        self.addCleanup(patcher.stop)

    def extract(self, content: str) -> dict:
        self.model.invoke.return_value = SimpleNamespace(content=content)
        return self.node._step_4_extract_info("原始问题", [])

    def test_quota_failure_propagates_instead_of_entering_no_product_branch(self) -> None:
        failure = RuntimeError("403 AllocationQuota.FreeTierOnly")
        failure.status_code = 403
        failure.body = {"code": "AllocationQuota.FreeTierOnly"}
        self.model.invoke.side_effect = failure
        self.node._step_5_vectorize_and_query = MagicMock()
        self.node._step_7_check_confirmation = MagicMock()
        with self.assertRaisesRegex(KnowledgeRetrievalError, "免费额度已用尽"):
            self.node.process({"session_id": "quota-test", "original_query": "Aolynk CB304n是什么"})
        self.node._step_5_vectorize_and_query.assert_not_called()
        self.node._step_7_check_confirmation.assert_not_called()

    def test_fenced_json_and_missing_rewritten_query_keep_product_names(self) -> None:
        result = self.extract('```json\n{"item_names": ["Aolynk CB304n", "Aolynk CB304n", "  "]}\n```')
        self.assertEqual(result, {"item_names": ["AolynkCB304n"], "rewritten_query": "原始问题"})
        self.assertEqual(self.extract('{"item_names": ["H3C"], "rewritten_query": null}')["rewritten_query"], "原始问题")

    def test_valid_empty_names_are_distinct_from_invalid_model_output(self) -> None:
        self.assertEqual(self.extract('{"item_names": [], "rewritten_query": "问题"}')["item_names"], [])
        for content in ('not-json', '[]', '{"rewritten_query": "问题"}', '{"item_names": "Aolynk"}', '{"item_names": [null]}'):
            with self.subTest(content=content), self.assertRaises(KnowledgeRetrievalError):
                self.extract(content)

    def test_model_authentication_and_missing_model_errors_are_actionable(self) -> None:
        for status, expected in ((401, "鉴权失败"), (403, "无权调用"), (404, "ITEM_MODEL")):
            failure = RuntimeError("provider error")
            failure.status_code = status
            failure.body = {"error": {"code": "AccessDenied"}}
            self.model.invoke.side_effect = failure
            with self.subTest(status=status), self.assertRaisesRegex(KnowledgeRetrievalError, expected):
                self.node._step_4_extract_info("问题", [])

    def answer_module(self):
        # 加载实际答案节点；模型和 MongoDB 仍使用替身。
        with patch.dict("sys.modules", {
            "utils.llm_utils": stub_module("utils.llm_utils", get_llm_client=MagicMock()),
            "utils.mongo_history_utils": stub_module("utils.mongo_history_utils", save_chat_message=MagicMock()),
            "langchain_core.prompts": stub_module("langchain_core.prompts", load_prompt=MagicMock()),
        }):
            path = Path(__file__).resolve().parents[1] / "processor/query_processor/nodes/node_answer_output.py"
            spec = importlib.util.spec_from_file_location("answer_history_unit_test", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return module

    @staticmethod
    def history_writer(records):
        def save(session_id, role, text, **fields):
            message_id = fields.pop("message_id", None)
            document = {"session_id": session_id, "role": role, "text": text, **fields}
            if message_id:
                records[int(message_id) - 1] = document
                return message_id
            records.append(document)
            return str(len(records))
        return save

    def test_clarification_and_no_match_answers_are_saved_once_in_both_modes(self) -> None:
        answer_module = self.answer_module()
        for streaming in (False, True):
            for score in (None, .7):
                with self.subTest(streaming=streaming, score=score):
                    records = []
                    save = self.history_writer(records)
                    self.node._step_4_extract_info = MagicMock(return_value={
                        "item_names": [] if score is None else ["Aolynk"], "rewritten_query": "问题",
                    })
                    self.node._step_5_vectorize_and_query = MagicMock(return_value=[{
                        "extracted_name": "Aolynk", "matches": [{"item_name": "AolynkCB304n", "score": score}],
                    }])
                    state = {"session_id": "history-test", "original_query": "问题", "is_stream": streaming}
                    with patch.object(self.module, "save_chat_message", side_effect=save), \
                            patch.object(self.module, "add_done_task"), \
                            patch.object(answer_module, "save_chat_message", side_effect=save), \
                            patch.object(answer_module, "add_done_task"), \
                            patch.object(answer_module, "set_task_result"), \
                            patch.object(answer_module, "push_to_session"):
                        self.node.process(state)
                        self.assertEqual(len(records), 1)  # 此阶段只更新同一条用户消息。
                        answer = object.__new__(answer_module.NodeAnswerOutput)
                        answer.process(state)
                    self.assertEqual([item["role"] for item in records], ["user", "assistant"])
                    self.assertEqual(records[0]["rewritten_query"], "问题")
                    self.assertEqual(records[1]["text"], state["answer"])
                    self.assertEqual(records[1]["image_urls"], [])
                    answer_module.get_llm_client.assert_not_called()

    def test_generated_answers_and_repeated_questions_keep_one_reply_per_turn(self) -> None:
        answer_module = self.answer_module()
        records = []
        save = self.history_writer(records)
        self.node._step_4_extract_info = MagicMock(return_value={"item_names": ["Aolynk"], "rewritten_query": "问题"})
        self.node._step_5_vectorize_and_query = MagicMock(return_value=[{
            "extracted_name": "Aolynk", "matches": [{"item_name": "AolynkCB304n", "score": .95}],
        }])
        with patch.object(self.module, "save_chat_message", side_effect=save), \
                patch.object(self.module, "add_done_task"), \
                patch.object(answer_module, "save_chat_message", side_effect=save), \
                patch.object(answer_module, "add_done_task"):
            for _ in range(2):
                state = {"session_id": "history-test", "original_query": "相同问题", "is_stream": False}
                self.node.process(state)
                self.assertFalse(state["answer"])
                answer = object.__new__(answer_module.NodeAnswerOutput)
                answer._step_2_construct_prompt = MagicMock(return_value="prompt")
                answer._step_3_generate_response = MagicMock(side_effect=lambda state, prompt: state.update(answer="生成的回答"))
                answer.process(state)
        self.assertEqual([item["role"] for item in records], ["user", "assistant", "user", "assistant"])


if __name__ == "__main__":
    unittest.main()
