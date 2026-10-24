"""持久化文档导入轨迹。记录失败不改变原有导入结果。"""

import json
import logging
import math
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from fastapi import HTTPException
from sqlalchemy import BigInteger, Column, DateTime, Integer, MetaData, String, Table, Text, func, insert, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.dialects.mysql import DATETIME, MEDIUMTEXT

logger = logging.getLogger(__name__)
metadata = MetaData()
traces = Table(
    "kb_import_traces", metadata,
    Column("task_id", String(36), primary_key=True),
    Column("trace_id", String(32), nullable=False, unique=True),
    Column("user_id", BigInteger, nullable=False, index=True),
    Column("scope", String(64), nullable=False),
    Column("filename", Text, nullable=False),
    Column("route", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("started_at", DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=False),
    Column("finished_at", DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=True),
    Column("duration_ms", Integer, nullable=True),
    Column("nodes", Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False),
    Column("error", Text, nullable=False),
    mysql_charset="utf8mb4", mysql_engine="InnoDB",
)

NODE_DEFINITIONS = (
    ("upload_file", "保存文件与导入登记", "本地代码 / MySQL"),
    ("backup_file", "备份原始文件", "MinIO"),
    ("node_entry", "检查文件与选择路线", "本地代码"),
    ("node_pdf_to_md", "PDF 转 Markdown", "MinerU"),
    ("node_md_img", "Markdown 图片处理", "图片识别 / MinIO"),
    ("node_document_split", "文档切分", "本地代码"),
    ("node_item_name_recognition", "主体名称识别", "语言模型 / BGE-M3 / Milvus"),
    ("node_bge_embedding", "生成切片向量", "BGE-M3"),
    ("node_import_milvus", "写入知识向量", "Milvus"),
)


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def safe_text(value: Any) -> str:
    text = str(value)[:4000]
    text = re.sub(r"(?i)(bearer\s+)[\w.\-]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|access[_-]?key|secret|password|token|authorization|cookie)[\"']?\s*[=:]\s*[\"']?)[^\s,;\"'}]+", r"\1[REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", text)
    text = re.sub(r"(https?://)[^/\s@]+@", r"\1[REDACTED]@", text)
    # 移除 URL 查询参数，避免把签名下载地址记录到页面。
    text = re.sub(r"(https?://[^\s?\"<>]+)\?[^\s\"<>]*", r"\1?[REDACTED]", text)
    # 错误信息可能携带绝对本地路径；记录只保留文件名。
    text = re.sub(r"[A-Za-z]:[\\/][^\n\"<>|]*", lambda match: Path(match.group().replace("\\", "/")).name, text)
    return text[:2000]


def snapshot(value: Any, key: str = "", depth: int = 0) -> Any:
    """生成有界快照；不复制完整向量、配置对象或无限长的文档。"""
    if re.search(r"password|token|secret|authorization|cookie|api[_-]?key|access[_-]?key", key, re.I):
        return "[REDACTED]"
    if key in {"dense_vector", "sparse_vector"}:
        try:
            return {"kind": key, "size": len(value), "summary": True}
        except TypeError:
            return {"kind": key, "summary": True}
    if key in {"file_dir", "import_file_path", "pdf_path", "md_path", "storage_path"} and value:
        return Path(str(value).replace("\\", "/")).name
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        preview = safe_text(value)
        return preview if len(value) <= 2000 else {"preview": preview, "characters": len(value), "truncated": True}
    if depth >= 6:
        return {"type": type(value).__name__, "truncated": True}
    if isinstance(value, dict):
        result = {str(name): snapshot(item, str(name), depth + 1) for name, item in list(value.items())[:30]}
        if len(value) > 30:
            result["_truncated_fields"] = len(value) - 30
        return result
    if isinstance(value, (list, tuple)):
        items = [snapshot(item, depth=depth + 1) for item in value[:3]]
        return items if len(value) <= 3 else {"count": len(value), "preview": items, "truncated": True}
    return {"type": type(value).__name__, "shape": str(getattr(value, "shape", ""))[:80], "summary": True}


def bounded_snapshot(value: Any) -> Any:
    result = snapshot(value)
    encoded = json.dumps(result, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > 24000:
        return {"preview": encoded[:5000], "truncated": True}
    return result


class ImportTraceStore:
    def __init__(self, engine: Engine, scope: str = "shared_kb") -> None:
        self.engine = engine
        self.scope = scope

    def create_schema(self) -> None:
        metadata.create_all(self.engine)

    def start(self, task_id: str, user_id: int, filename: str, started_at: datetime) -> None:
        route = "pdf" if filename.endswith(".pdf") else "markdown"
        nodes = []
        for index, (key, label, provider) in enumerate(NODE_DEFINITIONS):
            skipped = key == "node_pdf_to_md" and route == "markdown"
            nodes.append({
                "key": key, "sequence": index + 1, "label": label, "provider": provider,
                "status": "skipped" if skipped else "pending", "started_at": None,
                "finished_at": None, "duration_ms": None, "upstream": None,
                "input": None, "output": None, "error": "", "warnings": [],
                "reason": "Markdown 文件直接进入图片处理，无需 PDF 转换" if skipped else "",
            })
        with self.engine.begin() as connection:
            connection.execute(insert(traces).values(
                task_id=task_id, trace_id=uuid.uuid4().hex, user_id=user_id,
                scope=self.scope, filename=filename, route=route, status="processing",
                started_at=started_at, nodes=json.dumps(nodes, ensure_ascii=False), error="",
            ))

    def change(self, task_id: str, mutate: Callable[[list[dict[str, Any]]], None], **values: Any) -> None:
        with self.engine.begin() as connection:
            row = connection.execute(select(traces.c.nodes).where(
                traces.c.task_id == task_id, traces.c.scope == self.scope,
            ).with_for_update()).first()
            if row is None:
                return
            nodes = json.loads(row.nodes)
            mutate(nodes)
            connection.execute(update(traces).where(traces.c.task_id == task_id).values(
                nodes=json.dumps(nodes, ensure_ascii=False), **values,
            ))

    def get(self, user_id: int, task_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = connection.execute(select(traces).where(
                traces.c.user_id == user_id, traces.c.task_id == task_id, traces.c.scope == self.scope,
            )).mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail="未找到此导入记录")
        result = self.serialize(dict(row))
        result["snapshot_policy"] = "长文本保留预览，切片最多展示 3 条，向量仅记录数量及维度；敏感配置和签名参数已隐藏。"
        return result

    @staticmethod
    def serialize(row: dict[str, Any]) -> dict[str, Any]:
        for field in ("started_at", "finished_at"):
            value = row.get(field)
            if isinstance(value, datetime):
                row[field] = value.replace(tzinfo=timezone.utc).isoformat()
        if "nodes" in row:
            row["nodes"] = json.loads(row["nodes"])
            row["warning_count"] = sum(len(node["warnings"]) for node in row["nodes"])
        return row

    def list(self, user_id: int, keyword: str, status: str | None, page: int, page_size: int) -> dict[str, Any]:
        conditions = [traces.c.user_id == user_id, traces.c.scope == self.scope]
        if status:
            conditions.append(traces.c.status == status)
        if keyword:
            conditions.append(or_(*(field.contains(keyword, autoescape=True) for field in (
                traces.c.filename, traces.c.task_id, traces.c.trace_id,
            ))))
        fields = [column for column in traces.c if column.name != "nodes"]
        with self.engine.connect() as connection:
            total = connection.execute(select(func.count()).select_from(traces).where(*conditions)).scalar_one()
            rows = connection.execute(select(*fields).where(*conditions).order_by(
                traces.c.started_at.desc(), traces.c.task_id.desc(),
            ).offset((page - 1) * page_size).limit(page_size)).mappings().all()
        return {"items": [self.serialize(dict(row)) for row in rows], "total": total, "page": page, "page_size": page_size}


class ImportTraceRecorder:
    def __init__(self, store: ImportTraceStore, task_id: str) -> None:
        self.store = store
        self.task_id = task_id
        self.timings: dict[str, float] = {}

    def change(self, mutate: Callable[[list[dict[str, Any]]], None], **values: Any) -> None:
        try:
            self.store.change(self.task_id, mutate, **values)
        except Exception:
            logger.exception("[%s] 导入轨迹写入失败，原导入继续执行", self.task_id)

    def begin(self, key: str, data: Any, started_at: datetime | None = None) -> None:
        self.timings[key] = time.perf_counter()
        captured = bounded_snapshot(data)

        def mutate(nodes: list[dict[str, Any]]) -> None:
            node = next(item for item in nodes if item["key"] == key)
            prior = [item for item in nodes[:node["sequence"] - 1] if item["status"] in {"success", "warning"}]
            node.update(status="running", started_at=(started_at or now()).replace(tzinfo=timezone.utc).isoformat(), input=captured,
                        upstream=prior[-1]["key"] if prior else None)
        self.change(mutate)

    def end(self, key: str, data: Any = None, error: str = "", warnings: list[str] | None = None,
            duration_ms: int | None = None) -> None:
        elapsed = duration_ms if duration_ms is not None else max(0, int((time.perf_counter() - self.timings.pop(key, time.perf_counter())) * 1000))
        captured = bounded_snapshot(data)

        def mutate(nodes: list[dict[str, Any]]) -> None:
            node = next(item for item in nodes if item["key"] == key)
            node.update(status="failed" if error else "warning" if warnings else "success",
                        finished_at=now().replace(tzinfo=timezone.utc).isoformat(), duration_ms=elapsed,
                        output=captured, error=safe_text(error), warnings=[safe_text(message) for message in (warnings or [])[:8]])
        self.change(mutate)

    def finish(self, status: str, error: str = "") -> None:
        finished = now()

        def mutate(nodes: list[dict[str, Any]]) -> None:
            for node in nodes:
                if node["status"] in {"pending", "running"}:
                    node["status"] = "skipped" if status == "failed" else "unrecorded"
                    node["reason"] = "前序步骤失败，未执行" if status == "failed" else "本次执行未产生此节点的记录"
        try:
            with self.store.engine.connect() as connection:
                started = connection.execute(select(traces.c.started_at).where(traces.c.task_id == self.task_id)).scalar_one()
            self.change(mutate, status=status, error=safe_text(error), finished_at=finished,
                        duration_ms=max(0, int((finished - started).total_seconds() * 1000)))
        except Exception:
            logger.exception("[%s] 导入轨迹结束状态写入失败", self.task_id)


current_trace: ContextVar[ImportTraceRecorder | None] = ContextVar("import_trace", default=None)


def start_trace(engine: Engine, task_id: str, user_id: int, filename: str, started_at: datetime,
                scope: str = "shared_kb") -> ImportTraceRecorder | None:
    try:
        store = ImportTraceStore(engine, scope)
        store.create_schema()
        store.start(task_id, user_id, filename, started_at)
        return ImportTraceRecorder(store, task_id)
    except Exception:
        logger.exception("[%s] 无法初始化导入轨迹，原导入继续执行", task_id)
        return None


@contextmanager
def trace_scope(recorder: ImportTraceRecorder | None) -> Iterator[None]:
    token = current_trace.set(recorder)
    try:
        yield
    finally:
        current_trace.reset(token)


@contextmanager
def trace_node(key: str, state: Any, node_logger: logging.Logger) -> Iterator[Callable[[Any], None]]:
    recorder = current_trace.get()
    if recorder is None:
        yield lambda result: None
        return
    recorder.begin(key, state)
    warnings: list[str] = []
    output: list[Any] = [None]

    class Warnings(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if current_trace.get() is recorder and len(warnings) < 8:
                warnings.append(safe_text(record.getMessage()))

    handler = Warnings(logging.WARNING)
    node_logger.addHandler(handler)
    try:
        yield lambda result: output.__setitem__(0, result)
    except Exception as exc:
        recorder.end(key, error=str(exc), warnings=warnings)
        raise
    else:
        recorder.end(key, output[0], warnings=warnings)
    finally:
        node_logger.removeHandler(handler)
