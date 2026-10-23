"""独立账户服务：不导入模型、MinIO 或知识库工作流。"""

import secrets
from datetime import timedelta
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, EmailStr, Field
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from sqlalchemy.exc import IntegrityError
from starlette.responses import JSONResponse

from api.frontend import register_frontend
from auth.dependencies import CurrentUser, Store, check_request_origin, configure_auth_support
from auth.store import User, beijing_now
from config.auth_config import get_auth_config

app = FastAPI(title="掌柜智库-账户API")
configure_auth_support(app)
register_frontend(app, "login")
password_hasher = PasswordHash.recommended()
dummy_password_hash = password_hasher.hash(secrets.token_urlsafe(24))


@app.exception_handler(RequestValidationError)
async def invalid_account_payload(request: Request, exc: RequestValidationError) -> JSONResponse:
    # 不把密码原文随 Pydantic 的 input 字段回显到错误响应中。
    fields = {error["loc"][-1] for error in exc.errors() if error["loc"]}
    if "email" in fields:
        detail = "请输入有效的邮箱地址"
    elif "password" in fields:
        detail = "密码长度不符合要求：注册需 8 至 128 位，登录最多 128 位。"
    else:
        detail = "请检查提交的账户信息"
    return JSONResponse(status_code=422, content={"detail": detail})


class LoginRequest(BaseModel):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)


class RegisterRequest(LoginRequest):
    password: str = Field(min_length=8, max_length=128)


def login_response(user: User, request: Request, response: Response, store: Store) -> dict[str, object]:
    config = get_auth_config()
    token = secrets.token_urlsafe(32)
    max_age = config.session_hours * 60 * 60
    expires_at = beijing_now() + timedelta(seconds=max_age)
    store.create_login(user.id, token, expires_at, request.cookies.get(config.cookie_name))
    response.set_cookie(
        config.cookie_name, token, max_age=max_age, httponly=True,
        secure=config.cookie_secure, samesite="lax", path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"user": user.public()}


@app.post("/auth/register", status_code=201, dependencies=[Depends(check_request_origin)])
def register(payload: RegisterRequest, request: Request, response: Response, store: Store) -> dict[str, object]:
    email = str(payload.email).lower()
    try:
        user = store.create_user(
        email,
        password_hasher.hash(payload.password),
        payload.password,
        )
    except IntegrityError as exc:
        # 唯一索引同时处理并发注册，注册失败不签发会话。
        if store.find_user(email):
            raise HTTPException(status_code=409, detail="该邮箱已注册，请直接登录") from exc
        raise
    return login_response(user, request, response, store)


@app.post("/auth/login", dependencies=[Depends(check_request_origin)])
def login(payload: LoginRequest, request: Request, response: Response, store: Store) -> dict[str, object]:
    record = store.find_user(str(payload.email).lower())
    encoded = str(record["password_hash"]) if record else dummy_password_hash
    try:
        valid = password_hasher.verify(payload.password, encoded)
    except (ValueError, UnknownHashError):
        valid = False
    if not valid or not record or not record["is_active"]:
        raise HTTPException(status_code=401, detail="邮箱或密码不正确")
    user = User(int(record["id"]), str(record["email"]), record["created_at"])
    return login_response(user, request, response, store)


@app.get("/auth/me")
def me(response: Response, user: CurrentUser) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    return {"user": user.public()}


@app.post("/auth/logout", dependencies=[Depends(check_request_origin)])
def logout(request: Request, response: Response, store: Store) -> dict[str, str]:
    config = get_auth_config()
    token = request.cookies.get(config.cookie_name)
    if token:
        store.revoke_login(token)
    response.delete_cookie(config.cookie_name, path="/", httponly=True, secure=config.cookie_secure, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return {"message": "已退出登录"}


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8002)
