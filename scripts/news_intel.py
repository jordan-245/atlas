#!/usr/bin/env python3
"""
Atlas Multi-Source News Intelligence Aggregator.

Fetches Iran conflict news from 4 sources in parallel, deduplicates by headline
similarity, and prioritises wire services (Reuters/AP) over aggregators.

Sources:
  1. Brave Search API — news + web + video endpoints (existing brave_news.js)
  2. GDELT API — free real-time global coverage, English-filtered
  3. Google News RSS — free, fast, no API key needed
  4. Live blog scraper — CNBC, CNN, Al Jazeera, BBC live update pages

Usage:
  python3 scripts/news_intel.py                      # full run, human-readable
  python3 scripts/news_intel.py --json                # JSON output
  python3 scripts/news_intel.py --hours 4             # custom recency window
  python3 scripts/news_intel.py --sources brave,gdelt # specific sources only
  python3 scripts/news_intel.py --query "ceasefire"   # custom query focus

Cost: 0 (GDELT, Google News, live blogs are free; Brave uses existing key)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup

# ── Constants ────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent

WIRE_SERVICES = {
    "reuters.com", "apnews.com", "ap.org",
    "afp.com", "france24.com",
}

TIER1_SOURCES = {
    *WIRE_SERVICES,
    "cnbc.com", "cnn.com", "bbc.com", "bbc.co.uk",
    "aljazeera.com", "nytimes.com", "washingtonpost.com",
    "wsj.com", "ft.com", "bloomberg.com", "theguardian.com",
}

# GDELT API
GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"

# Google News RSS
GNEWS_RSS = "https://news.google.com/rss/search"

# Live blog discovery queries (used to find current URLs)
LIVE_BLOG_DISCOVERY = [
    "iran live updates site:cnbc.com",
    "iran war live site:aljazeera.com",
    "iran live site:bbc.com/news",
    "iran live updates site:cnn.com",
]

# Search queries for GDELT and Google News
# Queries tuned per-source: GDELT needs broader phrases (full-text search),
# Google News works best with tight keyword combos (headline-weighted).
DEFAULT_QUERIES_GDELT = [
    "iran war",
    "iran strike military",
    "hormuz shipping",
    "oil price iran",
    "hezbollah attack",
    "houthi ship",
]

DEFAULT_QUERIES_GNEWS = [
    "iran war strikes military",
    "iran ceasefire negotiations diplomacy",
    "hormuz shipping tanker oil",
    "iran oil price crude",
    "hezbollah houthi red sea",
    "iran cyber defence",
]

CEASEFIRE_QUERIES_GDELT = [
    "iran ceasefire",
    "iran diplomacy negotiations",
    "iran military depleted",
    "war powers iran",
    "hormuz escort",
]

CEASEFIRE_QUERIES_GNEWS = [
    "iran ceasefire talks negotiations",
    "iran diplomacy mediation Oman Qatar",
    "trump iran deal negotiate",
    "iran supreme leader successor",
    "iran military capacity depleted",
    "war powers resolution iran",
    "iran regime collapse",
    "oil price hundred barrel",
    "hormuz escort tanker convoy",
    "war risk insurance gulf",
]

UA = "Mozilla/5.0 (compatible; AtlasBot/1.0)"
TIMEOUT = 15


# ═══════════════════════════════════════════════════════════════
# Source 1: Brave Search (delegates to existing brave_news.js)
# ═══════════════════════════════════════════════════════════════

def fetch_brave(hours: float) -> list[dict]:
    """Call existing brave_news.js and parse JSON output."""
    script = PROJECT_ROOT / "scripts" / "brave_news.js"
    if not script.exists():
        return []

    api_key = os.environ.get("BRAVE_API_KEY", "")
    if not api_key:
        return []

    try:
        result = subprocess.run(
            ["node", str(script), "--json", "--hours", str(hours)],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "BRAVE_API_KEY": api_key},
        )
        if result.returncode != 0:
            print(f"  [brave] ERROR: {result.stderr[:200]}", file=sys.stderr)
            return []

        data = json.loads(result.stdout)
        articles = []
        for r in data.get("recent", []) + data.get("older", []):
            articles.append(_normalize(
                title=r.get("title", ""),
                url=r.get("url", ""),
                description=r.get("description", ""),
                source_domain=r.get("source", ""),
                age_str=r.get("age", ""),
                page_age=r.get("page_age", ""),
                origin="brave",
                section=r.get("_section", ""),
            ))
        return articles
    except Exception as e:
        print(f"  [brave] EXCEPTION: {e}", file=sys.stderr)
        return []


# ═══════════════════════════════════════════════════════════════
# Source 2: GDELT API (free, real-time, global)
# ═══════════════════════════════════════════════════════════════

def fetch_gdelt(queries: list[str], hours: float) -> list[dict]:
    """Fetch articles from GDELT's DOC 2.0 API."""
    articles = []
    timespan = f"{max(1, int(hours))}h"

    for q in queries:
        try:
            params = {
                "query": f"{q} sourcelang:eng",
                "mode": "artlist",
                "maxrecords": "30",
                "format": "json",
                "timespan": timespan,
                "sort": "datedesc",
            }
            resp = requests.get(GDELT_API, params=params, timeout=TIMEOUT,
                                headers={"User-Agent": UA})
            if resp.status_code != 200:
                continue

            data = resp.json()
            for art in data.get("articles", []):
                domain = art.get("domain", "")
                seen = art.get("seendate", "")
                # Parse GDELT date: 20260305T023000Z
                pub_dt = None
                if seen:
                    try:
                        pub_dt = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(
                            tzinfo=timezone.utc
                        )
                    except ValueError:
                        pass

                articles.append(_normalize(
                    title=art.get("title", ""),
                    url=art.get("url", ""),
                    description="",  # GDELT artlist doesn't include snippets
                    source_domain=domain,
                    pub_datetime=pub_dt,
                    origin="gdelt",
                    language=art.get("language", "English"),
                    source_country=art.get("sourcecountry", ""),
                ))

            # Rate limit: GDELT free tier allows ~5 req/min, need ≥12s between calls
            time.sleep(12)

        except Exception as e:
            print(f"  [gdelt] ERROR on '{q}': {e}", file=sys.stderr)

    return articles


