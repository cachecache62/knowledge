"""与 Vue 前端对应的文件上传和导入进度接口。"""

import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

import uvicorn
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware

from api.frontend import register_frontend
from config.minio_config import minio_config
from processor.import_processor.main_graph import KBImportWorkflow
from tool.logger import logger
from utils.minio_utils import get_minio_client
from utils.task_utils import (
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_PENDING,
    TASK_STATUS_PROCESSING,
    add_done_task,
    add_running_task,
    get_done_task_list,
    get_running_task_list,
    get_task_result,
    get_task_status,
    set_task_result,
    update_task_status,
)

app = FastAPI(title="掌柜智库-导入API", description="Vue 前端的文件导入及进度接口")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_frontend(app, "import")


def run_graph_task(task_id: str, file_dir: str, import_file_path: str) -> None:
    try:
        update_task_status(task_id, TASK_STATUS_PROCESSING)
        initial_state = {
            "task_id": task_id,
            "file_dir": file_dir,
            "import_file_path": import_file_path,
        }
        workflow = KBImportWorkflow()
        for _event in workflow.run(initial_state, stream=True):
            pass
        update_task_status(task_id, TASK_STATUS_COMPLETED)
    except Exception as exc:
        set_task_result(task_id, "error", str(exc))
        update_task_status(task_id, TASK_STATUS_FAILED)
        logger.exception("[%s] LangGraph 导入流程执行失败", task_id)


def save_upload(source: BinaryIO, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        shutil.copyfileobj(source, output)


@app.post(
    "/upload", summary="文件上传接口", description="接收 files 字段并返回 task_ids"
)
async def upload_files(
    background_tasks: BackgroundTasks, files: list[UploadFile] = File(...)
) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="请选择要上传的文件")
    # 先校验整个批次，避免发现非法后缀时已经安排了部分任务。
    filenames: list[str] = []
    for file in files:
        filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not filename or Path(filename).suffix not in {".pdf", ".md"}:
            raise HTTPException(
                status_code=400, detail="仅支持扩展名为小写 .pdf 或 .md 的文件"
            )
        filenames.append(filename)

    root_dir = os.getenv("DATA_BASED_ROOT_DIR")
    if not root_dir:
        raise HTTPException(
            status_code=500, detail="后端未配置 DATA_BASED_ROOT_DIR 上传目录"
        )
    day = datetime.now().strftime("%Y%m%d")
    data_dir = Path(root_dir) / day
    task_ids: list[str] = []

    for file, filename in zip(files, filenames):
        task_id = str(uuid.uuid4())
        file_dir = data_dir / task_id
        import_file_path = file_dir / filename
        update_task_status(task_id, TASK_STATUS_PENDING)
        add_running_task(task_id, "upload_file")
        try:
            await run_in_threadpool(save_upload, file.file, import_file_path)
        except Exception as exc:
            set_task_result(task_id, "error", str(exc))
            update_task_status(task_id, TASK_STATUS_FAILED)
            logger.exception("[%s] 上传文件保存失败", task_id)
            raise HTTPException(
                status_code=500, detail=f"上传文件保存失败：{exc}"
            ) from exc
        finally:
            await file.close()

        try:
            minio_client = get_minio_client()
            await run_in_threadpool(
                minio_client.fput_object,
                bucket_name=minio_config.bucket_name,
                object_name=f"pdf_files/{day}/{filename}",
                file_path=str(import_file_path),
                content_type=file.content_type or "application/octet-stream",
            )
        except Exception as exc:
            # 保留原行为：MinIO 备份失败，仍继续本地文档导入。
            logger.warning(
                "[%s] 文件上传 MinIO 失败，将继续本地处理：%s",
                task_id,
                exc,
                exc_info=True,
            )

        add_done_task(task_id, "upload_file")
        background_tasks.add_task(
            run_graph_task, task_id, str(file_dir), str(import_file_path)
        )
        task_ids.append(task_id)
        logger.info("[%s] 已安排文档导入：%s", task_id, filename)

    return {
        "code": 200,
        "message": f"文件上传成功, total: {len(task_ids)}",
        "task_ids": task_ids,
    }


@app.get("/status/{task_id}", summary="任务状态查询")
async def get_task_progress(task_id: str) -> dict[str, Any]:
    status = get_task_status(task_id)
    if not status:
        raise HTTPException(
            status_code=404, detail="未找到导入任务，服务重启后内存任务状态会清空"
        )
    return {
        "code": 200,
        "task_id": task_id,
        "status": status,
        "done_list": get_done_task_list(task_id),
        "running_list": get_running_task_list(task_id),
        "error": get_task_result(task_id, "error", ""),
    }


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
