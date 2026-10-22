"""MySQL 与浏览器登录会话配置，不在源码中保存密码。"""

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)


@dataclass(frozen=True)
class AuthConfig:
    mysql_host: str
    mysql_port: int
    mysql_user: str
    mysql_password: str
    mysql_database: str
    cookie_name: str
    cookie_secure: bool
    session_hours: int
    allowed_origins: tuple[str, ...]

    def database_url(self, *, include_database: bool = True) -> URL:
        return URL.create(
            "mysql+pymysql", username=self.mysql_user, password=self.mysql_password,
            host=self.mysql_host, port=self.mysql_port,
            database=self.mysql_database if include_database else None,
            query={"charset": "utf8mb4"},
        )


@lru_cache
def get_auth_config() -> AuthConfig:
    database = os.getenv("MYSQL_DATABASE", "knowledge_base")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", database):
        raise ValueError("MYSQL_DATABASE 只能包含字母、数字和下划线")
    hours = int(os.getenv("AUTH_SESSION_HOURS", "6"))
    if not 1 <= hours <= 720:
        raise ValueError("AUTH_SESSION_HOURS 必须在 1 到 720 之间")
    defaults = ",".join(
        f"http://{host}:{port}"
        for host in ("127.0.0.1", "localhost")
        for port in (5173, 8000, 8001, 8002)
    )
    origins = tuple(value.strip().rstrip("/") for value in os.getenv("AUTH_ALLOWED_ORIGINS", defaults).split(",") if value.strip())
    if not origins or "*" in origins:
        raise ValueError("AUTH_ALLOWED_ORIGINS 必须是明确的前端地址，不能使用 *")
    return AuthConfig(
        mysql_host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        mysql_port=int(os.getenv("MYSQL_PORT", "3306")),
        mysql_user=os.getenv("MYSQL_USER", "root"),
        mysql_password=os.getenv("MYSQL_PASSWORD", ""),
        mysql_database=database,
        cookie_name="kb_auth_session",
        cookie_secure=os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true",
        session_hours=hours, allowed_origins=origins,
    )
