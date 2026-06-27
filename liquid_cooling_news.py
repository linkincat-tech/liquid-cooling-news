#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
液冷数据中心前沿动态追踪脚本 v3.0 (RSS + 本地多语种过滤 + PushDeer推送)
Liquid Cooling Data Center News Collector with Multi-language Filtering & Push Notification
"""

import os
import re
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional
from dataclasses import dataclass
from urllib.parse import urlparse, quote

import feedparser
import charset_normalizer
import requests
from openai import OpenAI


@dataclass
class NewsArticle:
    """新闻文章数据结构"""
    source_name: str
    source_lang: str  # 'en', 'ja', 'zh'
    title: str
    link: str
    summary: str
    short_link: Optional[str] = None
    published: Optional[str] = None


def send_push(content: str, title: str = "❄️ 液冷数据中心前沿动态简报") -> bool:
    """
    使用 PushDeer API 发送推送通知到手机

    Args:
        content: 要推送的内容
        title: 推送标题

    Returns:
        bool: 推送是否成功
    """
    push_key = os.getenv("PUSHDEER_KEY")
    if not push_key:
        print("\n⚠️ 未设置 PUSHDEER_KEY 环境变量，跳过推送")
        return False

    # 自动截断保护，避免 PushDeer 服务端强制截断导致乱码
    max_length = 3000
    suffix = "\n\n...更多内容请查看日志"
    if len(content) > max_length:
        content = content[: max_length - len(suffix)] + suffix

    api_url = "https://api2.pushdeer.com/message/push"

    payload = {
        "pushkey": push_key,
        "text": title,
        "desp": content,
        "type": "markdown"
    }

    try:
        print("\n📱 正在发送 PushDeer 推送...", end=" ")
        response = requests.post(api_url, data=payload, timeout=30)
        response.raise_for_status()

        result = response.json()
        if result.get("code") == 0:
            print("✅ 推送成功！")
            return True
        else:
            print(f"❌ 推送失败: {result.get('error', '未知错误')}")
            return False

    except requests.exceptions.RequestException as e:
        print(f"❌ 推送请求失败: {str(e)[:50]}")
        return False
    except Exception as e:
        print(f"❌ 推送异常: {str(e)[:50]}")
        return False


def _split_push_content_by_articles(content: str, chunk_size: int = 10) -> List[str]:
    lines = [line for line in content.splitlines()]
    header = []
    articles = []
    current = []
    started = False

    for line in lines:
        if re.match(r'^\s*-\s+', line):
            if current:
                articles.append('\n'.join(current).rstrip())
            current = [line]
            started = True
        elif started:
            current.append(line)
        else:
            header.append(line)

    if current:
        articles.append('\n'.join(current).rstrip())

    if not articles:
        return [content]

    chunks = []
    for i in range(0, len(articles), chunk_size):
        chunk_articles = articles[i:i + chunk_size]
        chunk_text = '\n'.join(header + [''] + chunk_articles).strip()
        chunks.append(chunk_text)

    return chunks


def _ensure_chunk_within_limit(chunk: str, max_chars: int = 2000) -> str:
    if len(chunk) <= max_chars:
        return chunk

    lines = chunk.splitlines()
    article_indices = [i for i, line in enumerate(lines) if re.match(r'^\s*-\s+', line)]
    removed = 0
    while len("\n".join(lines)) > max_chars and removed < 2 and article_indices:
        start = article_indices.pop()
        lines = lines[:start]
        removed += 1

    result = "\n".join(lines).rstrip()
    if len(result) > max_chars:
        print("    ⚠️ 内容仍超限，最后截断字符以保证发送。")
        result = result[:max_chars]

    if removed > 0:
        print(f"    ⚠️ 已从该批次截断最后 {removed} 条新闻以保持 {max_chars} 字以内。")
    return result


def send_push_batches(content: str) -> bool:
    """将内容按每 10 条新闻分批发送到 PushDeer。"""
    chunks = _split_push_content_by_articles(content, chunk_size=10)
    total = len(chunks)
    success = True

    for idx, chunk in enumerate(chunks, start=1):
        title = f"❄️ 液冷早报 - 第 {idx}/{total} 部分"
        chunk_content = f"{chunk}\n\n(第 {idx}/{total} 部分)"
        chunk_content = _ensure_chunk_within_limit(chunk_content, max_chars=2000)
        print(f"\n📱 发送推送第 {idx}/{total} 部分...")
        if not send_push(chunk_content, title=title):
            success = False
        if idx < total:
            time.sleep(5)

    return success


class LiquidCoolingNewsCollector:
    """液冷数据中心新闻收集器"""

    # Chrome User-Agent 伪装
    CHROME_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

    # RSS 源配置 - Google News 聚合
    RSS_FEEDS = {
        # Google News 聚合 - 英语源
        "Google News (EN) - NVIDIA GB300": {
            "url": 'https://news.google.com/rss/search?q="NVIDIA+GB300"+when:1d&hl=en-US&gl=US&ceid=US:en',
            "lang": "en",
            "weight": "high"
        },
        "Google News (EN) - Liquid Cooling": {
            "url": 'https://news.google.com/rss/search?q="Liquid+Cooling"+AND+"Data+Center"+when:1d&hl=en-US&gl=US&ceid=US:en',
            "lang": "en",
            "weight": "high"
        },
        "Google News (EN) - Cold Plate AI": {
            "url": 'https://news.google.com/rss/search?q="Cold+Plate"+AND+AI+when:1d&hl=en-US&gl=US&ceid=US:en',
            "lang": "en",
            "weight": "high"
        },
        "Google News (EN) - CDU Data Center": {
            "url": 'https://news.google.com/rss/search?q=CDU+AND+"Data+Center"+when:1d&hl=en-US&gl=US&ceid=US:en',
            "lang": "en",
            "weight": "high"
        },
        "Google News (EN) - UQD Quick Disconnect": {
            "url": 'https://news.google.com/rss/search?q=UQD+OR+"Universal+Quick+Disconnect"+when:1d&hl=en-US&gl=US&ceid=US:en',
            "lang": "en",
            "weight": "high"
        },
        "Google News (EN) - MQD Mini Quick Disconnect": {
            "url": 'https://news.google.com/rss/search?q=MQD+OR+"Mini+Quick+Disconnect"+when:1d&hl=en-US&gl=US&ceid=US:en',
            "lang": "en",
            "weight": "high"
        },
        "Google News (EN) - CoolIT Boyd NVIDIA": {
            "url": 'https://news.google.com/rss/search?q=(CoolIT+OR+Boyd)+AND+NVIDIA+when:1d&hl=en-US&gl=US&ceid=US:en',
            "lang": "en",
            "weight": "high"
        },
        "Google News (EN) - Wiwynn Quanta GB300": {
            "url": 'https://news.google.com/rss/search?q=(Wiwynn+OR+Quanta)+AND+GB300+when:1d&hl=en-US&gl=US&ceid=US:en',
            "lang": "en",
            "weight": "high"
        },
        "Google News (EN) - Google TPU Liquid Cooling": {
            "url": 'https://news.google.com/rss/search?q="Google+TPU"+AND+"liquid+cooling"+when:1d&hl=en-US&gl=US&ceid=US:en',
            "lang": "en",
            "weight": "high"
        },
        "Google News (EN) - Japan AI Data Center": {
            "url": 'https://news.google.com/rss/search?q="Japan+AI+Data+Center"+when:1d&hl=en-US&gl=US&ceid=US:en',
            "lang": "en",
            "weight": "high"
        },
        "Google News (EN) - NVIDIA Rubin": {
            "url": 'https://news.google.com/rss/search?q="NVIDIA+Rubin"+when:1d&hl=en-US&gl=US&ceid=US:en',
            "lang": "en",
            "weight": "high"
        },
        # Google News 聚合 - 日语源
        "Google News (JA) - NVIDIA GB300": {
            "url": "https://news.google.com/rss/search?q=NVIDIA+GB300+when:1d&hl=ja&gl=JP&ceid=JP:ja",
            "lang": "ja",
            "weight": "high"
        },
        "Google News (JA) - Liquid Cooling DC": {
            "url": "https://news.google.com/rss/search?q=液冷+データセンター+when:1d&hl=ja&gl=JP&ceid=JP:ja",
            "lang": "ja",
            "weight": "high"
        },
        "Google News (JA) - Cold Plate AI": {
            "url": "https://news.google.com/rss/search?q=コールドプレート+AI+when:1d&hl=ja&gl=JP&ceid=JP:ja",
            "lang": "ja",
            "weight": "high"
        },
        "Google News (JA) - CDU Cooling": {
            "url": "https://news.google.com/rss/search?q=CDU+液冷+when:1d&hl=ja&gl=JP&ceid=JP:ja",
            "lang": "ja",
            "weight": "high"
        },
        "Google News (JA) - UQD Cooling": {
            "url": "https://news.google.com/rss/search?q=UQD+液冷+when:1d&hl=ja&gl=JP&ceid=JP:ja",
            "lang": "ja",
            "weight": "high"
        },
        "Google News (JA) - Liquid Cooling Cert": {
            "url": "https://news.google.com/rss/search?q=液冷+認証+when:1d&hl=ja&gl=JP&ceid=JP:ja",
            "lang": "ja",
            "weight": "high"
        },
        "Google News (JA) - CoolIT NVIDIA": {
            "url": "https://news.google.com/rss/search?q=CoolIT+NVIDIA+when:1d&hl=ja&gl=JP&ceid=JP:ja",
            "lang": "ja",
            "weight": "high"
        },
        "Google News (JA) - AI Server Cooling": {
            "url": "https://news.google.com/rss/search?q=AIサーバー+液冷+when:1d&hl=ja&gl=JP&ceid=JP:ja",
            "lang": "ja",
            "weight": "high"
        },
        "Google News (JA) - Google TPU Cooling": {
            "url": "https://news.google.com/rss/search?q=Google+TPU+液冷+when:1d&hl=ja&gl=JP&ceid=JP:ja",
            "lang": "ja",
            "weight": "high"
        },
        "Google News (JA) - AI Data Center Japan": {
            "url": "https://news.google.com/rss/search?q=AI+データセンター+日本+when:1d&hl=ja&gl=JP&ceid=JP:ja",
            "lang": "ja",
            "weight": "high"
        }
    }

    # 关键词配置（本地预过滤）
    KEYWORDS = {
        "en": [
            "nvidia gb300", "liquid cooling", "data center cooling", "cold plate",
            "cdu", "coolant distribution unit", "uqd", "universal quick disconnect",
            "mqd", "mini quick disconnect", "coolit", "boyd", "wiwynn", "quanta",
            "google tpu", "liquid cooled", "japan ai data center", "nvidia rubin",
            "immersion cooling", "direct liquid cooling", "dlc"
        ],
        "ja": [
            "nvidia gb300", "液冷", "データセンター", "コールドプレート",
            "cdu", "uqd", "coolit", "aiサーバー", "google tpu",
            "液冷認証", "ai データセンター 日本", "nvidia rubin",
            "浸漬冷却", "直接液体冷却"
        ],
        "zh": [
            "液冷", "数据中心", "冷板", "cdu", "快换接头",
            "nvidia gb300", "液冷认证", "ai服务器", "谷歌tpu"
        ]
    }

    def __init__(self):
        """初始化收集器"""
        self.api_key = os.getenv("KIMI_API_KEY")
        if not self.api_key:
            raise ValueError("请设置环境变量 KIMI_API_KEY")

        # 初始化 Kimi API 客户端 (OpenAI 兼容接口)
        self.client = OpenAI(
            api_key=self.api_key,
            base_url="https://api.moonshot.cn/v1"
        )

        self.filtered_articles: List[NewsArticle] = []

    def _check_keywords(self, text: str, lang: str) -> bool:
        """
        检查文本是否包含关键词
        在调用 API 之前，先在本地通过关键词匹配（不区分大小写）进行初步筛选。
        """
        if not text:
            return False

        text_lower = text.lower()
        keywords = self.KEYWORDS.get(lang, self.KEYWORDS["en"])

        for keyword in keywords:
            if keyword.lower() in text_lower:
                return True

        return False

    def _extract_text_from_entry(self, entry) -> tuple:
        """从 RSS 条目中提取标题和摘要"""
        title = getattr(entry, 'title', '') or ''

        # 尝试多种方式获取摘要
        summary = ''
        if hasattr(entry, 'summary'):
            summary = entry.summary
        elif hasattr(entry, 'description'):
            summary = entry.description
        elif hasattr(entry, 'content'):
            # Atom 格式
            content = entry.content
            if isinstance(content, list) and len(content) > 0:
                summary = content[0].get('value', '')

        # 清理 HTML 标签
        summary = self._clean_html(summary)

        return title, summary

    @staticmethod
    def _clean_html(text: str) -> str:
        """清理 HTML 标签"""
        if not text:
            return ""
        # 移除 HTML 标签
        clean = re.sub(r'<[^>]+>', '', text)
        # 移除多余空白
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean

    def _parse_published_date(self, published_str: Optional[str]) -> Optional[datetime]:
        """解析发布时间字符串为 datetime 对象"""
        if not published_str:
            return None

        # 尝试多种 RSS 日期格式
        date_formats = [
            '%a, %d %b %Y %H:%M:%S %z',      # RFC 822: Mon, 01 Jan 2024 12:00:00 +0000
            '%a, %d %b %Y %H:%M:%S %Z',      # RFC 822 with named timezone
            '%Y-%m-%dT%H:%M:%S%z',           # ISO 8601: 2024-01-01T12:00:00+00:00
            '%Y-%m-%dT%H:%M:%SZ',            # ISO 8601 UTC: 2024-01-01T12:00:00Z
            '%Y-%m-%d %H:%M:%S',             # Simple format: 2024-01-01 12:00:00
        ]

        for fmt in date_formats:
            try:
                parsed = datetime.strptime(published_str, fmt)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
            except ValueError:
                continue

        # 尝试 feedparser 提供的 parsed tuple
        return None

    def _is_within_24_hours(self, published_str: Optional[str]) -> bool:
        """检查文章发布时间是否在 25 小时内，增强 Google News 延迟容错。"""
        pub_date = self._parse_published_date(published_str)
        if not pub_date:
            # 如果无法解析日期，默认保留 (宁缺毋滥原则下选择保守策略)
            return True

        now = datetime.now(timezone.utc)

        # 确保 pub_date 有时区信息并统一为 UTC
        if pub_date.tzinfo is None:
            pub_date = pub_date.replace(tzinfo=timezone.utc)
        else:
            pub_date = pub_date.astimezone(timezone.utc)

        diff = now - pub_date
        return diff.total_seconds() <= 25 * 3600  # 25
        diff = now - pub_date
        return diff.total_seconds() <= 25 * 3600  # 25 hours in seconds

    @staticmethod
    def _format_api_error(e: Exception) -> str:
        """格式化 API 异常，尽量提取状态码和返回内容。"""
        status_code = getattr(e, 'status_code', None) or getattr(e, 'http_status', None)
        message = str(e)
        details = []

        if status_code is not None:
            details.append(f"status={status_code}")

        response = getattr(e, 'response', None)
        if response is not None:
            try:
                if hasattr(response, 'text'):
                    details.append(f"response={response.text}")
                elif hasattr(response, 'json'):
                    details.append(f"response={response.json()}")
            except Exception:
                details.append(repr(response))

        if details:
            return f"{message} ({'; '.join(details)})"
        return message

    @staticmethod
    def _detect_and_decode(content: bytes) -> str:
        """检测编码并解码内容，防止乱码"""
        try:
            result = charset_normalizer.detect(content)
            encoding = result.get('encoding', 'utf-8')
            return content.decode(encoding, errors='replace')
        except Exception:
            return content.decode('utf-8', errors='replace')

    def _shorten_url(self, url: str) -> str:
        """使用 TinyURL 生成短链接，失败时返回原链接。"""
        if not url or len(url) < 80:
            return url
        try:
            response = requests.get(
                "https://tinyurl.com/api-create.php",
                params={"url": url},
                timeout=10
            )
            if response.status_code == 200:
                short_url = response.text.strip()
                if short_url.startswith("http"):
                    return short_url
        except Exception as e:
            print(f"    ⚠️ 链接缩短失败: {str(e)[:80]}")
        return url

    def _source_short_name(self, source_name: str) -> str:
        """返回来源名称的精简版本。"""
        mapping = {
            "Google News (EN) - NVIDIA GB300": "❄️GB300",
            "Google News (EN) - Liquid Cooling": "❄️Liquid",
            "Google News (EN) - Cold Plate AI": "❄️ColdPlate",
            "Google News (EN) - CDU Data Center": "❄️CDU",
            "Google News (EN) - UQD Quick Disconnect": "❄️UQD",
            "Google News (EN) - MQD Mini Quick Disconnect": "❄️MQD",
            "Google News (EN) - CoolIT Boyd NVIDIA": "❄️CoolIT",
            "Google News (EN) - Wiwynn Quanta GB300": "❄️OEM",
            "Google News (EN) - Google TPU Liquid Cooling": "❄️TPU",
            "Google News (EN) - Japan AI Data Center": "❄️Japan",
            "Google News (EN) - NVIDIA Rubin": "❄️Rubin",
            "Google News (JA) - NVIDIA GB300": "❄️JP-GB300",
            "Google News (JA) - Liquid Cooling DC": "❄️JP-Liquid",
            "Google News (JA) - Cold Plate AI": "❄️JP-ColdPlate",
            "Google News (JA) - CDU Cooling": "❄️JP-CDU",
            "Google News (JA) - UQD Cooling": "❄️JP-UQD",
            "Google News (JA) - Liquid Cooling Cert": "❄️JP-Cert",
            "Google News (JA) - CoolIT NVIDIA": "❄️JP-CoolIT",
            "Google News (JA) - AI Server Cooling": "❄️JP-AIServer",
            "Google News (JA) - Google TPU Cooling": "❄️JP-TPU",
            "Google News (JA) - AI Data Center Japan": "❄️JP-DC"
        }
        return mapping.get(source_name, source_name)

    def _trim_chunk_to_limit(self, chunk: str, max_chars: int = 2000) -> str:
        """如果 chunk 超过字符限制，优先删除最后两条新闻。"""
        if len(chunk) <= max_chars:
            return chunk

        lines = chunk.splitlines()
        article_indices = [i for i, line in enumerate(lines) if re.match(r'^\s*-\s*\[', line)]
        removed = 0
        while len(chunk) > max_chars and removed < 2 and article_indices:
            start = article_indices.pop()
            lines = lines[:start]
            removed += 1
            chunk = "\n".join(lines).rstrip()

        if len(chunk) > max_chars:
            print("    ⚠️ 内容仍超限，最后截断字符以保证发送。")
            chunk = chunk[:max_chars]

        if removed > 0:
            print(f"    ⚠️ 已从该批次截断最后 {removed} 条新闻以保持 {max_chars} 字以内。")
        return chunk

    def fetch_feed(self, source_name: str, feed_config: Dict) -> List[NewsArticle]:
        """获取单个 RSS 源的文章"""
        articles = []
        original_url = feed_config["url"]
        lang = feed_config["lang"]

        # 对 URL 进行安全编码，确保日语或其他特殊字符在 Google News 查询中被正确识别
        if any(ord(ch) > 127 for ch in original_url):
            url = quote(original_url, safe=':/?&=+')
        else:
            url = original_url

        print(f"  📡 正在获取: {source_name}...", end=" ")

        try:
            # 使用 requests 获取内容 + 编码检测
            headers = {'User-Agent': self.CHROME_UA}
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()

            # 编码检测与解码
            content = self._detect_and_decode(response.content)

            # 使用 feedparser 解析
            feed = feedparser.parse(content)

            # 备用方式：如果 requests 失败，直接使用 feedparser
            if not feed.entries:
                feed = feedparser.parse(
                    url,
                    request_headers={'User-Agent': self.CHROME_UA}
                )

            total_entries = len(feed.entries)
            if feed.bozo and hasattr(feed, 'bozo_exception'):
                print(f"⚠️ (警告: {str(feed.bozo_exception)[:50]}...)")
            print(f"✅ 获取 {total_entries} 条，开始过滤...")

            filtered_count = 0
            for entry in feed.entries:
                title, summary = self._extract_text_from_entry(entry)

                # 强制 25 小时时效检查
                published = getattr(entry, 'published', None)
                if not self._is_within_24_hours(published):
                    continue

                # 本地关键词过滤
                combined_text = f"{title} {summary}"
                if not self._check_keywords(combined_text, lang):
                    continue

                article = NewsArticle(
                    source_name=source_name,
                    source_lang=lang,
                    title=title,
                    link=getattr(entry, 'link', ''),
                    summary=summary[:500],  # 限制摘要长度
                    published=published
                )
                articles.append(article)
                filtered_count += 1

            print(f"    📝 {source_name} 初筛: {total_entries} 条 -> 过滤后 {filtered_count} 条")

        except Exception as e:
            print(f"❌ 错误: {str(e)[:50]}")

        return articles

    def fetch_all_feeds(self) -> List[NewsArticle]:
        """获取所有 RSS 源的文章"""
        print("=" * 60)
        print("📰 开始获取 RSS 源...")
        print("=" * 60)

        all_articles = []
        for source_name, config in self.RSS_FEEDS.items():
            articles = self.fetch_feed(source_name, config)
            all_articles.extend(articles)
            time.sleep(0.5)  # 礼貌性延迟

        print(f"\n🎯 25小时容错时效检查+关键词过滤后: {len(all_articles)} 条相关文章")
        return all_articles

    def _keyword_score(self, article: NewsArticle) -> int:
        """简单计算关键词匹配度。"""
        text = f"{article.title} {article.summary}".lower()
        keywords = self.KEYWORDS.get(article.source_lang, self.KEYWORDS["en"])
        return sum(1 for keyword in keywords if keyword.lower() in text)

    def _should_skip_api(self, article: NewsArticle) -> bool:
        """如果已有足够摘要或者关键词匹配度不高，则跳过 API 调用。"""
        summary = (article.summary or "").strip()
        if not summary:
            return False
        if len(summary) >= 120:
            return True
        keyword_count = self._keyword_score(article)
        if len(summary) >= 80 and keyword_count <= 1:
            return True
        return False

    @staticmethod
    def _truncate_summary(text: str, max_chars: int = 120) -> str:
        text = (text or "").strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rstrip() + "..."

    def _build_batch_prompt(self, articles: List[NewsArticle]) -> str:
        lang = articles[0].source_lang
        if lang == "en":
            base = (
                "You are a professional tech news summarizer. Provide concise, professional summaries in English only.\n\n"
                "Please summarize the following liquid cooling data center news. For each item, return a 50-word or shorter summary in the original language. "
                "Use numbered labels [1] [2] [3] etc., keep only core points, and do not include extra commentary.\n\n"
            )
        elif lang == "ja":
            base = (
                "あなたは技術ニュースの要約専門家です。日本語でのみ回答してください。\n\n"
                "以下の液冷データセンター関連ニュースを要約してください。各項目ごとに50字以内の要約を作成し、[1][2][3]の番号付きで返してください。\n\n"
            )
        else:
            base = (
                "你是科技新闻摘要专家。只用中文回答。\n\n"
                "请总结以下液冷数据中心相关新闻。为每条新闻提供50字以内的摘要，并用[1][2][3]编号返回。\n\n"
            )

        prompt_lines = [base, "News list:\n"]
        for idx, article in enumerate(articles, start=1):
            content = article.summary.strip() if len((article.summary or "").strip()) >= 20 else "N/A"
            prompt_lines.append(f"{idx}. Title: {article.title}\nContent: {content}\n\n")

        prompt_lines.append("Return only numbered summaries in the format:\n[1] summary\n[2] summary\n...")
        return "".join(prompt_lines)

    def _call_kimi_with_backoff(self, prompt: str) -> str:
        delays = [5, 10]
        for attempt in range(3):
            try:
                response = self.client.chat.completions.create(
                    model="moonshot-v1-8k",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=300,
                    temperature=0.3
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                error_text = self._format_api_error(e)
                status_code = getattr(e, 'status_code', None) or getattr(e, 'http_status', None)
                is_rate_limit = status_code == 429 or '429' in error_text
                print(f"    ⚠️ Kimi API 调用失败 (attempt {attempt + 1}/3): {error_text}")
                if is_rate_limit and attempt < len(delays):
                    wait = delays[attempt]
                    print(f"      429 限流，{wait} 秒后重试...")
                    time.sleep(wait)
                    continue
                raise

    def _parse_batch_summaries(self, text: str, count: int) -> Dict[int, str]:
        summaries: Dict[int, str] = {}
        parts = re.split(r'\[\s*(\d+)\s*\]', text)
        if len(parts) >= 3:
            for i in range(1, len(parts), 2):
                idx = int(parts[i])
                content = parts[i + 1].strip()
                if content:
                    summaries[idx] = content.splitlines()[0].strip()
        if len(summaries) < count:
            for line in text.splitlines():
                match = re.match(r'^\[\s*(\d+)\s*\](.*)$', line)
                if match:
                    summaries[int(match.group(1))] = match.group(2).strip()
        return summaries

    def batch_summarize_articles(self, articles: List[NewsArticle]) -> Dict[int, str]:
        if not articles:
            return {}

        prompt = self._build_batch_prompt(articles)
        response_text = self._call_kimi_with_backoff(prompt)
        raw_summaries = self._parse_batch_summaries(response_text, len(articles))
        results: Dict[int, str] = {}
        for idx, article in enumerate(articles, start=1):
            results[idx] = raw_summaries.get(idx, self._truncate_summary(article.summary))
        return results
    def _get_source_weight(self, source_name: str) -> int:
        """获取来源权重值，用于排序。数值越小优先级越高。"""
        config = self.RSS_FEEDS.get(source_name, {})
        weight = config.get("weight", "low")
        return 0 if weight == "high" else 1

    def generate_briefing(self, articles: List[NewsArticle]) -> str:
        """生成最终简报"""
        print("\n" + "=" * 60)
        print("🤖 正在调用 Kimi API 生成摘要...")
        print("=" * 60)

        briefing_lines = []
        briefing_lines.append("❄️ 液冷数据中心前沿动态简报")
        briefing_lines.append(f"📅 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        briefing_lines.append("")

        # 价值排序：专业源 (IEEE, The Robot Report, TechCrunch) 权重更高，排在前面
        # Google News 聚合排在后面
        sorted_articles = sorted(articles, key=lambda a: self._get_source_weight(a.source_name))

        # 推送数量控制：英语源目标 15-20 条；日语源独立最多 20 条。
        # 宁缺毋滥原则：如果英语组不足 15 条，则该组有多少推多少。
        TARGET_MIN = 15
        TARGET_MAX = 20
        JAPANESE_SOURCES = [name for name in self.RSS_FEEDS.keys() if "(JA)" in name]

        english_articles = [a for a in sorted_articles if a.source_name not in JAPANESE_SOURCES]
        japanese_articles = [a for a in sorted_articles if a.source_name in JAPANESE_SOURCES]

        if len(english_articles) >= TARGET_MIN:
            selected_english = english_articles[:TARGET_MAX]
        else:
            selected_english = english_articles

        selected_japanese = japanese_articles[:20]
        selected_articles = selected_english + selected_japanese

        print(f"\n📊 文章分布统计:")
        source_counts: Dict[str, int] = {}
        for article in articles:
            source_counts[article.source_name] = source_counts.get(article.source_name, 0) + 1
        for name, count in sorted(source_counts.items(), key=lambda x: self._get_source_weight(x[0])):
            weight_label = "高权重" if self._get_source_weight(name) == 0 else "低权重"
            print(f"   - {name}: {count} 条 ({weight_label})")
        print(f"\n📋 英语/专业源选中: {len(selected_english)} 条 (目标范围: {TARGET_MIN}-{TARGET_MAX})")
        print(f"📋 日语源选中: {len(selected_japanese)} 条 (最多 20 条)")
        print(f"📋 最终合并简报: {len(selected_articles)} 条")

        if selected_articles:
            briefing_lines.append("🌐 精选液冷数据中心新闻")
            briefing_lines.append("-" * 40)

            local_summaries: Dict[int, str] = {}
            api_targets: List[tuple[int, NewsArticle]] = []
            for idx, article in enumerate(selected_articles, 1):
                if len(article.link) > 80 or "google news" in article.source_name.lower():
                    short_link = self._shorten_url(article.link)
                    if short_link != article.link:
                        print(f"  🔗 链接缩短: {article.link[:60]}... ({len(article.link)}) -> ({len(short_link)})")
                    article.short_link = short_link
                else:
                    article.short_link = article.link

                if self._should_skip_api(article):
                    local_summaries[idx] = self._truncate_summary(article.summary, max_chars=80)
                else:
                    api_targets.append((idx, article))

            api_summaries: Dict[int, str] = {}
            grouped: Dict[str, List[tuple[int, NewsArticle]]] = {}
            for idx, article in api_targets:
                grouped.setdefault(article.source_lang, []).append((idx, article))

            for lang, items in grouped.items():
                for batch_start in range(0, len(items), 5):
                    batch_items = items[batch_start:batch_start + 5]
                    batch_articles = [article for _, article in batch_items]
                    try:
                        batch_results = self.batch_summarize_articles(batch_articles)
                    except Exception as e:
                        print(f"    ⚠️ 批量摘要失败: {str(e)[:80]}")
                        batch_results = {i: self._truncate_summary(article.summary, max_chars=80)
                                         for i, article in enumerate(batch_articles, start=1)}

                    for local_idx, summary in batch_results.items():
                        api_summaries[batch_items[local_idx - 1][0]] = summary

            for i, article in enumerate(selected_articles, 1):
                print(f"  处理 [{i}/{len(selected_articles)}]: {article.title[:40]}...")
                ai_summary = local_summaries.get(i) or api_summaries.get(i) or self._truncate_summary(article.summary, max_chars=80)
                article_source = self._source_short_name(article.source_name)
                link = article.short_link or article.link
                briefing_lines.append(f"\n- {article_source} {article.title} [🔗]({link})")
                briefing_lines.append(f"  - {ai_summary}")
                time.sleep(0.3)

        if not articles:
            briefing_lines.append("\n⚠️ 今日暂无匹配关键词的液冷新闻。")

        briefing_lines.append("\n✅ 简报生成完毕")

        return "\n".join(briefing_lines)

    def run(self) -> Optional[str]:
        """
        运行完整流程

        Returns:
            Optional[str]: 生成的简报内容，如果没有文章则返回 None
        """
        try:
            # 1. 获取所有 RSS 文章
            articles = self.fetch_all_feeds()

            if not articles:
                print("\n⚠️ 未找到匹配关键词的文章，跳过 API 调用。")
                return None

            # 2. 生成 AI 简报
            briefing = self.generate_briefing(articles)

            # 3. 输出最终简报
            print("\n" + "=" * 60)
            print(briefing)
            print("=" * 60)

            return briefing

        except Exception as e:
            print(f"\n❌ 运行错误: {e}")
            raise


def main():
    """主函数"""
    print("""
    ╔══════════════════════════════════════════════════════════════╗
    ║           液冷数据中心前沿动态追踪脚本 v3.0                  ║
    ║   Liquid Cooling News Collector (RSS + AI + PushDeer)        ║
    ╚══════════════════════════════════════════════════════════════╝
    """)

    collector = LiquidCoolingNewsCollector()
    briefing = collector.run()

    # 发送推送到手机，分批处理避免截断
    if briefing:
        send_push_batches(briefing)


if __name__ == "__main__":
    main()
