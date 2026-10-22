"""MySQL 账户存储；对话正文仍保存在原来的 MongoDB 中。"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from fastapi import HTTPException
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    delete,
    insert,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from config.auth_config import get_auth_config


def utcnow() -> datetime:
    # MySQL DATETIME 统一保存 UTC，不依赖数据库服务器的本地时区。
    return datetime.now(timezone.utc).replace(tzinfo=None)


metadata = MetaData()
users = Table(
    "kb_users",
    metadata,
    Column(
        "id",
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    Column("email", String(254), nullable=False, unique=True),
    Column("password_hash", String(512), nullable=False),
    Column("password_plaintext", String(128), nullable=True),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime, nullable=False),
    Column(
        "updated_at", DateTime, nullable=False,
        default=utcnow, onupdate=utcnow, server_default=text("CURRENT_TIMESTAMP"),
    ),
    mysql_charset="utf8mb4",
    mysql_engine="InnoDB",
)
login_sessions = Table(
    "kb_login_sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", ForeignKey("kb_users.id", ondelete="CASCADE"), nullable=False),
    Column("expires_at", DateTime, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Index("ix_kb_login_sessions_expiry", "expires_at"),
    mysql_charset="utf8mb4",
    mysql_engine="InnoDB",
)
chat_sessions = Table(
    "kb_chat_sessions",
    metadata,
    Column("internal_id", String(36), primary_key=True),
    Column("user_id", ForeignKey("kb_users.id", ondelete="CASCADE"), nullable=False),
    Column("client_id", String(128), nullable=False),
    Column("created_at", DateTime, nullable=False),
    UniqueConstraint("user_id", "client_id", name="uq_kb_user_chat"),
    mysql_charset="utf8mb4",
    mysql_engine="InnoDB",
)
import_tasks = Table(
    "kb_import_tasks",
    metadata,
    Column("task_id", String(36), primary_key=True),
    Column("user_id", ForeignKey("kb_users.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime, nullable=False),
    mysql_charset="utf8mb4",
    mysql_engine="InnoDB",
)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class User:
    id: int
    email: str
    created_at: datetime

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "email": self.email,
            "created_at": self.created_at.isoformat() + "Z",
        }


class AuthStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_schema(self) -> None:
        metadata.create_all(self.engine)
        self._ensure_user_updated_at()

    def _ensure_user_updated_at(self) -> None:
        # create_all 不会修改已有表；补齐旧用户表，并支持中断后重新执行。
        column = next(
            (item for item in inspect(self.engine).get_columns("kb_users") if item["name"] == "updated_at"),
            None,
        )
        if column is not None and not column["nullable"]:
            return
        with self.engine.begin() as connection:
            if column is None:
                definition = "DATETIME NULL DEFAULT CURRENT_TIMESTAMP" if self.engine.dialect.name == "mysql" else "DATETIME NULL"
                connection.execute(text(f"ALTER TABLE kb_users ADD COLUMN updated_at {definition}"))
                # 新增字段时，已有行可能被数据库填入当前时间；回填其创建时间。
                connection.execute(text("UPDATE kb_users SET updated_at = created_at"))
            else:
                connection.execute(text("UPDATE kb_users SET updated_at = created_at WHERE updated_at IS NULL"))
            if self.engine.dialect.name == "mysql":
                connection.execute(text("ALTER TABLE kb_users MODIFY COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"))

    def create_user(
            self, 
            email: str, 
            password_hash: str, 
            password_plaintext: str | None = None
        ) -> User:
        created_at = utcnow()
        with self.engine.begin() as connection:
            result = connection.execute(
                insert(users).values(
                    email=email,
                    password_hash=password_hash,
                    password_plaintext=password_plaintext,
                    is_active=True,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            return User(int(result.inserted_primary_key[0]), email, created_at)

    def find_user(self, email: str) -> dict[str, object] | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(users).where(users.c.email == email))
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def create_login(
        self, user_id: int, token: str, expires_at: datetime, previous_token: str | None
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                delete(login_sessions).where(login_sessions.c.expires_at <= utcnow())
            )
            if previous_token:
                connection.execute(
                    delete(login_sessions).where(
                        login_sessions.c.token_hash == token_digest(previous_token)
                    )
                )
            connection.execute(
                insert(login_sessions).values(
                    user_id=user_id,
                    token_hash=token_digest(token),
                    expires_at=expires_at,
                    created_at=utcnow(),
                )
            )

    def authenticated_user(self, token: str) -> User | None:
        now = utcnow()
        session_start = now - timedelta(hours=get_auth_config().session_hours)
        statement = (
            select(users.c.id, users.c.email, users.c.created_at)
            .join(
                login_sessions,
                users.c.id == login_sessions.c.user_id,
            )
            .where(
                login_sessions.c.token_hash == token_digest(token),
                login_sessions.c.expires_at > now,
                login_sessions.c.created_at > session_start,
                users.c.is_active.is_(True),
            )
        )
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
            return User(**row) if row else None

    def revoke_login(self, token: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                delete(login_sessions).where(
                    login_sessions.c.token_hash == token_digest(token)
                )
            )

    def chat_id(
        self, user_id: int, client_id: str, *, create: bool = False
    ) -> str | None:
        statement = select(chat_sessions.c.internal_id).where(
            chat_sessions.c.user_id == user_id,
            chat_sessions.c.client_id == client_id,
        )
        with self.engine.connect() as connection:
            existing = connection.execute(statement).scalar_one_or_none()
        if existing or not create:
            return existing
        internal_id = str(uuid.uuid4())
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    insert(chat_sessions).values(
                        internal_id=internal_id,
                        user_id=user_id,
                        client_id=client_id,
                        created_at=utcnow(),
                    )
                )
        except IntegrityError:
            # 同一用户并发发起首条请求时，由唯一索引决定唯一映射。
            with self.engine.connect() as connection:
                existing = connection.execute(statement).scalar_one_or_none()
            if existing:
                return existing
            raise
        return internal_id

    def remember_import(self, user_id: int, task_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                insert(import_tasks).values(
                    user_id=user_id, task_id=task_id, created_at=utcnow()
                )
            )

    def owns_import(self, user_id: int, task_id: str) -> bool:
        with self.engine.connect() as connection:
            return (
                connection.execute(
                    select(import_tasks.c.task_id).where(
                        import_tasks.c.task_id == task_id,
                        import_tasks.c.user_id == user_id,
                    )
                ).first()
                is not None
            )


@lru_cache
def get_auth_store() -> AuthStore:
    try:
        config = get_auth_config()
        engine = create_engine(
            config.database_url(),
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args={
                "connect_timeout": 5,
                "read_timeout": 10,
                "write_timeout": 10,
            },
        )
        store = AuthStore(engine)
        store.create_schema()
        return store
    except (SQLAlchemyError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="账户数据库不可用，请检查 MySQL 配置并执行数据库初始化。",
        ) from exc
