import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from requests.exceptions import Timeout

from processor.import_processor.import_config import ImportConfig
from processor.import_processor.nodes.node_pdf_to_md import NodePDFToMD
from processor.import_processor.exceptions import ConfigurationError, PdfConversionError


MODULE = "processor.import_processor.nodes.node_pdf_to_md"


def response(payload):
    return Mock(status_code=200, json=Mock(return_value=payload))


def poll_response(state, **fields):
    return response({"code": 0, "data": {"extract_result": [{"state": state, **fields}]}})


class PDFPollingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.pdf = Path(self.temp_dir.name) / "example.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\n")
        self.node = NodePDFToMD(ImportConfig(
            mineru_base_url="https://mineru.net/api/v4/",
            mineru_api_token="test-token",
            mineru_model_version="pipeline",
            mineru_poll_timeout_seconds=600,
            mineru_poll_interval_seconds=3,
        ))
        self.now = 0
        self.logs = []
        self.node.log_step = lambda step_name, message: self.logs.append(message)

    def sleep(self, seconds):
        self.now += seconds

    def run_poll(self, results):
        with patch(MODULE + ".requests.post", return_value=response({
            "code": 0, "data": {"batch_id": "test-batch", "file_urls": ["https://upload.example/pdf"]}
        })) as post, patch(MODULE + ".requests.put", return_value=Mock(status_code=200)), \
                patch(MODULE + ".requests.get", side_effect=results) as get, \
                patch(MODULE + ".time.monotonic", side_effect=lambda: self.now), \
                patch(MODULE + ".time.sleep", side_effect=self.sleep):
            result = self.node._step_2_upload_and_poll(self.pdf)
            return result, post, get

    def test_pending_running_done_and_configured_model(self):
        result, post, get = self.run_poll([
            poll_response("pending"),
            poll_response("running", extract_progress={"extracted_pages": 2, "total_pages": 5}),
            poll_response("done", full_zip_url="https://result.example/archive.zip"),
        ])
        self.assertEqual(result, "https://result.example/archive.zip")
        self.assertEqual(post.call_args.kwargs["json"]["model_version"], "pipeline")
        self.assertEqual(post.call_args.args[0], "https://mineru.net/api/v4/file-urls/batch")
        self.assertIn("尚未开始解析", self.logs[1])
        self.assertIn("2/5", self.logs[2])
        self.assertEqual(get.call_count, 3)

    def test_pending_timeout_keeps_batch_and_state(self):
        self.node.config.mineru_poll_timeout_seconds = 5
        with self.assertRaises(TimeoutError) as caught:
            self.run_poll([poll_response("pending"), poll_response("pending")])
        self.assertEqual(self.now, 5)
        self.assertIn("pending", str(caught.exception))
        self.assertIn("test-batch", str(caught.exception))
        self.assertIn("不会取消服务端任务", str(caught.exception))

    def test_failed_includes_service_reason_and_batch(self):
        with self.assertRaises(PdfConversionError) as caught:
            self.run_poll([poll_response("failed", err_msg="文件解析失败")])
        self.assertIn("文件解析失败", str(caught.exception))
        self.assertIn("test-batch", str(caught.exception))

    def test_api_error_includes_message(self):
        with self.assertRaises(PdfConversionError) as caught:
            self.run_poll([response({"code": -1, "msg": "查询失败"})])
        self.assertIn("查询失败", str(caught.exception))

    def test_continuous_pending_warns_once(self):
        self.node.config.mineru_poll_interval_seconds = 150
        self.node.config.mineru_poll_timeout_seconds = 750
        with patch.object(self.node.logger, "warning") as warning:
            self.run_poll([
                poll_response("pending"), poll_response("pending"),
                poll_response("pending"), poll_response("pending"),
                poll_response("done", full_zip_url="https://result.example/archive.zip"),
            ])
        warning.assert_called_once()
        self.assertIn("排队超过5分钟", warning.call_args.args[0])

    def test_network_failures_still_reach_poll_timeout(self):
        self.node.config.mineru_poll_timeout_seconds = 5
        with patch.object(self.node.logger, "warning"), self.assertRaises(TimeoutError):
            self.run_poll([Timeout("request timeout"), Timeout("request timeout")])
        self.assertEqual(self.now, 5)

    def test_invalid_config_fails_before_submission(self):
        for field, value in (("mineru_model_version", "invalid"),
                             ("mineru_poll_timeout_seconds", 0),
                             ("mineru_poll_interval_seconds", -1)):
            with self.subTest(field=field), patch(MODULE + ".requests.post") as post:
                original = getattr(self.node.config, field)
                setattr(self.node.config, field, value)
                try:
                    with self.assertRaises(ConfigurationError):
                        self.node._step_2_upload_and_poll(self.pdf)
                    post.assert_not_called()
                finally:
                    setattr(self.node.config, field, original)


if __name__ == "__main__":
    unittest.main()
