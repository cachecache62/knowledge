"""以文档登记为准过滤知识检索，不修改或复制回收站中的向量。"""

import json
from collections import Counter
from pathlib import Path
from typing import Any

from utils.import_registry import get_import_registry


class KnowledgeRetrievalError(RuntimeError):
    """检索不可用，不能作为正常的零匹配结果继续生成回答。"""


def visible_sources() -> tuple[set[str], set[str]]:
    registry = get_import_registry()
    rows = registry.retrieval_sources()
    # 无任务标识的旧向量只能按唯一标题归属；跨 scope 或同名时也不能猜测。
    title_counts = Counter(Path(row["filename"]).stem for row in rows)
    task_ids: set[str] = set()
    titles: set[str] = set()
    for row in rows:
        if row["scope"] != registry.scope or row["is_deleted"] or row["status"] != "completed":
            continue
        if row["vector_key"]:
            task_ids.add(row["vector_key"])
        else:
            title = Path(row["filename"]).stem
            if title_counts[title] == 1:
                titles.add(title)
                # 旧商品名节点曾把转义后的标题直接存入向量字段。
                titles.add(title.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'"))
    return task_ids, titles


def build_retrieval_filter(expr: str | None = None) -> str:
    task_ids, titles = visible_sources()
    # 使用非空的无效任务标识，避免生成 Milvus 不接受的空 in 表达式。
    task_values = json.dumps(sorted(task_ids) or [""], ensure_ascii=False)
    # exists 判断必须单独加括号，避免 Milvus 把后面的逻辑表达式一并解析进去。
    visibility = f"((exists source_task_id) and (source_task_id in {task_values}))"
    if titles:
        title_values = json.dumps(sorted(titles), ensure_ascii=False)
        visibility = f"({visibility} or ((not (exists source_task_id)) and (file_title in {title_values})))"
    return f"({expr}) and ({visibility})" if expr else visibility


def filter_visible_documents(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """在检索完成和构建提示词时复查状态，保留联网搜索资料。"""
    if not items or all(item.get("source") == "web" for item in items):
        return items
    task_ids, titles = visible_sources()
    result = []
    for item in items:
        if item.get("source") == "web":
            result.append(item)
            continue
        entity = item.get("entity", item)
        task_id = entity.get("source_task_id")
        if task_id in task_ids or (task_id is None and entity.get("file_title") in titles):
            result.append(item)
    return result
