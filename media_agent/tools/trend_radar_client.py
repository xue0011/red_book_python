"""
热点抓取客户端 — 基于 TrendRadar (sansan0/TrendRadar) 架构


使用 NewsNow API 抓取 35+ 平台热点数据：
抖音、微博、知乎、B站、今日头条、百度、小红书等。


TrendRadar GitHub: https://github.com/sansan0/TrendRadar
NewsNow API: https://newsnow.busiyi.world/api/s
"""


import json
import time
import random
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse


import requests




#  平台 ID 映射 — 来自 TrendRadar config.yaml
PLATFORM_IDS = {
    # 主流平台
    "douyin": "抖音",
    "weibo": "微博",
    "zhihu": "知乎",
    "bilibili-hot-search": "B站热搜",
    "toutiao": "今日头条",
    "baidu": "百度热搜",
    # 更多平台
    "xiaohongshu": "小红书",
    "kuaishou": "快手",
    "wallstreetcn-hot": "华尔街见闻",
    "cls-hot": "财联社",
    "thepaper": "澎湃新闻",
    "tencent-news": "腾讯新闻",
    "neteasenews": "网易新闻",
    "sogou": "搜狗热搜",
    "guancha": "观察者网",
    "ithome": "IT之家",
    "36kr": "36氪",
    "sspai": "少数派",
    "hupu": "虎扑",
    "tieba": "百度贴吧",
    "acfun": "AcFun",
    "juejin": "掘金",
    "csdn": "CSDN",
    "github-trending": "GitHub趋势",
}


# 默认 API（可自部署 newsnow）
DEFAULT_API_URL = "https://newsnow.busiyi.world/api/s"


# 默认请求头
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/91.0.4472.124 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}




