"""管理接口的原文件定位、备份读取与幂等清理。"""

import os
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
from urllib.parse import quote
from uuid import UUID

from fastapi import HTTPException
from minio.error import S3Error
from starlette.background import BackgroundTask
from starlette.responses import FileResponse, StreamingResponse

from tool.logger import logger


def upload_root() -> Path:
    configured = os.getenv("DATA_BASED_ROOT_DIR")
    if not configured:
        raise HTTPException(status_code=503, detail="文档存储目录未配置")
    return Path(configured).resolve()


def local_file(record: dict[str, Any]) -> Path | None:
    root = upload_root()
    task_id = str(UUID(record["task_id"]))
    filename = record["filename"]
    if Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=409, detail="文档文件名无效")
    relative = record.get("storage_path")
    candidates = (
        [root / relative]
        if relative
        else [directory / filename for directory in root.glob(f"*/{task_id}")]
    )
    if len(candidates) > 1:
        raise HTTPException(
            status_code=409, detail="发现多个同任务文档目录，请先核对文件位置"
        )
    for candidate in candidates:
        # 所有下载与删除路径必须是 root/日期/任务ID/原文件名。
        candidate = candidate.resolve()
        logger.info(
            "定位文档：task_id=%s，storage_path=%s，候选路径数量=%s，最终路径=%s",
            task_id,
            relative,
            len(candidates),
            candidate
        )
        if not candidate.is_relative_to(root):
            raise HTTPException(status_code=409, detail="文档存储路径无效")
        parts = candidate.relative_to(root).parts
        if (
            len(parts) != 3
            or not re.fullmatch(r"\d{8}", parts[0])
            or parts[1] != task_id
            or parts[2] != filename
        ):
            raise HTTPException(status_code=409, detail="文档存储路径无效")
        if candidate.is_file() or candidate.parent.is_dir() or relative:
            return candidate
    return None


def object_keys(
    record: dict[str, Any], client: Any, bucket: str, path: Path | None
) -> list[str]:
    task_id = str(UUID(record["task_id"]))
    stored_key = record.get("object_key")
    if stored_key:
        parts = PurePosixPath(stored_key).parts
        if (
            len(parts) != 4
            or parts[0] != "pdf_files"
            or not re.fullmatch(r"\d{8}", parts[1])
            or parts[2:] != (task_id, record["filename"])
        ):
            raise HTTPException(status_code=409, detail="文档备份路径无效")
        return [stored_key]
    if path is not None:
        return ["pdf_files/" + path.relative_to(upload_root()).as_posix()]
    # 兼容旧记录：日期可能使用过不同的时区，不能仅按 created_at 猜测路径。
    suffix = f"/{task_id}/{record['filename']}"
    return [
        item.object_name
        for item in client.list_objects(bucket, prefix="pdf_files/", recursive=True)
        if item.object_name.endswith(suffix)
    ]


def download_document(
    record: dict[str, Any], client_factory: Any, bucket: str
) -> FileResponse | StreamingResponse:
    path = local_file(record)
    headers = {"Cache-Control": "private, no-store"}
    if path is not None and path.is_file():
        return FileResponse(
            path,
            filename=record["filename"],
            media_type="application/octet-stream",
            headers=headers,
        )
    client = client_factory()
    for key in object_keys(record, client, bucket, path):
        try:
            response = client.get_object(bucket, key)
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                continue
            raise

        closed = False

        def close() -> None:
            nonlocal closed
            if not closed:
                closed = True
                try:
                    response.close()
                finally:
                    response.release_conn()

        def chunks() -> Iterator[bytes]:
            try:
                yield from response.stream(1024 * 1024)
            finally:
                close()

        headers["Content-Disposition"] = (
            f"attachment; filename*=UTF-8''{quote(record['filename'], safe='')}"
        )
        return StreamingResponse(
            chunks(),
            media_type="application/octet-stream",
            headers=headers,
            background=BackgroundTask(close),
        )
    raise HTTPException(status_code=404, detail="原文档文件不存在，可能已被移动或清理")


