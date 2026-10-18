# 掌柜智库 Vue 3 前端

由 `test/fastapi/web/page/import.html` 和 `chat.html` 迁移而来，使用 Vue 3 Composition API、Vue Router 和 Vite。配套后端入口位于项目根目录的 `api/`。

## 启动

Node.js 要求：20.19+（20 系列）或 22.12+。

在项目根目录执行：

```powershell
cd fronted
npm install
npm run dev
```

- 聊天页：`http://127.0.0.1:5173/#/chat`
- 导入页：`http://127.0.0.1:5173/#/import`
- 登录页：`http://127.0.0.1:5173/#/login`
- 注册页：`http://127.0.0.1:5173/#/register`

登录、注册页与问答、资料库使用统一的灰白配色，支持邮箱校验、密码显隐及注册确认密码。当前后端未提供账号鉴权接口，因此提交只进行本地校验并提示页面预览，不发送认证请求或模拟登录成功，也不保存账号或密码。注册的 8 位最小密码长度为暂定设计规则，接入服务端时应核对实际约束。账号页不进入 KeepAlive 缓存；离开账号页后重新进入会清空表单，登录与注册切换时保留邮箱并清空密码。问答和导入页仍保留原有状态与访问方式。

另开两个终端，在 Python 项目根目录启动现有后端：

```powershell
uv run uvicorn api.import_service:app --host 127.0.0.1 --port 8000
uv run uvicorn api.query_service:app --host 127.0.0.1 --port 8001
```

前端可以独立启动；完整业务仍需后端所依赖的数据库、模型和外部服务。

## 界面与会话

页面采用浅色侧边栏、居中的回答区和底部圆角输入框。侧边栏支持新对话、搜索最近对话、资料库和账号入口，窄屏下可展开收起。回答支持基础 Markdown 标题、列表、引用、行内代码和代码块，可以复制回答与代码。

最近对话的 ID 和标题保存在当前浏览器的 `kb_conversations`，沿用 `kb_session_id` 保存当前会话。正文仍由后端历史接口读取；列表仅包含当前浏览器记录的会话。回答期间禁止切换会话，切换到资料库后问答继续执行。资料库保留原有上传与进度查询。

## API 地址

默认导入服务为 `http://127.0.0.1:8000`，问答服务为 `http://127.0.0.1:8001`。

需要自定义时，可在前端目录自行创建 `.env.local`：

```dotenv
VITE_IMPORT_API_BASE=http://127.0.0.1:8000
VITE_QUERY_API_BASE=http://127.0.0.1:8001
```

修改后重启 Vite；生产构建前也需设置为浏览器实际可访问的地址。`VITE_` 配置会进入浏览器资源，不应放密钥。根目录的 Python `.env` 不需要修改。

## 功能与结构

```text
src/
├── App.vue                  # 导航；KeepAlive 保留页面与任务状态
├── api.js                   # API 地址及 HTTP 错误处理
├── main.js                  # Vue 挂载及 hash 路由
├── style.css                # ChatGPT 风格布局及移动端适配
├── views/
│   ├── ImportView.vue       # 多文件选择/拖拽上传、每两秒轮询任务、节点日志
│   ├── ChatView.vue         # 会话、历史、同步问答、SSE 问答、清空历史
│   └── AuthView.vue         # 登录/注册页面预览、校验和密码显隐
├── components/
│   ├── AnswerContent.vue    # 答案文本和参考图片
│   └── TaskProgress.vue     # 可折叠的节点进度
└── utils/
    ├── answer.js            # 兼容图片区块、Markdown 图片和 image_urls
    └── queryStream.js       # SSE 事件处理及连接清理
```

上传字段保持 `files`，使用 `/upload` 返回的 `task_ids` 查询 `/status/{task_id}`。后台按小写扩展名判断文件，所以上传仅接受 `.pdf`、`.md`。

问答请求字段保持 `query`、`session_id`、`is_stream`，会话使用原有 `kb_session_id` 本地存储键。流式模式先 POST `/query`，再连接 `/stream/{session_id}`；处理 `progress`、`delta`、`final`、`error`，也兼容 `final_answer`。非流式直接展示 POST 返回的答案。

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
