from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from auth.store import get_auth_store


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


metadata = MetaData()

documents = Table(
    "kb_import_documents",
    metadata,

    Column("task_id", String(36), primary_key=True),
    Column("scope", String(64), nullable=False),
    Column("filename", Text, nullable=False),

    Column("file_hash", String(64), nullable=False),
    Column("active_hash", String(64), nullable=True),

    Column("status", String(16), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Column("error", Text, nullable=False),

    UniqueConstraint(
        "scope",
        "active_hash",
        name="uq_kb_active_import_hash",
    ),

    mysql_charset="utf8mb4",
    mysql_engine="InnoDB",
)


class ImportRegistry:
    def __init__(
        self,
        engine: Engine,
        scope: str = "shared_kb",
    ) -> None:
        self.engine = engine
        self.scope = scope

    def create_schema(self) -> None:
        metadata.create_all(self.engine)

    def claim(
        self,
        task_id: str,
        filename: str,
        file_hash: str,
    ) -> tuple[bool, dict[str, Any]]:
        """尝试登记，返回：是否允许导入、对应记录。"""

        now = utcnow()

        values = {
            "task_id": task_id,
            "scope": self.scope,
            "filename": filename,
            "file_hash": file_hash,
            "active_hash": file_hash,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "error": "",
        }

        for attempt in range(2):
            try:
                with self.engine.begin() as connection:
                    connection.execute(
                        insert(documents).values(**values)
                    )

                return True, values

            except IntegrityError:
                # 前面的插入事务已经回滚。
                with self.engine.connect() as connection:
                    row = connection.execute(
                        select(documents).where(
                            documents.c.scope == self.scope,
                            documents.c.active_hash == file_hash,
                        )
                    ).mappings().first()

                if row is not None:
                    return False, dict(row)

                # 冲突后，旧任务可能恰好失败并释放占位。
                # 允许再尝试一次；其他完整性异常仍向外抛出。
                if attempt == 1:
                    raise

        raise RuntimeError("无法登记导入任务")

    def set_status(
        self,
        task_id: str,
        status: str,
        error: str = "",
    ) -> None:
        if status not in {"processing", "completed", "failed"}:
            raise ValueError("不支持的导入状态")

        values: dict[str, Any] = {
            "status": status,
            "updated_at": utcnow(),
            "error": error,
        }

        if status == "failed":
            # 失败后释放占位，允许再次上传。
            values["active_hash"] = None

        with self.engine.begin() as connection:
            connection.execute(
                update(documents)
                .where(
                    documents.c.scope == self.scope,
                    documents.c.task_id == task_id,
                )
                .values(**values)
            )


@lru_cache
def get_import_registry() -> ImportRegistry:
    # 复用已有 MySQL 引擎和连接池。
    registry = ImportRegistry(get_auth_store().engine)
    registry.create_schema()
    return registry
