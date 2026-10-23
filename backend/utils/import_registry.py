from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Callable
from pathlib import Path

from fastapi import HTTPException

from sqlalchemy import (
    Column,
    Boolean,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    insert,
    inspect,
    text,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from auth.store import get_auth_store

from sqlalchemy import func, or_
from auth.store import import_tasks


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


BEIJING_TZ = timezone(timedelta(hours=8))


def beijing_now() -> datetime:
    # 北京时间（UTC+8）用于前端显示和日志记录，避免跨时区混乱。
    return datetime.now(BEIJING_TZ).replace(tzinfo=None)


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
    Column("is_deleted", Boolean, nullable=False, default=False, server_default=text("0")),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Column("error", Text, nullable=False),
    Column("storage_path", String(1024), nullable=True),
    Column("object_key", String(1024), nullable=True),
    Column("vector_key", String(36), nullable=True),
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
        # create_all 不会补齐旧表；删除标记默认 0，保留已有文档为正常状态。
        existing = {column["name"] for column in inspect(self.engine).get_columns(documents.name)}
        definitions = (
            ("storage_path", "VARCHAR(1024) NULL"),
            ("object_key", "VARCHAR(1024) NULL"),
            ("vector_key", "VARCHAR(36) NULL"),
            ("is_deleted", "BOOLEAN NOT NULL DEFAULT 0"),
        )
        for name, definition in definitions:
            if name not in existing:
                try:
                    with self.engine.begin() as connection:
                        connection.execute(text(f"ALTER TABLE kb_import_documents ADD COLUMN {name} {definition}"))
                except SQLAlchemyError:
                    # 首次请求可能并发触发初始化；只忽略另一连接已经添加此字段的情形。
                    if name not in {column["name"] for column in inspect(self.engine).get_columns(documents.name)}:
                        raise

    def remember_storage(self, task_id: str, storage_path: str, object_key: str | None = None) -> None:
        values: dict[str, Any] = {"storage_path": storage_path}
        if object_key is not None:
            values["object_key"] = object_key
        with self.engine.begin() as connection:
            connection.execute(update(documents).where(
                documents.c.task_id == task_id, documents.c.scope == self.scope,
                documents.c.is_deleted.is_(False),
            ).values(**values))

    def get_document(self, user_id: int, task_id: str) -> dict[str, Any]:
        """
        获取文档记录，返回示例：·
        {
        "task_id": "...",
        "filename": "手册.pdf",
        "storage_path": "20261023/任务ID/手册.pdf",
        "status": "completed",
        # 其他文档字段……
    }
        """
        with self.engine.connect() as connection:
            row = connection.execute(
                select(documents).join(import_tasks, documents.c.task_id == import_tasks.c.task_id)
                .where(documents.c.scope == self.scope, documents.c.task_id == task_id,
                       documents.c.is_deleted.is_(False),
                       import_tasks.c.user_id == user_id)
            ).mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail="未找到此文档")
        return dict(row)

    def delete_document(
        self, user_id: int, task_id: str,
        cleanup: Callable[[dict[str, Any], bool], None],
    ) -> None:
        # 在清理期间锁住记录，避免重复删除；外部清理失败时保留登记和去重占位，允许重试。
        with self.engine.begin() as connection:
            row = connection.execute(
                select(documents).join(import_tasks, documents.c.task_id == import_tasks.c.task_id)
                .where(documents.c.scope == self.scope, documents.c.task_id == task_id,
                       documents.c.is_deleted.is_(False),
                       import_tasks.c.user_id == user_id).with_for_update()
            ).mappings().first()
            if row is None:
                raise HTTPException(status_code=404, detail="未找到此文档")
            if row["status"] in {"pending", "processing"}:
                raise HTTPException(status_code=409, detail="文档正在导入，请等待处理结束后再删除")
            others = connection.execute(
                select(documents.c.filename).where(
                    documents.c.task_id != task_id, documents.c.is_deleted.is_(False),
                )
            ).scalars().all()
            stem = Path(row["filename"]).stem
            if not row["vector_key"] and any(Path(name).stem == stem for name in others):
                raise HTTPException(status_code=409, detail="旧文档存在同名记录，无法安全确认向量归属，暂不能删除")
            delete_images = not any(Path(name).stem.replace(" ", "") == stem.replace(" ", "") for name in others)
            cleanup(dict(row), delete_images)
            # 保留文档原始记录和上传者归属，只标记删除并释放文件指纹占位。
            connection.execute(update(documents).where(documents.c.task_id == task_id).values(
                is_deleted=True, active_hash=None, updated_at=beijing_now(),
            ))

    def claim(
        self,
        task_id: str,
        filename: str,
        file_hash: str,
    ) -> tuple[bool, dict[str, Any]]:
        """尝试登记，返回：是否允许导入、对应记录。"""

        now = beijing_now()

        values = {
            "task_id": task_id,
            "scope": self.scope,
            "filename": filename,
            "file_hash": file_hash,
            "active_hash": file_hash,
            "status": "pending",
            "is_deleted": False,
            "created_at": now,
            "updated_at": now,
            "error": "",
            "vector_key": task_id,
        }

        for attempt in range(2):
            try:
                with self.engine.begin() as connection:
                    connection.execute(insert(documents).values(**values))

                return True, values

            except IntegrityError:
                # 前面的插入事务已经回滚。
                with self.engine.connect() as connection:
                    row = (
                        connection.execute(
                            select(documents).where(
                                documents.c.scope == self.scope,
                                documents.c.active_hash == file_hash,
                                documents.c.is_deleted.is_(False),
                            )
                        )
                        .mappings()
                        .first()
                    )

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
                    documents.c.is_deleted.is_(False),
                )
                .values(**values)
            )

    def list_documents(
        self,
        user_id: int,
        keyword: str = "",
        status: str | None = None,
        page: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        # 通过任务归属表，只查询当前用户上传的文档。
        source = documents.join(
            import_tasks,
            documents.c.task_id == import_tasks.c.task_id,
        )

        conditions = [
            documents.c.scope == self.scope,
            documents.c.is_deleted.is_(False),
            import_tasks.c.user_id == user_id,
        ]

        if keyword:
            conditions.append(
                or_(
                    documents.c.filename.contains(keyword, autoescape=True),
                    documents.c.task_id.contains(keyword, autoescape=True),
                )
            )

        if status:
            conditions.append(documents.c.status == status)

        fields = [
            documents.c.task_id,
            documents.c.filename,
            documents.c.scope,
            documents.c.status,
            documents.c.created_at,
            documents.c.updated_at,
        ]

        statement = (
            select(*fields)
            .select_from(source)
            .where(*conditions)
            .order_by(
                documents.c.updated_at.desc(),
                documents.c.task_id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )

        with self.engine.connect() as connection:
            total = connection.execute(
                select(func.count()).select_from(source).where(*conditions)
            ).scalar_one()

            rows = connection.execute(statement).mappings().all()

        items = []
        for row in rows:
            item = dict(row)

            # 当前 import_registry 写入的是 UTC。
            # 明确携带时区，前端才能正确转换为北京时间。
            for field in ("created_at", "updated_at"):
                item[field] = item[field].replace(tzinfo=timezone.utc).astimezone(BEIJING_TZ).isoformat()

            items.append(item)

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }


@lru_cache
def get_import_registry() -> ImportRegistry:
    # 复用已有 MySQL 引擎和连接池。
    registry = ImportRegistry(get_auth_store().engine)
    registry.create_schema()
    return registry