# ═══════════════════════════════════════════════════════════════
# Source 3: Google News RSS (free, no key, fast)
# ═══════════════════════════════════════════════════════════════

def fetch_google_news(queries: list[str]) -> list[dict]:
    """Fetch from Google News RSS feeds."""
    articles = []

    for q in queries:
        try:
            url = f"{GNEWS_RSS}?q={quote_plus(q)}&hl=en-US&gl=US&ceid=US:en"
            feed = feedparser.parse(url)

            for entry in feed.entries[:20]:
                # Google News wraps titles with source: "Title - Source Name"
                title = entry.get("title", "")
                source_name = ""
                if entry.get("source", {}).get("title"):
                    source_name = entry["source"]["title"]
                elif " - " in title:
                    parts = title.rsplit(" - ", 1)
                    if len(parts) == 2:
                        title, source_name = parts

                # Parse pub date
                pub_dt = None
                if entry.get("published_parsed"):
                    try:
                        pub_dt = datetime(*entry.published_parsed[:6],
                                          tzinfo=timezone.utc)
                    except (TypeError, ValueError):
                        pass

                link = entry.get("link", "")
                # Google News links are redirects — extract real URL if present
                desc = entry.get("summary", "")
                # Strip HTML from description
                if "<" in desc:
                    desc = BeautifulSoup(desc, "html.parser").get_text(separator=" ")

                domain = _extract_domain(link)
                if source_name:
                    domain = source_name.lower().replace(" ", "")

                articles.append(_normalize(
                    title=title.strip(),
                    url=link,
                    description=desc[:300],
                    source_domain=domain,
                    pub_datetime=pub_dt,
                    origin="google_news",
                ))

            time.sleep(0.3)

        except Exception as e:
            print(f"  [gnews] ERROR on '{q}': {e}", file=sys.stderr)

    return articles


