import logging
import traceback
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from auth.store import AuthStore, User, get_auth_store
from config.auth_config import get_auth_config


logger = logging.getLogger(__name__)


def check_request_origin(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    allowed = get_auth_config().allowed_origins
    if (origin and origin.rstrip("/") not in allowed) or request.headers.get("sec-fetch-site") == "cross-site" and not origin:
        raise HTTPException(status_code=403, detail="不允许此来源的请求")


def require_cookie(request: Request) -> str:
    check_request_origin(request)
    token = request.cookies.get(get_auth_config().cookie_name)
    if not token or len(token) > 256:
        raise HTTPException(status_code=401, detail="请先登录")
    return token


def get_current_user(
    token: Annotated[str, Depends(require_cookie)],
    store: Annotated[AuthStore, Depends(get_auth_store)],
) -> User:
    user = store.authenticated_user(token)
    if user is None:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
Store = Annotated[AuthStore, Depends(get_auth_store)]


def configure_auth_support(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(get_auth_config().allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(SQLAlchemyError)
    async def database_unavailable(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        # 不使用 str(exc)/exc_info，它们可能包含 SQL 参数中的密码或会话信息。
        driver_error = getattr(exc, "orig", None)
        driver_args = getattr(driver_error, "args", ())
        mysql_code = driver_args[0] if driver_args and isinstance(driver_args[0], int) else None
        frames = " -> ".join(
            f"{Path(frame.filename).name}:{frame.lineno}({frame.name})"
            for frame in traceback.extract_tb(exc.__traceback__)
        )
        logger.error(
            "数据库请求异常 method=%s path=%s type=%s driver=%s mysql_code=%s stack=%s",
            request.method, request.url.path, type(exc).__name__,
            type(driver_error).__name__ if driver_error else None, mysql_code, frames,
        )
        # 不把数据库地址、驱动异常或 SQL 参数返回给浏览器。
        return JSONResponse(status_code=503, content={"detail": "账户数据库暂时不可用，请稍后重试。"})