def ensure_document_recoverable(record: dict[str, Any]) -> None:
    """恢复前只读检查存储；任何失败都让文档继续留在回收站。"""
    from config.milvus_config import milvus_config
    from config.minio_config import minio_config
    from utils.milvus_utils import escape_milvus_string, get_milvus_client
    from utils.minio_utils import get_minio_client

    path = local_file(record)
    if path is None or not path.is_file():
        client = get_minio_client()
        for key in object_keys(record, client, minio_config.bucket_name, path):
            try:
                client.stat_object(minio_config.bucket_name, key)
                break
            except S3Error as exc:
                if exc.code not in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                    raise
        else:
            raise HTTPException(status_code=409, detail="原文档和备份均不存在，无法恢复，请重新上传")

    if record["status"] == "completed":
        client = get_milvus_client()
        if record.get("vector_key"):
            expression = f"source_task_id == '{escape_milvus_string(record['vector_key'])}'"
        else:
            title = escape_milvus_string(Path(record["filename"]).stem)
            expression = f"file_title == '{title}' and not exists source_task_id"
        if not client.has_collection(milvus_config.chunks_collection) or not client.query(
            collection_name=milvus_config.chunks_collection,
            filter=expression, output_fields=["file_title"], limit=1,
        ):
            raise HTTPException(status_code=409, detail="文档知识数据已缺失，无法恢复，请重新上传")


def cleanup_document(record: dict[str, Any], delete_images: bool) -> None:
    # 延迟导入，独立的单元测试不连接外部存储，也不加载模型。
    from config.milvus_config import milvus_config
    from config.minio_config import minio_config
    from utils.milvus_utils import escape_milvus_string, get_milvus_client
    from utils.minio_utils import get_minio_client

    path = local_file(record)
    client = get_minio_client()
    keys = object_keys(record, client, minio_config.bucket_name, path)
    vector_client = get_milvus_client()
    if record.get("vector_key"):
        expression = f"source_task_id == '{escape_milvus_string(record['vector_key'])}'"
    else:
        stem = escape_milvus_string(Path(record["filename"]).stem)
        expression = f"file_title == '{stem}' and not exists source_task_id"
    for collection in (
        milvus_config.chunks_collection,
        milvus_config.item_name_collection,
    ):
        if not collection:
            raise RuntimeError("文档向量集合未配置")
        if vector_client.has_collection(collection):
            collection_expression = expression
            if (
                not record.get("vector_key")
                and collection == milvus_config.item_name_collection
            ):
                # 旧名称节点曾把转义后的字符串写入列；兼容带引号的历史标题。
                raw_title = Path(record["filename"]).stem
                escaped_value = escape_milvus_string(raw_title)
                if raw_title != escaped_value:
                    legacy_title = escape_milvus_string(escaped_value)
                    collection_expression = f"(file_title == '{stem}' or file_title == '{legacy_title}') and not exists source_task_id"
            vector_client.delete(
                collection_name=collection, filter=collection_expression
            )

    for key in keys:
        client.remove_object(minio_config.bucket_name, key)
    if delete_images and minio_config.img_dir:
        prefix = (
            f"{minio_config.img_dir}/{Path(record['filename']).stem}".replace(
                " ", ""
            ).rstrip("/")
            + "/"
        )
        for item in client.list_objects(
            minio_config.bucket_name, prefix=prefix, recursive=True
        ):
            client.remove_object(minio_config.bucket_name, item.object_name)
    if path is not None and path.parent.exists():
        # 紧邻递归删除再次检查最终绝对路径，绝不删除上传根目录或其他任务目录。
        root = upload_root()
        task_dir = path.parent.resolve()
        if (
            not task_dir.is_relative_to(root)
            or task_dir.name != str(UUID(record["task_id"]))
            or len(task_dir.relative_to(root).parts) != 2
        ):
            raise HTTPException(status_code=409, detail="文档存储路径无效")
        shutil.rmtree(task_dir)
