"""为两个 API 服务提供项目根目录中的 Vue 构建产物。"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse, RedirectResponse

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "fronted" / "dist"


def register_frontend(app: FastAPI, default_page: str) -> None:
    # 允许先启动 API，再构建前端。
    app.mount(
        "/assets",
        StaticFiles(directory=str(FRONTEND_DIST / "assets"), check_dir=False),
        name="frontend_assets",
    )

    @app.get("/", include_in_schema=False)
    async def frontend_home() -> RedirectResponse:
        return RedirectResponse(f"/index.html#/{default_page}")

    @app.get("/index.html", include_in_schema=False)
    async def frontend_index() -> FileResponse:
        index_path = FRONTEND_DIST / "index.html"
        if not index_path.is_file():
            raise HTTPException(
                status_code=503,
                detail="Vue 前端尚未构建，请在 fronted 目录执行 npm install 和 npm run build；开发时可使用 npm run dev。",
            )
        return FileResponse(index_path)

    @app.get("/chat.html", include_in_schema=False)
    async def frontend_chat() -> RedirectResponse:
        return RedirectResponse("/index.html#/chat")

    @app.get("/import.html", include_in_schema=False)
    async def frontend_import() -> RedirectResponse:
        return RedirectResponse("/index.html#/import")
