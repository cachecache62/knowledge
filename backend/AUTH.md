# MySQL 注册与登录

前端启动时请求 `/auth/me`，未登录只展示登录、注册页，登录成功后才创建业务页面。浏览器持久保存登录 Cookie，默认从注册或登录成功起有效 6 小时；期间刷新页面、关闭再打开浏览器均自动恢复登录，无需重复输入账户密码。访问页面不会延长有效期，过期或主动退出后需重新登录。退出会撤销服务端会话并销毁业务页面缓存。

## 启动

1. 在 `backend` 执行 `uv sync`。
2. 将 [auth.env.example](auth.env.example) 中的配置合并到自己的 `backend/.env`，或设置为系统环境变量。填写实际 MySQL 用户名和密码。默认连接 `127.0.0.1:3306`、数据库 `knowledge_base`。本次实现没有修改现有 `.env`。
3. 确认 MySQL 运行后，在 `backend` 初始化：

   ```powershell
   uv run python -m tool.init_auth_db
   ```

   初始化只创建指定数据库和缺少的账户相关表，不清空已有数据。数据库已存在时，应用首次连接也会创建缺少的表。

4. 在 `backend` 分别打开三个终端启动：

   ```powershell
   uv run uvicorn api.auth_service:app --host 127.0.0.1 --port 8002
   uv run uvicorn api.import_service:app --host 127.0.0.1 --port 8000
   uv run uvicorn api.query_service:app --host 127.0.0.1 --port 8001
   ```

   账户服务不导入模型、MinIO 或工作流；单独验证注册登录只需要 MySQL 和账户服务。

5. 在 `fronted` 执行 `npm run dev`，打开 `http://127.0.0.1:5173`。首次使用选择注册，注册成功后自动登录。

## 配置

| 变量 | 默认值 / 用途 |
| --- | --- |
| `MYSQL_HOST`、`MYSQL_PORT` | `127.0.0.1`、`3306` |
| `MYSQL_USER`、`MYSQL_PASSWORD` | 本地默认 `root`、空密码，需填写实际凭据 |
| `MYSQL_DATABASE` | `knowledge_base` |
| `AUTH_SESSION_HOURS` | `6`，单位为小时，允许 1～720 小时；Cookie 和服务端会话使用相同有效期 |
| `AUTH_COOKIE_SECURE` | 本地 HTTP 为 `false`，部署 HTTPS 后设置为 `true` |
| `AUTH_ALLOWED_ORIGINS` | 允许携带 Cookie 的明确来源，逗号分隔，不能使用 `*` |

前端的 `VITE_AUTH_API_BASE` 默认为 `http://127.0.0.1:8002`，导入、问答分别默认为 `http://127.0.0.1:8000` 和 `http://127.0.0.1:8001`。可以在 `fronted/.env.local` 中设置，修改后重启 Vite，生产构建需在构建前设置。

旧的 `AUTH_SESSION_DAYS` 配置不再使用；未设置 `AUTH_SESSION_HOURS` 时默认生效 6 小时。修改后重启账户、导入、问答三个服务。服务端同时按会话创建时间校验有效期，因此旧版签发的 7 天 Cookie 也无法超过新的登录有效期。普通浏览器需保留 Cookie；无痕模式或退出时自动清除 Cookie 的浏览器不能在关闭后恢复登录。

本地前端和三个 API 请统一使用 `127.0.0.1`，或统一使用 `localhost`，避免 Cookie 因主机名不同而不发送。更换前端端口时，把实际来源加入 `AUTH_ALLOWED_ORIGINS`；例如 preview 默认端口 4173。请求使用 `credentials: include`，SSE 使用 `withCredentials: true`。部署时建议通过同一站点反向代理各服务。

## 接口与存储

| 方法 | 账户服务路径 | 行为 |
| --- | --- | --- |
| POST | `/auth/register` | JSON：`email`、`password`；邮箱唯一，密码 8～128 位；成功自动登录 |
| POST | `/auth/login` | JSON：`email`、`password`；失败返回统一 401，成功轮换当前登录会话 |
| GET | `/auth/me` | 当前用户；未登录、过期、停用返回 401 |
| POST | `/auth/logout` | 撤销当前会话，删除 Cookie |