# ═══════════════════════════════════════════════════════════════
# Source 4: Live Blog Scraper
# ═══════════════════════════════════════════════════════════════

def fetch_live_blogs(hours: float) -> list[dict]:
    """Discover and scrape live blog pages from major news outlets."""
    articles = []

    # Step 1: Discover current live blog URLs via Brave Search
    api_key = os.environ.get("BRAVE_API_KEY", "")
    live_urls = []

    if api_key:
        for query in LIVE_BLOG_DISCOVERY:
            try:
                resp = requests.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": "3", "freshness": "pd"},
                    headers={
                        "X-Subscription-Token": api_key,
                        "Accept": "application/json",
                    },
                    timeout=TIMEOUT,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for r in data.get("web", {}).get("results", []):
                        url = r.get("url", "")
                        title = r.get("title", "")
                        if any(kw in title.lower() for kw in
                               ["live", "update", "latest"]):
                            live_urls.append(url)
                time.sleep(1.1)  # Brave rate limit
            except Exception as e:
                print(f"  [liveblog] Discovery error: {e}", file=sys.stderr)

    # Deduplicate URLs
    live_urls = list(dict.fromkeys(live_urls))[:6]  # max 6 pages

    if not live_urls:
        return articles

    # Step 2: Fetch and parse each live blog page
    for url in live_urls:
        try:
            resp = requests.get(url, timeout=TIMEOUT, headers={
                "User-Agent": UA,
                "Accept": "text/html",
            })
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            domain = _extract_domain(url)

            # Parse live blog entries using common patterns
            entries = _parse_liveblog_entries(soup, domain, url)
            articles.extend(entries)

        except Exception as e:
            print(f"  [liveblog] Scrape error for {url}: {e}", file=sys.stderr)

    return articles


def _parse_liveblog_entries(
    soup: BeautifulSoup, domain: str, page_url: str
) -> list[dict]:
    """Extract individual entries from a live blog page.

    Handles common live blog HTML structures across CNBC, CNN, BBC, Al Jazeera.
    """
    entries = []

    # Strategy 1: Look for common liveblog article/entry containers
    # These selectors cover most major news sites' live blog formats
    selectors = [
        # CNBC live blog entries
        "div[class*='LiveBlogEntry']",
        "article[class*='LiveBlog']",
        "div[class*='live-blog-entry']",
        "div[class*='liveblog__entry']",
        # CNN live blog
        "article[class*='live-story']",
        "div[class*='live-story__content']",
        "article[data-type='liveblog']",
        # BBC live blog
        "div[class*='lx-stream__post']",
        "article[class*='lx-stream-post']",
        "li[class*='lx-stream__post']",
        # Al Jazeera live blog
        "div[class*='wysiwyg']",
        "article[class*='liveblog-entry']",
        "div[class*='article-p-wrapper']",
        # Generic
        "div[class*='live-update']",
        "div[class*='update-entry']",
        "article[class*='post']",
    ]

    found_entries = []
    for sel in selectors:
        found = soup.select(sel)
        if len(found) >= 3:  # need at least 3 to be a live blog
            found_entries = found
            break

    if not found_entries:
        # Fallback: look for time-stamped paragraphs
        # Many live blogs use <time> or <span> with timestamps
        time_elements = soup.find_all("time")
        if len(time_elements) >= 3:
            for t in time_elements[:30]:
                parent = t.find_parent(["article", "div", "li", "section"])
                if parent and parent not in found_entries:
                    found_entries.append(parent)

    # Extract text from each entry
    for entry in found_entries[:30]:  # max 30 entries per page
        # Try to find a headline/title within the entry
        headline_el = entry.find(["h2", "h3", "h4", "strong"])
        headline = headline_el.get_text(strip=True) if headline_el else ""

        # Get the full text
        text = entry.get_text(separator=" ", strip=True)
        if len(text) < 30:
            continue  # skip empty/trivial entries

        # Try to find a timestamp
        time_el = entry.find("time")
        pub_dt = None
        if time_el:
            dt_str = time_el.get("datetime", "") or time_el.get_text(strip=True)
            pub_dt = _parse_datetime_flexible(dt_str)

        # Use headline or first ~100 chars as title
        if not headline:
            headline = text[:120].strip()
            if len(text) > 120:
                headline = headline.rsplit(" ", 1)[0] + "..."

        # Description is the rest
        desc = text[:500] if text != headline else ""

        entries.append(_normalize(
            title=headline,
            url=page_url,
            description=desc,
            source_domain=domain,
            pub_datetime=pub_dt,
            origin="liveblog",
        ))

    return entries


