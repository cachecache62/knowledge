"""与 Vue 前端对应的问答、历史记录和 SSE 接口。"""

import uuid
from typing import Any

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from api.frontend import register_frontend
from auth.dependencies import CurrentUser, Store, configure_auth_support
from processor.query_processor.main_graph import KBQueryWorkflow
from tool.logger import logger
from utils.mongo_history_utils import clear_history, get_recent_messages
from utils.document_visibility import KnowledgeRetrievalError
from utils.sse_utils import (
    SSEEvent, create_sse_queue, get_sse_queue, push_to_session, sse_generator,
)
from utils.task_utils import (
    TASK_STATUS_COMPLETED, TASK_STATUS_FAILED, TASK_STATUS_PROCESSING,
    clear_task, get_done_task_list, get_task_status, set_task_result, update_task_status,
)

app = FastAPI(title="掌柜智库-查询API", description="Vue 前端的问答及历史记录接口")
configure_auth_support(app)
register_frontend(app, "chat")


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, description="查询内容")
    session_id: str | None = Field(None, min_length=1, max_length=128, description="会话ID")
    is_stream: bool = Field(False, description="是否流式返回")


@app.post("/query")
async def query(background_tasks: BackgroundTasks, request: QueryRequest, user: CurrentUser, store: Store) -> dict[str, Any]:
    user_query = request.query.strip()
    if not user_query:
        raise HTTPException(status_code=422, detail="查询内容不能为空")
    # 前端传了对话 ID 就沿用，没有就生成一个
    client_session_id = request.session_id or str(uuid.uuid4())
    # 浏览器 ID 仅用于定位当前用户的映射；不直接用它查询他人的 MongoDB 历史。
    session_id = await run_in_threadpool(store.chat_id, user.id, client_session_id, create=True)
    if get_task_status(session_id) == TASK_STATUS_PROCESSING or get_sse_queue(session_id) is not None:
        raise HTTPException(status_code=409, detail="当前会话仍在处理或等待流式结果，请先结束本次问答")

    # 同一会话每轮清理进度和结果；MongoDB 历史记录继续保留。
    clear_task(session_id)
    if request.is_stream:
        create_sse_queue(session_id)
    update_task_status(session_id, TASK_STATUS_PROCESSING, request.is_stream)

    if request.is_stream:
        background_tasks.add_task(run_query_graph, session_id, user_query, True)
        return {"message": "结果正在处理中...", "session_id": client_session_id}

    try:
        # 同步 Graph、LLM 和 MCP 在工作线程中执行，避免阻塞事件循环。
        result = await run_in_threadpool(run_query_graph, session_id, user_query, False)
    except KnowledgeRetrievalError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"问答处理失败：{exc}") from exc
    return {
        "message": "处理完成！",
        "session_id": client_session_id,
        "answer": result.get("answer", ""),
        "image_urls": result.get("image_urls", []),
        "done_list": get_done_task_list(session_id),
    }


def run_query_graph(session_id: str, user_query: str, is_stream: bool = True) -> dict[str, Any]:
    initial_state: dict[str, Any] = {
        "original_query": user_query, "session_id": session_id, "is_stream": is_stream,
    }
    try:
        workflow = KBQueryWorkflow()
        if is_stream:
            # 回答节点负责 delta 与 final；这里推进整个图。
            for chunk in workflow.run(initial_state, stream=True):
                logger.debug(chunk)
            result: dict[str, Any] = {}
        else:
            # invoke 返回最终状态字典，不把它当作节点事件迭代。
            result = dict(workflow.run(initial_state, stream=False))
            result["image_urls"] = workflow.node_answer_output._extract_images_from_docs(
                result.get("reranked_docs") or []
            )
            set_task_result(session_id, "answer", result.get("answer", ""))
        update_task_status(session_id, TASK_STATUS_COMPLETED, is_stream)
        return result
    except Exception as exc:
        logger.exception("[%s] 问答流程执行失败", session_id)
        set_task_result(session_id, "error", str(exc))
        update_task_status(session_id, TASK_STATUS_FAILED, is_stream)
        if is_stream:
            push_to_session(session_id, SSEEvent.ERROR, {"error": str(exc)})
            return {}
        raise
    finally:
        if is_stream:
            # 结束信号排在 final / error 之后，队列保留到客户端读取。
            push_to_session(session_id, SSEEvent.CLOSE, {})


@app.get("/stream/{session_id}")
async def stream(session_id: str, request: Request, user: CurrentUser, store: Store) -> StreamingResponse:
    session_id = await run_in_threadpool(store.chat_id, user.id, session_id)
    if session_id is None:
        raise HTTPException(status_code=404, detail="未找到此会话")
    if get_sse_queue(session_id) is None:
        raise HTTPException(status_code=404, detail="未找到流式会话，请先 POST /query 并设置 is_stream=true")
    return StreamingResponse(
        sse_generator(session_id, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.delete("/history/{session_id}")
async def clear_chat_history(session_id: str, user: CurrentUser, store: Store) -> dict[str, Any]:
    session_id = await run_in_threadpool(store.chat_id, user.id, session_id)
    if session_id is None:
        return {"message": "历史会话已清空", "deleted_count": 0}
    if get_task_status(session_id) == TASK_STATUS_PROCESSING:
        raise HTTPException(status_code=409, detail="当前会话仍在处理，请完成后再清空历史")
    count = await run_in_threadpool(clear_history, session_id)
    return {"message": "历史会话已清空", "deleted_count": count}


@app.get("/history/{session_id}")
async def history(session_id: str, user: CurrentUser, store: Store, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    internal_id = await run_in_threadpool(store.chat_id, user.id, session_id)
    if internal_id is None:
        return {"session_id": session_id, "items": []}
    try:
        records = await run_in_threadpool(get_recent_messages, internal_id, limit=limit)
        items = [{
            "_id": str(record.get("_id")) if record.get("_id") is not None else "",
            "session_id": record.get("session_id", ""),
            "role": record.get("role", ""),
            "text": record.get("text", ""),
            "rewritten_query": record.get("rewritten_query", ""),
            "item_names": record.get("item_names") or [],
            "image_urls": record.get("image_urls") or [],
            "ts": record.get("ts"),
        } for record in records]
        return {"session_id": session_id, "items": items}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"历史记录查询失败：{exc}") from exc


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
