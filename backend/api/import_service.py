"""与 Vue 前端对应的文件上传和导入进度接口。"""

import hashlib
import os
# import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

import uvicorn
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from api.frontend import register_frontend
from auth.dependencies import CurrentUser, Store, configure_auth_support
from config.minio_config import minio_config
from processor.import_processor.main_graph import KBImportWorkflow
from tool.logger import logger
from utils.minio_utils import get_minio_client

from utils.import_registry import ImportRegistry, get_import_registry

from utils.task_utils import (
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_PENDING,
    TASK_STATUS_PROCESSING,
    add_done_task,
    # add_running_task,
    get_done_task_list,
    get_running_task_list,
    get_task_result,
    get_task_status,
    set_task_result,
    update_task_status,
)


from typing import Literal
from fastapi import Query

app = FastAPI(title="掌柜智库-导入API", description="Vue 前端的文件导入及进度接口")
configure_auth_support(app)
register_frontend(app, "import")


def run_graph_task(
    task_id: str,
    file_dir: str,
    import_file_path: str,
    registry: ImportRegistry,
) -> None:
    """
    在后台线程中运行 LangGraph 导入流程。
    :param task_id: 任务ID
    :param file_dir: 文件所在目录
    :param import_file_path: 导入文件路径
    :param registry: 导入注册表对象
    """
    try:
        registry.set_status(task_id, "processing")
        update_task_status(task_id, TASK_STATUS_PROCESSING)
        initial_state = {
            "task_id": task_id,
            "file_dir": file_dir,
            "import_file_path": import_file_path,
        }
        workflow = KBImportWorkflow()
        for _event in workflow.run(initial_state, stream=True):
            pass

         # 整个流程成功后，才登记为已完成。
        registry.set_status(task_id, "completed")

        update_task_status(task_id, TASK_STATUS_COMPLETED)

    except Exception as exc:
        set_task_result(task_id, "error", str(exc))
        update_task_status(task_id, TASK_STATUS_FAILED)

        try:
            registry.set_status(task_id, "failed", str(exc))
        except Exception:
            logger.exception("[%s] 更新指纹状态失败", task_id)
        logger.exception("[%s] LangGraph 导入流程执行失败", task_id)


# def save_upload(source: BinaryIO, destination: Path) -> None:
#     destination.parent.mkdir(parents=True, exist_ok=True)
#     with destination.open("wb") as output:
#         shutil.copyfileobj(source, output)


def save_upload(source: BinaryIO, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()

    with destination.open("wb") as output:
        while chunk := source.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)

    return digest.hexdigest()


