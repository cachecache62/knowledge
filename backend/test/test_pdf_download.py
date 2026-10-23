"""MinerU 结果下载发生瞬时 DNS/网络故障时，只重试下载。"""

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from processor.import_processor.exceptions import FileProcessingError
from processor.import_processor.nodes.node_pdf_to_md import NodePDFToMD


MODULE = "processor.import_processor.nodes.node_pdf_to_md"
ZIP_URL = "https://cdn-mineru.openxlab.org.cn/example.zip"


class PDFDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.node = NodePDFToMD()

    def test_temporary_dns_failure_retries_download_and_extracts(self) -> None:
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("full.md", "# Converted manual")
        response = Mock(status_code=200, content=archive.getvalue())
        with tempfile.TemporaryDirectory() as directory, \
                patch(MODULE + ".requests.get", side_effect=[requests.ConnectionError("DNS failed"), response]) as get, \
                patch(MODULE + ".time.sleep") as sleep:
            md_path = self.node._step_3_download_and_extract(ZIP_URL, Path(directory), "manual")
            self.assertEqual(Path(md_path).read_text(encoding="utf-8"), "# Converted manual")
        self.assertEqual(get.call_count, 2)
        get.assert_called_with(ZIP_URL, timeout=(10, 120))
        sleep.assert_called_once_with(2)
        response.close.assert_called_once()

    def test_persistent_connection_failure_is_bounded_and_explains_dns(self) -> None:
        with patch(MODULE + ".requests.get", side_effect=requests.ConnectionError("DNS failed")) as get, \
                patch(MODULE + ".time.sleep") as sleep:
            with self.assertRaises(FileProcessingError) as caught:
                self.node._download_result(ZIP_URL)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertIn("域名解析失败", str(caught.exception))
        self.assertIn("cdn-mineru.openxlab.org.cn", str(caught.exception))

    def test_timeout_is_reported_after_three_attempts(self) -> None:
        with patch(MODULE + ".requests.get", side_effect=requests.Timeout("read timeout")), \
                patch(MODULE + ".time.sleep"):
            with self.assertRaisesRegex(FileProcessingError, "请求超时"):
                self.node._download_result(ZIP_URL)

    def test_transient_http_errors_are_retried(self) -> None:
        busy = Mock(status_code=503)
        success = Mock(status_code=200, content=b"zip")
        with patch(MODULE + ".requests.get", side_effect=[busy, success]) as get, \
                patch(MODULE + ".time.sleep"):
            self.assertEqual(self.node._download_result(ZIP_URL), b"zip")
        self.assertEqual(get.call_count, 2)
        busy.close.assert_called_once()
        success.close.assert_called_once()

    def test_non_retryable_http_error_keeps_old_directory(self) -> None:
        response = Mock(status_code=403)
        with tempfile.TemporaryDirectory() as directory:
            old = Path(directory) / "manual"
            old.mkdir()
            previous = old / "manual.md"
            previous.write_text("old", encoding="utf-8")
            with patch(MODULE + ".requests.get", return_value=response) as get, \
                    patch(MODULE + ".time.sleep") as sleep:
                with self.assertRaisesRegex(FileProcessingError, "HTTP 403"):
                    self.node._step_3_download_and_extract(ZIP_URL, Path(directory), "manual")
            self.assertEqual(previous.read_text(encoding="utf-8"), "old")
        self.assertEqual(get.call_count, 1)
        sleep.assert_not_called()
        response.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
