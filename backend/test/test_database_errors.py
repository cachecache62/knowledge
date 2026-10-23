"""数据库异常保留诊断代码，同时隐藏 SQL 和敏感参数。"""

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError

from auth.dependencies import configure_auth_support


class DatabaseErrorTests(unittest.TestCase):
    def test_handler_logs_error_type_and_mysql_code_without_parameters(self) -> None:
        for error_type, code in ((IntegrityError, 1062), (OperationalError, 2006)):
            with self.subTest(error_type=error_type.__name__):
                service = FastAPI()
                configure_auth_support(service)
                error = error_type(
                    "INSERT INTO hidden_table VALUES (:password)",
                    {"password": "sensitive-value"},
                    Exception(code, "sensitive-driver-message"),
                )

                @service.get("/database-error")
                async def database_error() -> None:
                    raise error

                with TestClient(service) as client, self.assertLogs("auth.dependencies", level="ERROR") as logs:
                    response = client.get("/database-error")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"detail": "账户数据库暂时不可用，请稍后重试。"})
                message = "\n".join(logs.output)
                self.assertIn(f"type={error_type.__name__}", message)
                self.assertIn(f"mysql_code={code}", message)
                self.assertIn("test_database_errors.py", message)
                self.assertIn("/database-error", message)
                for sensitive in ("sensitive-value", "sensitive-driver-message", "hidden_table"):
                    self.assertNotIn(sensitive, message)
                    self.assertNotIn(sensitive, response.text)


if __name__ == "__main__":
    unittest.main()