class TrendRadarClient:
    """
    热点抓取客户端 — 基于 TrendRadar 的 DataFetcher 架构。


    使用 NewsNow 聚合 API 获取多平台热点榜单，
    支持自定义 API 地址（可自部署 newsnow）。


    使用方式:
        client = TrendRadarClient()
        topics = client.fetch_hot_topics(["douyin", "weibo", "zhihu"])
        # topics: [{"title": "...", "url": "...", "platform": "抖音", "rank": 1}, ...]
    """


    def __init__(self, api_url: str = None, proxy_url: str = None):
        """
        Args:
            api_url: NewsNow API 地址，默认使用公共 API
            proxy_url: 代理地址（可选）
        """
        self.api_url = api_url or DEFAULT_API_URL
        self.proxy_url = proxy_url


    #  单平台抓取
    def fetch_platform(
        self,
        platform_id: str,
        max_retries: int = 2,
    ) -> List[Dict]:
        """
        抓取单个平台的热点数据。


        Args:
            platform_id: 平台 ID（如 "douyin", "weibo"）
            max_retries: 最大重试次数


        Returns:
            热点列表 [{"title": str, "url": str, "rank": int}, ...]
        """
        url = f"{self.api_url}?id={platform_id}&latest"
        proxies = {"http": self.proxy_url, "https": self.proxy_url} if self.proxy_url else None
        platform_name = PLATFORM_IDS.get(platform_id, platform_id)


        for attempt in range(max_retries + 1):
            try:
                resp = requests.get(
                    url, proxies=proxies, headers=DEFAULT_HEADERS, timeout=10
                )
                resp.raise_for_status()
                data = resp.json()


                status = data.get("status", "")
                if status not in ("success", "cache"):
                    raise ValueError(f"API 状态异常: {status}")


                items = data.get("items", [])
                topics = []
                for i, item in enumerate(items, 1):
                    title = item.get("title")
                    if not title or not str(title).strip():
                        continue
                    topics.append({
                        "title": str(title).strip(),
                        "url": item.get("url", ""),
                        "mobile_url": item.get("mobileUrl", ""),
                        "platform": platform_name,
                        "platform_id": platform_id,
                        "rank": i,
                        "heat": self._estimate_heat(i, len(items)),
                    })


                status_info = "最新" if status == "success" else "缓存"
                print(f"[TrendRadar] {platform_name} 获取成功（{status_info}，{len(topics)} 条）")
                return topics


            except Exception as e:
                if attempt < max_retries:
                    wait = random.uniform(2, 5)
                    print(f"[TrendRadar] {platform_name} 失败，{wait:.1f}s 后重试: {e}")
                    time.sleep(wait)
                else:
                    print(f"[TrendRadar] {platform_name} 最终失败: {e}")
                    return []


        return []


    #  多平台批量抓取
    def fetch_hot_topics(
        self,
        platform_ids: List[str] = None,
        request_interval: float = 0.3,
    ) -> Dict[str, List[Dict]]:
        """
        批量抓取多个平台的热点。


        Args:
            platform_ids: 平台 ID 列表，默认抓取主流 6 个平台
            request_interval: 请求间隔（秒）


        Returns:
            {平台ID: [热点列表], ...}
        """
        if platform_ids is None:
            platform_ids = ["douyin", "weibo", "zhihu",
                           "bilibili-hot-search", "toutiao", "baidu"]


        results = {}
        for i, pid in enumerate(platform_ids):
            topics = self.fetch_platform(pid)
            if topics:
                results[pid] = topics


            # 请求间隔（最后一个不需要等待）
            if i < len(platform_ids) - 1:
                jitter = request_interval + random.uniform(-0.1, 0.2)
                time.sleep(max(0.1, jitter))


        total = sum(len(v) for v in results.values())
        print(f"[TrendRadar] 批量抓取完成: {len(results)}/{len(platform_ids)} 平台，共 {total} 条")
        return results


    #  适配 self_media_agent 工作流格式
    def fetch_for_workflow(
        self,
        platform: str = "全部",
        platform_ids: List[str] = None,
    ) -> List[Dict]:
        """
        抓取热点并转换为工作流所需格式。


        Args:
            platform: 中文平台名筛选（"抖音"/"小红书"/"全部"）
            platform_ids: 直接指定平台 ID 列表


        Returns:
            [{"source": "抖音", "title": "...", "heat": 980000, "url": "..."}, ...]
        """
        # 平台名 → ID 映射
        name_to_ids = {
            "抖音": ["douyin"],
            "小红书": ["xiaohongshu"],
            "微博": ["weibo"],
            "知乎": ["zhihu"],
            "B站": ["bilibili-hot-search"],
            "今日头条": ["toutiao"],
            "百度": ["baidu"],
            "全部": ["douyin", "weibo", "zhihu", "bilibili-hot-search",
                    "toutiao", "baidu", "xiaohongshu"],
        }


        if platform_ids is None:
            platform_ids = name_to_ids.get(platform, ["douyin", "weibo", "zhihu"])


        all_topics = []
        for pid in platform_ids:
            topics = self.fetch_platform(pid)
            for t in topics:
                all_topics.append({
                    "source": t["platform"],
                    "title": t["title"],
                    "heat": t["heat"],
                    "url": t["url"],
                    "platform_id": t["platform_id"],
                    "rank": t["rank"],
                })


        # 按热度降序排列
        all_topics.sort(key=lambda x: x["heat"], reverse=True)
        return all_topics


    #  辅助方法
    @staticmethod
    def _estimate_heat(rank: int, total: int) -> int:
        """根据排名估算热度值（排名越前热度越高）"""
        base = 1000000
        ratio = 1.0 - (rank - 1) / max(total, 1)
        return int(base * ratio)


    @staticmethod
    def get_available_platforms() -> Dict[str, str]:
        """获取所有可用平台"""
        return dict(PLATFORM_IDS)




#  便捷函数（直接调用，无需实例化）
_default_client = None




def _get_client() -> TrendRadarClient:
    """获取默认客户端（懒加载单例）"""
    global _default_client
    if _default_client is None:
        _default_client = TrendRadarClient()
    return _default_client




def fetch_hot_topics(platform: str = "全部") -> List[Dict]:
    """
    便捷函数：抓取热点并返回工作流格式。


    使用方式:
        from tools.trend_radar_client import fetch_hot_topics
        topics = fetch_hot_topics("抖音")
        # [{"source": "抖音", "title": "...", "heat": 980000, ...}, ...]
    """
    return _get_client().fetch_for_workflow(platform=platform)




def fetch_platform_hot(platform_id: str) -> List[Dict]:
    """
    便捷函数：抓取单个平台热点。


    使用方式:
        from tools.trend_radar_client import fetch_platform_hot
        topics = fetch_platform_hot("douyin")
    """
    return _get_client().fetch_platform(platform_id)