密码使用 Argon2 哈希。登录令牌随机生成，数据库只保存 SHA-256 摘要，原令牌通过 HttpOnly、SameSite=Lax Cookie 发送。账户响应不缓存，写请求校验 Origin，参数错误不回显密码。

MySQL 表为 `kb_users`（用户）、`kb_login_sessions`（登录会话）、`kb_chat_sessions`（用户对话映射）、`kb_import_tasks`（任务归属）。问答、历史读取和清空、SSE、上传与任务查询均校验登录身份。健康检查及前端静态文件公开，以便展示登录页。直接调用业务 API 的客户端也要先登录并携带 Cookie。

`kb_users.updated_at` 记录用户信息的更新时间，项目写入时使用 UTC。注册时与 `created_at` 相同，通过 SQLAlchemy 更新用户资料时自动更新；单纯登录不会改变它。运行初始化命令或新进程首次连接时会给旧表补齐该字段，已有用户先按创建时间回填。重复初始化保留已有的更新时间。直接执行原生 SQL 修改用户时，需要同时设置 UTC 的 `updated_at`。

### UTC 存储与北京时间显示

所有 MySQL 时间字段（包括登录的 `expires_at` 和导入登记表 `kb_import_documents`）继续保存 UTC，数据库中看到的值比北京时间少 8 小时是预期行为。应用连接及初始化连接设置会话时区为 `+00:00`，使 `CURRENT_TIMESTAMP` 默认值也使用 UTC。原有 UTC 数据无需迁移，不要对历史记录执行加 8 小时的 `UPDATE`。

数据库客户端需要查看北京时间时，只在查询结果中转换，原始值保持不变：

```sql
SELECT id, email,
       CONVERT_TZ(created_at, '+00:00', '+08:00') AS created_at_beijing,
       CONVERT_TZ(updated_at, '+00:00', '+08:00') AS updated_at_beijing
FROM kb_users
ORDER BY id DESC
LIMIT 5;
```

这里使用固定时差，不依赖 MySQL 命名时区表。`DATETIME` 列不会因为修改客户端的 `time_zone` 而自动转换显示，查询时应显式转换。

账户 API 的 `created_at` 继续返回带 `Z` 的 UTC 时间，登录有效期计算保持不变。聊天消息时间显示显式指定 `Asia/Shanghai`，即使浏览器使用其他时区也显示北京时间。以后新增日期展示时，同样使用显式时区，例如 `new Date(user.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })`；不要去掉 `Z` 或先加 8 小时再交给浏览器转换，以免重复转换。

更新代码后重启账户、导入和问答三个服务，使连接池的 UTC 会话配置生效。

相同浏览器会话 ID 在不同账号下映射到不同内部 ID，防止读取或清空他人历史。导入进度仅任务所属账号可见。对话正文仍保存在 MongoDB；文档和向量仍属于原有的共享知识库，本次没有改成每用户私有文档检索。

浏览器按用户 ID 保存 `kb_conversations:<user_id>` 和 `kb_session_id:<user_id>`，不保存密码或登录令牌。退出后清空页面内存，再登录同一账号可恢复本浏览器的列表。旧匿名历史不会自动归属给新注册用户，原数据库记录保持原样。

## 验证

在 `backend` 运行 `uv run python -m unittest discover -s test -p 'test_*.py'`。认证测试默认使用内存 SQLite。设置 `AUTH_TEST_MYSQL_URL` 可连接专用 MySQL 测试库；库名必须以 `kb_auth_test_` 开头，测试会清空该测试库的账户相关表，不能指向实际使用的库。

在 `fronted` 运行 `npm test` 和 `npm run build`。浏览器联调已验证未登录拦截、注册、错误密码、刷新、退出、多账号切换、登录撤销、上传及带 Cookie 的 SSE。账户与会话使用独立的真实 MySQL 测试实例，模型和外部存储工作流使用测试替身。

参考：[FastAPI 密码哈希](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)、[Starlette CORS](https://starlette.dev/middleware/)。