# ═══════════════════════════════════════════════════════════════
# Normalization + Dedup + Ranking
# ═══════════════════════════════════════════════════════════════

def _normalize(
    *,
    title: str,
    url: str,
    description: str = "",
    source_domain: str = "",
    age_str: str = "",
    page_age: str = "",
    pub_datetime: datetime | None = None,
    origin: str = "",
    section: str = "",
    language: str = "English",
    source_country: str = "",
) -> dict:
    """Normalize an article from any source into a common format."""
    domain = source_domain.lower().replace("www.", "")

    # Calculate age in minutes
    age_minutes = None
    if pub_datetime:
        now = datetime.now(timezone.utc)
        delta = now - pub_datetime
        age_minutes = max(0, delta.total_seconds() / 60)
    elif page_age:
        age_minutes = _parse_iso_age(page_age)
    elif age_str:
        age_minutes = _parse_age_string(age_str)

    # Determine source tier
    is_wire = any(ws in domain for ws in WIRE_SERVICES)
    is_tier1 = any(t1 in domain for t1 in TIER1_SOURCES)
    tier = 1 if is_wire else (2 if is_tier1 else 3)

    return {
        "title": title.strip(),
        "url": url.strip(),
        "description": description.strip(),
        "source_domain": domain,
        "age_minutes": age_minutes,
        "pub_datetime": pub_datetime.isoformat() if pub_datetime else None,
        "origin": origin,
        "section": section,
        "tier": tier,
        "is_wire": is_wire,
        "language": language,
        "source_country": source_country,
    }


def dedup_articles(articles: list[dict], threshold: float = 0.65) -> list[dict]:
    """Remove duplicate articles using fuzzy headline matching.

    When duplicates are found, keep the one from the highest-tier source
    (wire > tier1 > other) to prioritize authoritative sources.
    """
    if not articles:
        return []

    # Sort by tier (lower = better) so we process best sources first
    articles_sorted = sorted(articles, key=lambda a: (a.get("tier", 3), a.get("age_minutes") or 999999))

    kept = []
    kept_titles = []  # normalized titles for comparison

    for art in articles_sorted:
        title = _clean_title(art["title"])
        if not title or len(title) < 15:
            continue

        # Check against all kept articles
        is_dup = False
        for kt in kept_titles:
            similarity = SequenceMatcher(None, title, kt).ratio()
            if similarity >= threshold:
                is_dup = True
                break

        if not is_dup:
            kept.append(art)
            kept_titles.append(title)

    return kept


def _clean_title(title: str) -> str:
    """Normalize title for comparison."""
    t = title.lower().strip()
    # Remove common suffixes like "- Reuters", "| CNN", etc.
    t = re.sub(r"\s*[-|–—]\s*[a-z\s.]+$", "", t)
    # Remove punctuation
    t = re.sub(r"[^\w\s]", " ", t)
    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()
    return t


def rank_articles(articles: list[dict]) -> list[dict]:
    """Sort articles by: recency first, then source tier."""
    def sort_key(a):
        age = a.get("age_minutes")
        if age is None:
            age = 999999
        tier = a.get("tier", 3)
        return (age, tier)

    return sorted(articles, key=sort_key)


