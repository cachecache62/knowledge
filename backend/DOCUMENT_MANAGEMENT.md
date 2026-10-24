# 文档下载、回收站与恢复

接口位于 `api/import_service.py`，默认端口 8000，沿用登录 Cookie 和来源校验。只能操作当前用户在 `kb_import_tasks` 中拥有的任务；其他用户访问与记录不存在均返回 404。

## 接口

| 方法与路径 | 行为 |
| --- | --- |
| `GET /documents` | 查询当前用户的真实导入记录，沿用现有筛选和分页 |
| `GET /documents/{task_id}/download` | 下载原始上传文件，保留中文文件名；优先本地，缺失时回退 MinIO |
| `DELETE /documents/{task_id}` | 将文档移入回收站，保留原文件、备份、图片和知识向量 |
| `GET /documents/trash` | 查询当前用户回收站，支持 keyword、status、page、page_size |
| `POST /documents/{task_id}/restore` | 恢复文档，保留任务 ID 和原导入状态 |

`task_id` 为 UUID。下载返回附件流与 `Cache-Control: private, no-store`，不会把本地路径或对象存储凭据返回给前端。下载文件丢失返回 404，存储不可用返回 503。参数错误返回 422；未登录返回 401。

删除成功返回：

```json
{"task_id": "00000000-0000-0000-0000-000000000001", "message": "文档已移入回收站"}
```

## 回收站与恢复规则

- `pending`、`processing` 状态返回 409，等待完成或失败后再删除。
- 删除只在 MySQL 事务中设置 `is_deleted=1`、`deleted_at`、`deleted_by`，并清空 `active_hash`、清理进程内任务状态；不再调用物理清理。可以重新上传同一文件，新上传创建独立记录。
- 正常列表和下载排除已删除记录。重复删除、重复恢复均幂等返回成功；其他用户和不存在的任务返回 404。
- 回收站列表增加 `deleted_at`（带 UTC 时区的 ISO 时间）和 `can_restore`。历史物理删除记录的 `deleted_at` 为空，显示为不可恢复。
- 恢复前检查本地原文件或 MinIO 备份是否存在；已完成文档还检查 Milvus 切片是否存在。数据缺失返回 409，存储服务异常返回 503，文档继续留在回收站。沿用已有路径边界校验。
- 恢复已完成文档时重新占用文件指纹；已有相同 scope、相同内容的活动文档时返回 409。数据库唯一约束处理并发上传或恢复的冲突，事务回滚。
- 恢复失败文档时保留 `failed` 状态、错误信息以及空 `active_hash`，不会伪装成导入成功。
- 原文件、解析文件、备份和向量会持续占用存储；本版不提供永久删除、自动过期或清空回收站。
- 已生成的历史聊天回答不会随文档删除而改写。

## 知识检索

`utils/document_visibility.py` 以登记库为准：只允许当前 scope 下未删除且 `completed` 的文档参与检索。共享知识库仍允许检索其他用户的活动共享文档。

`utils/milvus_utils.py` 在统一构建混合检索请求时，将文档可见性与原有商品名条件组合，同时作用于稠密和稀疏检索，覆盖普通召回、HyDE 和商品名查询。返回字段附带 `source_task_id`、`file_title`，检索结果返回后再校验一次。

Milvus 的 `exists` 判断需要单独括起：`(exists source_task_id) and (source_task_id in [...])`。旧向量条件写作 `(not (exists source_task_id)) and (file_title in [...])`，防止服务端把复合逻辑错误解析为存在性判断的操作数。检索或可见性查询失败会抛出 `KnowledgeRetrievalError`，由问答错误通道展示，不能将服务故障解释为正常的零匹配结果。

重排阶段保留上述来源字段，答案节点在构建提示词前重新读取文档状态，过滤召回后才被删除的文档；联网搜索结果照常保留。已经开始生成的回答不追溯撤销。数据库异常时不会回退为无过滤检索。

### 提问错误与未匹配结果

产品名称提取依赖 `ITEM_MODEL`。调用失败或模型返回无效 JSON 时会抛出 `KnowledgeRetrievalError`，不会再返回空名称并伪装为「未找到相关产品」。同步问答以 HTTP 503 返回可读原因，流式问答通过 SSE error 事件返回。

例如供应商返回 `403 / AllocationQuota.FreeTierOnly` 表示该模型的免费额度已耗尽且启用了免费额度用完即停。需在模型服务侧检查额度或选择有可用额度的模型；如选择按量付费，须自行确认对应计费设置。应用不会自动修改模型配置或付费开关。该错误发生在产品名称识别阶段，尚未进入正文检索，与文档是否成功入库是不同的检查。

模型合法返回 `item_names: []` 才属于正常的未提取到产品名称。JSON 代码围栏会先移除；缺失或为空的 `rewritten_query` 回退到原问题，并保留已提取的产品名称。

### 聊天历史写入

`node_item_name_confirm` 创建用户消息，随后通过该消息的 `message_id` 更新改写问题和产品名称；助手回复统一由 `node_answer_output` 保存。未找到产品或需要用户确认时，确认节点只设置 `state.answer`，不提前插入助手消息，避免后续答案输出节点再次保存相同回复。同步和流式问答遵循相同规则；用户重复提问仍分别保留每轮记录。此修改不清理已有重复记录。

## 新旧数据兼容

首次使用文档登记库时自动补齐 `storage_path`、`object_key`、`vector_key`、`is_deleted`，以及本版新增的 `deleted_at DATETIME NULL` 和 `deleted_by INTEGER NULL`。不清空已有数据；应用数据库用户需要允许增加列。

新导入在两个向量集合中写入动态字段 `source_task_id`，按该任务标识判断可见性。旧记录通过上传目录的任务 ID 查找原文件，必要时查询备份对象键；不按可能曾使用不同时区的创建时间猜测路径。

旧向量没有任务标识时，仅在标题归属唯一的情况下按 `file_title` 匹配，同时排除带任务标识的新向量。存在同名登记（包括回收站）时不猜测归属，旧文档删除和恢复返回 409；需先核对并补齐任务标识。不在登记库中的历史向量也不参与检索。

更新后重启导入服务和查询服务，首次访问时自动补列。前端重新执行 `npm run build` 或使用 Vite 开发服务。未修改现有账户时间策略或 `.env`。

## 验证

在 `backend` 运行：

```powershell
.\.venv\Scripts\python.exe -m unittest test.test_document_management test.test_api_frontend test.test_database_errors
```

测试使用内存 SQLite、临时文件和 MinIO/Milvus 替身，覆盖权限、原文件及备份下载、保留资源的软删除、回收站筛选分页、恢复与重复请求、相同内容冲突、文件及向量缺失、存储故障、检索可见性和旧表升级。不会通过这些测试修改真实业务文档。

服务端过滤语法另有只读集成回归，不调用模型或修改数据库。需要连接真实 MySQL 和 Milvus，显式启用后运行：

```powershell
$env:RUN_MILVUS_READONLY_TESTS = '1'
.\.venv\Scripts\python.exe -m unittest test.test_milvus_visibility_live
```

该检查验证当前有效文档、空集合、旧向量标题和商品名组合条件的实际服务端解析与返回范围。

在 `fronted` 运行 `npm test`、`npm run build`。页面交互测试执行真实 Vue 组件，覆盖删除后恢复、按钮防重提交、缓存页面刷新、恢复错误和最后一页恢复后的分页修正。
