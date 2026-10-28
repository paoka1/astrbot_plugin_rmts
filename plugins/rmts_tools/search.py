"""
DeepSeek 网页搜索：https://api-docs.deepseek.com/zh-cn/guides/search/
"""

from typing import Optional

import httpx

SEARCH_URL = "https://api.deepseek.com/v1/tools/search"


class SearchError(Exception):
    """搜索异常"""


async def search_text(api_key: str, query: str, ori_question: Optional[str] = None,
                      count: int = 5, summary_length: int = 500, timeout: float = 30.0) -> str:
    """搜索网页，返回给模型阅读的文本"""
    body = {"query": query[:128], "count": count}
    if ori_question:
        body["ori_question"] = ori_question[:128]

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(SEARCH_URL, json=body, headers={"Authorization": f"Bearer {api_key}"})
    except httpx.HTTPError as e:
        raise SearchError(f"网络请求失败: {e}")
    if response.status_code != 200:
        raise SearchError(f"HTTP {response.status_code}: {response.text[:200]}")

    results = response.json().get("results") or []
    if not results:
        return f"没有搜索到和「{query}」相关的网页"

    items = []
    for i, r in enumerate(results, 1):
        source = "，".join(x for x in (r.get("site_name"), (r.get("date") or "")[:10]) if x)
        title = f"{i}. {r.get('title', '')}" + (f"（{source}）" if source else "")
        items.append(f"{title}\n{r.get('url', '')}\n{(r.get('summary') or r.get('snippet') or '')[:summary_length]}")
    return f"「{query}」的搜索结果：\n\n" + "\n\n".join(items)
