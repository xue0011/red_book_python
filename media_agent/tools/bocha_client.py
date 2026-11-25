"""博查 Web Search 客户端 —— 用真实检索替换"模型凭空编造对标账号"。

规模控制（都在 ``config.py`` 里，测试阶段默认最省）：

- ``COLLECT_FULL_TEXT=false``：每条只保留标题 + 链接 + **摘要前 160 字**，不抓正文、不抓图；
- ``COLLECT_FULL_TEXT=true``：额外保留 ``MAX_TEXT_CHARS`` 字正文与站点图标；
- ``MAX_ITEMS_PER_SOURCE``：单次检索最多保留多少条，超过直接截断。

约定：**任何异常都返回空列表**，绝不把网络问题抛给 LangGraph；调用方据此降级。
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Iterable

from config import settings

log = logging.getLogger(__name__)

SHORT_TEXT = 160           # 精简模式：摘要截断长度
DEFAULT_PER_QUERY = 8      # 每条检索词要几条
MIN_CHARS = 40             # 太短的 snippet 不进候选

# 垃圾站黑名单：博查的结果里混着大量 SEO 农场、小说站、成人站，标题像正文其实全是广告。
JUNK_DOMAINS = (
    "suizhouhszh.org.cn",
    "lan-niao",
    "xueba",
    "yuzhua.com",
    "iiyi.com",
)
JUNK_TITLE_PATTERNS = (
    "全文阅读",
    "全本",
    "无弹窗",
    "免费阅读",
    "大结局",
    "小说",
    "文学网",
    "txt下载",
    "夸克口令",
    "岗位职责",
    "招聘",
    "培训",
    "课程",
    "类目",
    "小店",
    "领取",
)
BLOCK_WORDS = ("porn", "成人", "性爱", "裸")

# 平台域名：只有落在这些域名下的链接才算"真实账号主页/作品页"
PLATFORM_DOMAINS = {
    "douyin.com": "抖音",
    "iesdouyin.com": "抖音",
    "xiaohongshu.com": "小红书",
    "xhslink.com": "小红书",
    "bilibili.com": "B站",
    "b23.tv": "B站",
    "channels.weixin.qq.com": "视频号",
}


def platform_of_url(url: str) -> str:
    """从链接判断所属平台；判断不出来返回空串。"""
    url = (url or "").lower()
    for domain, name in PLATFORM_DOMAINS.items():
        if domain in url:
            return name
    return ""


OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
OG_IMAGE_RE_REVERSED = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image["\']',
    re.IGNORECASE,
)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36"


def fetch_og_image(url: str, timeout: float = 5.0, max_bytes: int = 200_000) -> str:
    """抓页面 og:image 当配图。只在 ``COLLECT_FULL_TEXT=true`` 时才会被调用。"""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            head = resp.read(max_bytes).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - 拿不到图不是错误
        return ""
    match = OG_IMAGE_RE.search(head) or OG_IMAGE_RE_REVERSED.search(head)
    image = match.group(1) if match else ""
    return image if image.startswith("http") else ""


def _is_junk(title: str, url: str, site: str) -> bool:
    lowered = f"{title} {url} {site}".lower()
    if any(word in lowered for word in BLOCK_WORDS):
        return True
    domain = _normalize_url(url)
    if any(junk in domain for junk in JUNK_DOMAINS):
        return True
    return any(pattern.lower() in title.lower() for pattern in JUNK_TITLE_PATTERNS)


@dataclass(slots=True)
class Evidence:
    """一条检索命中。id 由 search_many 统一编号（E1、E2…），供表格与模型引用。"""

    title: str
    url: str
    site: str = ""
    snippet: str = ""
    summary: str = ""
    published_at: str = ""
    site_icon: str = ""
    image: str = ""
    platform: str = ""
    id: str = ""

    def to_prompt_line(self) -> str:
        """给模型看的一行：编号 + 标题 + 站点 + 摘要。"""
        bits = [f"[{self.id}] {self.title}"]
        if self.site:
            bits.append(f"（{self.site}）")
        if self.snippet:
            bits.append(f" —— {self.snippet}")
        return "".join(bits)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "site": self.site,
            "snippet": self.snippet,
            "published_at": self.published_at,
            "platform": self.platform,
            "image": self.image,
            "site_icon": self.site_icon,
        }


def _truncate(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _normalize_url(url: str) -> str:
    """去掉查询串与锚点，只留 path，用于跨查询去重。"""
    url = (url or "").strip()
    for sep in ("?", "#"):
        if sep in url:
            url = url.split(sep, 1)[0]
    return url.rstrip("/").lower()


def search(
    query: str,
    count: int = DEFAULT_PER_QUERY,
    freshness: str = "oneMonth",
    timeout: float = 20.0,
) -> list[Evidence]:
    """一次检索。失败返回 []（不抛异常）。"""
    query = (query or "").strip()
    if not query or not settings.bocha_key_ok:
        return []

    count = max(1, min(int(count or DEFAULT_PER_QUERY), settings.MAX_ITEMS_PER_SOURCE))
    body = json.dumps(
        {"query": query, "summary": bool(settings.COLLECT_FULL_TEXT), "count": count, "freshness": freshness},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        settings.BOCHA_SEARCH_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {settings.BOCHA_API_KEY.strip()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        log.warning("博查检索失败 %r: %s", query, exc)
        return []

    pages = (((payload.get("data") or {}).get("webPages") or {}).get("value")) or []
    items: list[Evidence] = []
    for page in pages[:count]:
        title = _truncate(page.get("name") or "", 100)
        url = (page.get("url") or "").strip()
        site = _truncate(page.get("siteName") or "", 40)
        if not title or not url or _is_junk(title, url, site):
            continue

        snippet = _truncate(page.get("snippet") or "", SHORT_TEXT)
        summary = ""
        if settings.COLLECT_FULL_TEXT:
            summary = _truncate(page.get("summary") or "", max(settings.MAX_TEXT_CHARS, SHORT_TEXT))

        if len(snippet) < MIN_CHARS and len(summary) < MIN_CHARS:
            continue

        items.append(
            Evidence(
                title=title,
                url=url,
                site=site,
                snippet=snippet or _truncate(summary, SHORT_TEXT),
                summary=summary,
                published_at=(page.get("datePublished") or "")[:10],
                site_icon=page.get("siteIcon") or "",
                platform=platform_of_url(url),
            )
        )
    return items


def search_many(
    queries: Iterable[str],
    per_query: int = DEFAULT_PER_QUERY,
    timeout: float = 20.0,
) -> list[Evidence]:
    """多条检索词依次检索、按 URL 去重，统一编号 E1…En（最多 MAX_ITEMS_PER_SOURCE 条）。"""
    merged: list[Evidence] = []
    seen: set[str] = set()

    for query in queries:
        for item in search(query, count=per_query, timeout=timeout):
            key = _normalize_url(item.url)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= settings.MAX_ITEMS_PER_SOURCE:
                break
        if len(merged) >= settings.MAX_ITEMS_PER_SOURCE:
            break

    for index, item in enumerate(merged, start=1):
        item.id = f"E{index}"

    # 配图只在全文模式下抓：默认 false 时不发任何额外请求，省时间也省 token
    if settings.COLLECT_FULL_TEXT and merged:
        targets = merged[:8]
        with ThreadPoolExecutor(max_workers=4) as pool:
            images = list(pool.map(lambda it: fetch_og_image(it.url), targets))
        for item, image in zip(targets, images):
            item.image = image

    return merged


def build_queries(brief: str, occupation: str, platforms: list[str], limit: int = 3) -> list[str]:
    """从输入拼检索词：先账号类，再内容类，最后平台限定类。"""
    platform = (platforms or ["抖音"])[0]
    core = (occupation or "").strip() or "自媒体账号"
    queries = [
        f"{core} {platform} 账号 涨粉 案例",
        f"{core} 短视频 爆款 选题 结构",
        f"{core} {platform} 运营 经验 分享",
    ]
    # brief 里如果提到风格/人群，补一条长尾（截断避免超长查询）
    tail = _truncate(brief.replace("\n", " "), 60)
    if tail:
        queries.append(f"{core} {tail}")
    return queries[:limit]