def bucket_by_recency(
    articles: list[dict], recent_hours: float
) -> tuple[list[dict], list[dict]]:
    """Split articles into recent (within window) and older."""
    recent_mins = recent_hours * 60
    recent = []
    older = []

    for a in articles:
        age = a.get("age_minutes")
        if age is not None and age <= recent_mins:
            recent.append(a)
        else:
            older.append(a)

    return rank_articles(recent), rank_articles(older)


# ═══════════════════════════════════════════════════════════════
# Utility functions
# ═══════════════════════════════════════════════════════════════

def _extract_domain(url: str) -> str:
    """Extract domain from URL."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _parse_age_string(age: str) -> float | None:
    """Parse age strings like '2 hours ago', '45 minutes ago'."""
    if not age:
        return None
    m = re.match(r"(\d+)\s*(minute|hour|day|week|month)", age, re.I)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    multipliers = {
        "minute": 1, "hour": 60, "day": 1440,
        "week": 10080, "month": 43200,
    }
    return n * multipliers.get(unit, 999999)


def _parse_iso_age(iso_str: str) -> float | None:
    """Calculate age in minutes from an ISO datetime string."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return max(0, (now - dt).total_seconds() / 60)
    except (ValueError, TypeError):
        return None


def _parse_datetime_flexible(dt_str: str) -> datetime | None:
    """Try multiple datetime formats."""
    if not dt_str:
        return None

    formats = [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S",
        "%B %d, %Y %I:%M %p",
        "%b %d, %Y %I:%M %p",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(dt_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue

    # Try ISO format as last resort
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _format_age(minutes: float | None) -> str:
    """Format age in human-readable form."""
    if minutes is None:
        return "???"
    if minutes < 60:
        return f"{int(minutes)}m ago"
    if minutes < 1440:
        return f"{minutes / 60:.1f}h ago"
    return f"{minutes / 1440:.1f}d ago"


# ═══════════════════════════════════════════════════════════════
# Main orchestrator
# ═══════════════════════════════════════════════════════════════

def fetch_all(
    hours: float = 4.0,
    sources: list[str] | None = None,
    query_focus: str | None = None,
) -> dict:
    """Fetch from all sources in parallel, dedup, rank, and return."""

    enabled = sources or ["brave", "gdelt", "google_news", "liveblog"]

    # Select queries per source — GDELT needs broader phrases, Google News uses tighter combos
    if query_focus == "ceasefire":
        gdelt_queries = CEASEFIRE_QUERIES_GDELT
        gnews_queries = CEASEFIRE_QUERIES_GNEWS
    elif query_focus:
        gdelt_queries = [query_focus]
        gnews_queries = [query_focus]
    else:
        gdelt_queries = DEFAULT_QUERIES_GDELT
        gnews_queries = DEFAULT_QUERIES_GNEWS

    all_articles = []
    source_stats = {}
    errors = []

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {}

        if "brave" in enabled:
            futures[pool.submit(fetch_brave, hours)] = "brave"
        if "gdelt" in enabled:
            futures[pool.submit(fetch_gdelt, gdelt_queries, hours)] = "gdelt"
        if "google_news" in enabled:
            futures[pool.submit(fetch_google_news, gnews_queries)] = "google_news"
        if "liveblog" in enabled:
            futures[pool.submit(fetch_live_blogs, hours)] = "liveblog"

        for future in as_completed(futures, timeout=180):
            source_name = futures[future]
            try:
                results = future.result()
                source_stats[source_name] = len(results)
                all_articles.extend(results)
            except Exception as e:
                errors.append({"source": source_name, "error": str(e)})
                source_stats[source_name] = 0

    # Dedup across all sources
    deduped = dedup_articles(all_articles)

    # Bucket by recency
    recent, older = bucket_by_recency(deduped, hours)

    # Source diversity stats
    domains = {}
    for a in deduped:
        d = a.get("source_domain", "unknown")
        domains[d] = domains.get(d, 0) + 1

    top_sources = sorted(domains.items(), key=lambda x: -x[1])[:15]
    wire_count = sum(1 for a in deduped if a.get("is_wire"))

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "recent_window_hours": hours,
        "total_raw": len(all_articles),
        "total_deduped": len(deduped),
        "recent_count": len(recent),
        "older_count": len(older),
        "wire_count": wire_count,
        "source_stats": source_stats,
        "top_sources": top_sources,
        "unique_domains": len(domains),
        "recent": recent,
        "older": older,
        "errors": errors,
    }


def format_human(data: dict) -> str:
    """Format results as human-readable text (compatible with brave_news.js output)."""
    lines = []
    hours = data["recent_window_hours"]

    lines.append("=== ATLAS NEWS INTELLIGENCE — Iran Conflict ===")
    lines.append(f"Timestamp: {data['timestamp']}")
    lines.append(
        f"Sources: {', '.join(f'{k}({v})' for k, v in data['source_stats'].items())}"
    )
    lines.append(
        f"Results: {data['total_deduped']} deduped from {data['total_raw']} raw "
        f"| {data['wire_count']} wire service articles"
    )
    lines.append(
        f"Domains: {data['unique_domains']} unique | "
        f"Top: {', '.join(f'{d}({n})' for d, n in data['top_sources'][:10])}"
    )
    lines.append("")

    # Recent
    lines.append("═" * 70)
    lines.append(
        f"🔴  NEW SINCE LAST UPDATE (last {hours}h) — "
        f"{data['recent_count']} results"
    )
    lines.append("═" * 70)

    if not data["recent"]:
        lines.append("  (no new results in this window)\n")
    else:
        for a in data["recent"]:
            tier_icon = "⚡" if a["is_wire"] else ("📰" if a["tier"] <= 2 else "📋")
            age = _format_age(a.get("age_minutes"))
            lines.append(
                f"  {tier_icon} [{age}] [{a['source_domain']}] "
                f"({a['origin']}) {a['title']}"
            )
            if a["description"]:
                lines.append(f"    {a['description'][:200]}")
            lines.append(f"    {a['url']}")
            lines.append("")

    # Older
    lines.append("")
    lines.append("─" * 70)
    lines.append(
        f"🟡  OLDER CONTEXT ({hours}h-24h) — {data['older_count']} results"
    )
    lines.append("─" * 70)

    if not data["older"]:
        lines.append("  (no older results)\n")
    else:
        for a in data["older"][:40]:  # cap older results
            tier_icon = "⚡" if a["is_wire"] else ("📰" if a["tier"] <= 2 else "📋")
            age = _format_age(a.get("age_minutes"))
            lines.append(
                f"  {tier_icon} [{age}] [{a['source_domain']}] "
                f"({a['origin']}) {a['title']}"
            )
            if a["description"]:
                lines.append(f"    {a['description'][:150]}")
            lines.append(f"    {a['url']}")
            lines.append("")

    # Errors
    if data["errors"]:
        lines.append("\n── ERRORS ──")
        for e in data["errors"]:
            lines.append(f"  [{e['source']}] {e['error']}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Atlas multi-source news intelligence aggregator"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--hours", type=float, default=4.0, help="Recency window (default: 4)"
    )
    parser.add_argument(
        "--sources", type=str, default=None,
        help="Comma-separated sources: brave,gdelt,google_news,liveblog"
    )
    parser.add_argument(
        "--query", type=str, default=None,
        help="Query focus: 'ceasefire' or custom query string"
    )
    args = parser.parse_args()

    sources = args.sources.split(",") if args.sources else None
    data = fetch_all(hours=args.hours, sources=sources, query_focus=args.query)

    if args.json:
        # Remove non-serializable data for JSON output
        print(json.dumps(data, indent=2, default=str))
    else:
        print(format_human(data))


if __name__ == "__main__":
    main()
