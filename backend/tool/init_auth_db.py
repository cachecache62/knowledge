"""运行 uv run python -m tool.init_auth_db 初始化 MySQL 账户库。"""

from sqlalchemy import create_engine, text

from auth.store import AuthStore
from config.auth_config import get_auth_config


def main() -> None:
    config = get_auth_config()
    server = create_engine(config.database_url(include_database=False), connect_args={"connect_timeout": 5})
    try:
        with server.begin() as connection:
            # 数据库名在配置层经过严格白名单校验，不接受任意 SQL 标识符。
            connection.execute(text(f"CREATE DATABASE IF NOT EXISTS `{config.mysql_database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
    finally:
        server.dispose()
    engine = create_engine(config.database_url(), connect_args={"connect_timeout": 5})
    try:
        AuthStore(engine).create_schema()
    finally:
        engine.dispose()
    print(f"MySQL 账户数据库 {config.mysql_database} 初始化完成。")


if __name__ == "__main__":
    main()
