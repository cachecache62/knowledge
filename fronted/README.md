# 掌柜智库 Vue 3 前端

使用 Vue 3 Composition API、Vue Router 和 Vite。前端保存在 `fronted/`，后端入口位于 `backend/api/`。MySQL 初始化与账户服务配置见 [登录配置说明](../backend/AUTH.md)。

## 启动

Node.js 要求：20.19+（20 系列）或 22.12+。

在项目根目录执行：

```powershell
cd fronted
npm install
npm run dev
```

- 聊天页：`http://127.0.0.1:5173/#/chat/sess-xxxxxxxx`（进入原 `/#/chat` 时自动补全当前对话 ID）
- 导入页：`http://127.0.0.1:5173/#/import`
- 文档管理页：`http://127.0.0.1:5173/#/documents`
- 导入路由记录：`http://127.0.0.1:5173/#/import-traces`
- 登录页：`http://127.0.0.1:5173/#/login`
- 注册页：`http://127.0.0.1:5173/#/register`

启动时先校验登录状态，未登录仅展示登录、注册页面，不创建业务页面或请求业务数据。注册成功自动登录，随后回到此前请求的问答或导入页；过期时回到登录页，侧边栏提供退出按钮。后端通过持久化的 HttpOnly Cookie 维护会话，默认登录有效期为 6 小时，期间刷新页面或关闭再打开普通浏览器会自动恢复登录；页面访问不会延长有效期。前端不保存密码或登录令牌。注册密码为 8～128 位，支持邮箱校验、密码显隐及确认密码。

按登录配置说明准备 MySQL 并初始化数据库后，另开三个终端，在 `backend` 目录启动：

```powershell
uv run uvicorn api.auth_service:app --host 127.0.0.1 --port 8002
uv run uvicorn api.import_service:app --host 127.0.0.1 --port 8000
uv run uvicorn api.query_service:app --host 127.0.0.1 --port 8001
```

进入业务页需要账户服务和 MySQL 可用；完整问答和导入仍需对应后端及其模型、数据库和外部服务。

## 界面与会话

页面采用浅色侧边栏、居中的回答区和底部圆角输入框。侧边栏支持新对话、搜索最近对话、资料库和账号入口，窄屏下可展开收起。回答支持基础 Markdown 标题、列表、引用、行内代码和代码块，可以复制回答与代码。

最近对话按用户 ID 保存在当前浏览器的 `kb_conversations:<user_id>`，当前会话使用 `kb_session_id:<user_id>`。正文由带身份校验的历史接口读取，列表仅包含该账号在本浏览器记录的会话。退出销毁业务缓存。回答期间禁止切换会话，切换到资料库后问答继续执行。资料库保留原有上传与进度查询。

聊天地址为 `/#/chat/<前端对话 ID>`。新建和切换对话会更新地址；刷新、直接打开链接和浏览器前进后退都按地址中的 ID 加载历史，未登录时登录后返回原对话。ID 仍通过后端的当前用户映射转换为内部会话 ID，链接不会绕过用户隔离，也不包含 MongoDB 消息 `_id`。保留 hash 路由，无需配置服务器路径回退。

点击“最近对话”标题或旁边的箭头可以收起、展开列表，列表超出时内部滚动。点击“搜索对话”会自动展开列表。列表按最后一次消息时间倒序排列，搜索沿用该顺序；打开旧对话或刷新页面不会将它按访问时间置顶。

### 文档管理页面

侧边栏“资料库”可展开为“文档管理”和“上传文档”，原 `/import` 地址与上传流程保持不变。`/documents` 页面读取真实上传记录，提供标题/任务 ID 搜索、导入状态筛选、分页、原文件下载和带确认的文档删除。时间统一显示北京时间，回到页面时重新获取列表。

列表、下载和删除都校验当前登录用户的任务归属，不再读取或写入本地示例记录。下载优先返回本地原文件，本地文件缺失时尝试 MinIO 备份；请求携带登录 Cookie，文件不存在或存储不可用时展示明确错误。

删除前展示文档名称与影响范围，导入中的记录不能删除。清理文件与向量后只将数据库登记标记为 `is_deleted=1`，保留原始记录及上传者归属，页面不再展示已删除文档。成功后刷新当前筛选和分页，删除最后一页的最后一条时自动回到上一页。真实版本、FAQ、发布和回滚尚无后端支持，因此管理页移除了相关模拟入口。

导入服务新增 `GET /documents/{task_id}/download` 与 `DELETE /documents/{task_id}`，详见 [文档文件管理说明](../backend/DOCUMENT_MANAGEMENT.md)。