@app.post(
    "/upload", summary="文件上传接口", description="接收 files 字段并返回 task_ids"
)
async def upload_files(
    background_tasks: BackgroundTasks,
    user: CurrentUser,
    store: Store,
    files: list[UploadFile] = File(...),
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

    registry = await run_in_threadpool(get_import_registry)
    task_ids: list[str] = []

    results = []

    for file, filename in zip(files, filenames):
        task_id = str(uuid.uuid4())
        # await run_in_threadpool(store.remember_import, user.id, task_id)
        file_dir = data_dir / task_id
        import_file_path = file_dir / filename

        accepted = False
        content_type = file.content_type or "application/octet-stream"

        try:
            # 1. 保存并计算 SHA。
            try:
                file_hash = await run_in_threadpool(
                    save_upload,
                    file.file,
                    import_file_path,
                )
            finally:
                await file.close()

            # 2. 尝试登记。
            accepted, record = await run_in_threadpool(
                registry.claim,
                task_id,
                filename,
                file_hash,
            )

            # 3. 已有相同文件，直接返回提示。
            if not accepted:
                import_file_path.unlink(missing_ok=True)
                file_dir.rmdir()

                busy = record["status"] in {"pending", "processing"}

                results.append(
                    {
                        "filename": filename,
                        "duplicate": True,
                        "status": "duplicate",
                        "message": (
                            "相同文档正在导入，请勿重复上传"
                            if busy
                            else "已有相同文档，已跳过导入"
                        ),
                        "existing_filename": record["filename"],
                    }
                )

                continue

            # 4. 确认需要导入后，再记录任务归属。
            await run_in_threadpool(
                store.remember_import,
                user.id,
                task_id,
            )

            # 5. 保留现有 MinIO 备份逻辑。
            # 备份失败仍继续本地导入。
            try:
                minio_client = get_minio_client()

                await run_in_threadpool(
                    minio_client.fput_object,
                    bucket_name=minio_config.bucket_name,
                    object_name=f"pdf_files/{day}/{task_id}/{filename}",
                    file_path=str(import_file_path),
                    content_type=content_type,
                )

            except Exception:
                logger.exception(
                    "[%s] MinIO 备份失败，继续本地导入",
                    task_id,
                )

        except Exception as exc:
            # 已经登记，但保存之后的准备工作失败，需要释放占位。
            if accepted:
                try:
                    await run_in_threadpool(
                        registry.set_status,
                        task_id,
                        "failed",
                        str(exc),
                    )
                except Exception:
                    logger.exception("[%s] 更新失败状态失败", task_id)

            logger.exception("[%s] 上传准备失败", task_id)

            results.append(
                {
                    "filename": filename,
                    "duplicate": False,
                    "status": "failed",
                    "message": "文件保存或导入登记失败，请稍后重试",
                }
            )

            # 按文件返回失败，避免一个文件失败取消本批已安排的任务。
            continue

        # add_running_task(task_id, "upload_file")
        # try:
        #     await run_in_threadpool(save_upload, file.file, import_file_path)
        # except Exception as exc:
        #     set_task_result(task_id, "error", str(exc))
        #     update_task_status(task_id, TASK_STATUS_FAILED)
        #     logger.exception("[%s] 上传文件保存失败", task_id)
        #     raise HTTPException(
        #         status_code=500, detail=f"上传文件保存失败：{exc}"
        #     ) from exc
        # finally:
        #     await file.close()

        # try:
        #     minio_client = get_minio_client()
        #     await run_in_threadpool(
        #         minio_client.fput_object,
        #         bucket_name=minio_config.bucket_name,
        #         object_name=f"pdf_files/{day}/{filename}",
        #         file_path=str(import_file_path),
        #         content_type=file.content_type or "application/octet-stream",
        #     )
        # except Exception as exc:
        #     # 保留原行为：MinIO 备份失败，仍继续本地文档导入。
        #     logger.warning(
        #         "[%s] 文件上传 MinIO 失败，将继续本地处理：%s",
        #         task_id,
        #         exc,
        #         exc_info=True,
        #     )

        #      # 6. 非重复文件，才创建实际导入任务。
        #     update_task_status(task_id, TASK_STATUS_PENDING)
        #     add_done_task(task_id, "upload_file")
        #     background_tasks.add_task(
        #         run_graph_task, task_id, str(file_dir), str(import_file_path)
        #     )
        #     task_ids.append(task_id)
        #     logger.info("[%s] 已安排文档导入：%s", task_id, filename)

        # return {
        #     "code": 200,
        #     "message": f"文件上传成功, total: {len(task_ids)}",
        #     "task_ids": task_ids,
        # }
        # 6. 非重复文件，才创建实际导入任务。
        update_task_status(task_id, TASK_STATUS_PENDING)
        add_done_task(task_id, "upload_file")

        background_tasks.add_task(
            run_graph_task,
            task_id,
            str(file_dir),
            str(import_file_path),
            registry,
        )

        task_ids.append(task_id)

        results.append(
            {
                "filename": filename,
                "duplicate": False,
                "status": "pending",
                "task_id": task_id,
            }
        )

    return {
        "code": 200,
        "task_ids": task_ids,
        "results": results,
    }


@app.get("/status/{task_id}", summary="任务状态查询")
async def get_task_progress(
    task_id: str, user: CurrentUser, store: Store
) -> dict[str, Any]:
    if not await run_in_threadpool(store.owns_import, user.id, task_id):
        raise HTTPException(status_code=404, detail="未找到此导入任务")
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


@app.get("/documents", summary="查询当前用户上传的文档")
async def list_documents(
    user: CurrentUser,
    keyword: str = Query("", max_length=200),
    status: Literal[
        "pending", "processing", "completed", "failed"
    ] | None = None,
    page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(10, ge=1, le=100),
) -> dict[str, Any]:
    registry = await run_in_threadpool(get_import_registry)

    return await run_in_threadpool(
        registry.list_documents,
        user.id,
        keyword.strip(),
        status,
        page,
        page_size,
    )

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