### 文档导入路由记录

资料库中的「导入路由记录」提供搜索、状态筛选及分页。详情参照时间线与节点详情布局，支持时间线/流程图切换、节点输入输出快照、错误与处理提示、导入结果和 JSON 导出。上传任务卡片及文档管理行操作也提供详情入口。

记录存储在 MySQL；运行中每 3 秒刷新，离开页面或完成后停止轮询。只采集功能启用后的新导入，历史任务不补造数据。长文本、切片和向量用有界摘要展示。后端接口、存储与启用方式见 [导入路由记录说明](../backend/IMPORT_TRACING.md)。

## API 地址

默认导入服务为 `http://127.0.0.1:8000`，问答服务为 `http://127.0.0.1:8001`，账户服务为 `http://127.0.0.1:8002`。

需要自定义时，可在前端目录自行创建 `.env.local`：

```dotenv
VITE_IMPORT_API_BASE=http://127.0.0.1:8000
VITE_QUERY_API_BASE=http://127.0.0.1:8001
VITE_AUTH_API_BASE=http://127.0.0.1:8002
```

修改后重启 Vite；生产构建前也需设置为浏览器实际可访问的地址。`VITE_` 配置会进入浏览器资源，不应放数据库密码。前端与三个服务统一使用 `127.0.0.1` 或统一使用 `localhost`；后端 `AUTH_ALLOWED_ORIGINS` 需包含实际前端来源。MySQL 配置只放在后端。

## 功能与结构

```text
src/
├── App.vue                  # 导航；KeepAlive 保留页面与任务状态
├── api.js                   # API 地址及 HTTP 错误处理
├── main.js                  # Vue 挂载及 hash 路由
├── style.css                # ChatGPT 风格布局及移动端适配
├── views/
│   ├── ImportView.vue       # 多文件选择/拖拽上传、每两秒轮询任务、节点日志
│   ├── DocumentsView.vue    # 真实文档筛选、分页、下载与删除
│   ├── ChatView.vue         # 会话、历史、同步问答、SSE 问答、清空历史
│   └── AuthView.vue         # 注册、登录、校验和密码显隐
├── components/
│   ├── AnswerContent.vue    # 答案文本和参考图片
│   └── TaskProgress.vue     # 可折叠的节点进度
└── utils/
    ├── answer.js            # 兼容图片区块、Markdown 图片和 image_urls
    └── queryStream.js       # SSE 事件处理及连接清理
```

上传字段保持 `files`，使用 `/upload` 返回的 `task_ids` 查询 `/status/{task_id}`。后台按小写扩展名判断文件，所以上传仅接受 `.pdf`、`.md`。

问答字段保持 `query`、`session_id`、`is_stream`。所有请求携带登录 Cookie，业务接口返回 401 时清理登录状态并跳转。流式模式先 POST `/query`，再通过带凭据的 EventSource 连接 `/stream/{session_id}`；处理 `progress`、`delta`、`final`、`error`，也兼容 `final_answer`。非流式直接展示 POST 返回的答案。

轮询请求不会重叠，连续五次请求失败后可以手动重试进度查询；这不会重新上传文件。切换页面时任务继续进行，销毁应用时取消请求、定时器与 SSE。发送期间禁用再次发送和清空，避免同一会话的流式队列被覆盖。

同步问答和历史 API 都返回 `image_urls`，刷新后可以恢复参考图片；答案正文中的图片地址也会被提取。前端不改变现有 Markdown 导入等后端行为。

## 验证与构建

```powershell
npm test
npm run build
npm run preview
```

`npm test` 检查图片解析与 SSE 完成/异常处理；构建输出在 `dist/`。两个新的 FastAPI 服务都能提供 `fronted/dist` 的页面与 `/assets` 资源：构建后访问 `http://127.0.0.1:8000/import.html` 或 `http://127.0.0.1:8001/chat.html`，不需要另开 Vite。未构建时页面会返回明确的构建提示，API 接口仍可单独使用。

任务状态与 SSE 队列在内存中，请使用单个 worker。导入失败会在状态响应中返回 `error`；同步问答失败返回 HTTP 500，流式问答通过 `error` 事件报告并结束连接。未知任务及流式会话返回 HTTP 404。同一会话未完成时再次提交会返回 HTTP 409，防止覆盖队列。

后端协议测试（不访问外部数据库或模型）：在项目根目录运行 `uv run python -m unittest discover -s test -p test_api_frontend.py -v`。

手动验证：导入多个文件，检查节点日志和终态；测试同步及流式提问、图片、历史刷新与清空；切换页面检查任务保留；后端停止时检查错误提示。
