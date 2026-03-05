# Atlas News Intelligence — Multi-Source Strategy

## Architecture

Atlas uses a parallel multi-source news aggregation layer (`scripts/news_intel.py`) to
provide comprehensive, real-time conflict intelligence for the Iran Monitor and Ceasefire
Probability Tracker.

```
┌─────────────────────────────────────────────────────────┐
│                    news_intel.py                        │
│                (parallel orchestrator)                   │
├──────────┬──────────┬──────────────┬────────────────────┤
│ Brave API│ GDELT API│ Google News  │ Live Blog Scraper  │
│ (existing│ (free)   │ RSS (free)   │ (free)             │
│  key)    │          │              │                    │
├──────────┴──────────┴──────────────┴────────────────────┤
│              ThreadPoolExecutor (4 workers)             │
├─────────────────────────────────────────────────────────┤
│        Fuzzy Dedup (SequenceMatcher, 0.65 threshold)    │
├─────────────────────────────────────────────────────────┤
│    Wire Service Prioritization (Reuters/AP tier 1)      │
├─────────────────────────────────────────────────────────┤
│        Recency Bucketing (🔴 recent / 🟡 older)         │
└─────────────────────────────────────────────────────────┘
```

## Sources

### 1. Brave Search API (PRIMARY)

- **Endpoints**: `/news/search`, `/web/search`, `/videos/search`
- **Auth**: `BRAVE_API_KEY` env var (free tier: 2000 calls/month)
- **Budget**: ~11 API calls per iran_monitor run
- **Strength**: Best for breaking news, market data, video briefings
- **Implementation**: Delegates to existing `scripts/brave_news.js`

### 2. GDELT API (FREE, REAL-TIME, GLOBAL)

- **Endpoint**: `api.gdeltproject.org/api/v2/doc/doc`
- **Auth**: None required (fully free)
- **Params**: `sourcelang:eng`, `mode=artlist`, `timespan=4h`, `sort=datedesc`
- **Strength**: Massive global coverage (monitors ~300K sources in 65 languages).
  Catches regional outlets and non-English sources with English translations
  that Brave/Google often miss. Real-time (15-min delay).
- **Weakness**: No article snippets in artlist mode; just title + URL + domain

### 3. Google News RSS (FREE, FAST, NO KEY)

- **Endpoint**: `news.google.com/rss/search`
- **Auth**: None required
- **Params**: `q=QUERY`, `hl=en-US`, `gl=US`, `ceid=US:en`
- **Strength**: Google's curation of top news by relevance. 100 results per
  query. Fastest to reflect breaking stories (often ahead of GDELT).
  Includes source attribution.
- **Weakness**: Google News redirect URLs (not direct links). Limited to
  Google's perspective on relevance.

### 4. Live Blog Scraper (FREE, RICHEST SOURCE)

- **Strategy**: Two-step — discover current live blog URLs via Brave Search,
  then fetch and parse the HTML for individual update entries.
- **Discovery queries**:
  ```
  "iran live updates site:cnbc.com"
  "iran war live site:aljazeera.com"
  "iran live site:bbc.com/news"
  "iran live updates site:cnn.com"
  ```
- **Parsing**: CSS selectors for common live blog structures across CNBC, CNN,
  BBC, Al Jazeera. Falls back to `<time>` element detection.
- **Strength**: Live blogs update every 5-15 minutes and contain the richest
  real-time intelligence. Wire service updates, government statements, market
  reactions, and correspondent reports all appear here first.
- **Weakness**: HTML structure varies by outlet and changes without notice.
  Requires selector maintenance. URL patterns change daily.

### 5. NewsAPI.org (OPTIONAL BACKUP)

- **Status**: Not currently implemented
- **Free tier**: 100 requests/day
- **When to add**: If GDELT or Google News become unreliable

## Deduplication Strategy

The aggregator collects 200-400 raw articles across sources. Many are the same
story reported by different outlets or surfaced by multiple sources.

### Fuzzy Matching

```python
from difflib import SequenceMatcher

def is_duplicate(title_a, title_b, threshold=0.65):
    clean_a = normalize(title_a)  # lowercase, strip source suffix, remove punct
    clean_b = normalize(title_b)
    return SequenceMatcher(None, clean_a, clean_b).ratio() >= threshold
```

### Wire Service Prioritization

When duplicates are found, keep the version from the highest-tier source:

| Tier | Sources | Priority |
|------|---------|----------|
| 1 (Wire) | Reuters, AP, AFP | Highest — these are the PRIMARY sources |
| 2 (Tier1) | BBC, CNN, CNBC, Al Jazeera, NYT, WSJ, Bloomberg, FT, Guardian | High |
| 3 (Other) | All other outlets | Lowest — only kept if no tier 1/2 version |

This ensures that when Reuters and 15 local papers all report "Iran launches
missiles at Israel," we keep the Reuters version.

## Usage

```bash
# Full run (all 4 sources, 4-hour window)
python3 scripts/news_intel.py --hours 4

# JSON output for programmatic consumption
python3 scripts/news_intel.py --json --hours 4

# Ceasefire-focused queries
python3 scripts/news_intel.py --query ceasefire --hours 6

# Specific sources only (for debugging/testing)
python3 scripts/news_intel.py --sources gdelt,google_news

# Custom query
python3 scripts/news_intel.py --query "hormuz tanker escort"
```

## Integration Points

### Iran Monitor (4-hourly)

`scripts/iran_monitor_cron.sh` calls `news_intel.py --hours 4` as the primary
news source. Falls back to `brave_news.js` then single Brave Search if it fails.

### Ceasefire Tracker (hourly)

`scripts/ceasefire_cron.sh` calls `news_intel.py --hours 6 --query ceasefire`
to pre-fetch ceasefire-focused news. The pi agent uses this as primary context
and does additional targeted Brave searches per-factor when needed.

## Cost Analysis

| Source | Cost | Rate Limit | Calls/Run |
|--------|------|-----------|-----------|
| Brave Search | Free (2000/mo) | 1 req/sec | 11 (iran_monitor) + 4 (live blog discovery) |
| GDELT | Free | ~100/min | 6 |
| Google News RSS | Free | Unlimited | 6-10 |
| Live Blog Scraper | Free | N/A | 4-6 page fetches |
| **Total** | **$0** | | **~30 fetches/run** |

Brave API is the only bottleneck: 2000 calls/month ÷ (6 iran_monitor runs/day × 11 calls
+ 24 ceasefire runs/day × 4 calls) = ~2000 ÷ (66 + 96) = ~12 days before hitting limit.

**Mitigation**: Ceasefire tracker relies primarily on pre-fetched news (GDELT + Google News)
and only uses Brave for the 4 live blog discovery queries. Iran monitor uses all sources.

## Maintenance

### Live Blog Selectors

Live blog HTML structures change. If `liveblog` source returns 0 results:

1. Manually fetch a live blog URL and inspect HTML structure
2. Add new CSS selectors to `_parse_liveblog_entries()` in `news_intel.py`
3. Test: `python3 scripts/news_intel.py --sources liveblog`

### Adding New Sources

To add a new source:

1. Write a `fetch_SOURCENAME(queries, hours)` function returning `list[dict]`
2. Each dict must go through `_normalize()` for consistent schema
3. Add to `fetch_all()` ThreadPoolExecutor dispatch
4. Add source name to the `enabled` list

### GDELT Query Tuning

GDELT supports powerful query syntax:
- `sourcelang:eng` — English only
- `sourcecountry:US` — US sources only
- `domain:reuters.com` — specific domain
- `tone<-5` — negative tone (conflict articles)
- `theme:MILITARY` — GDELT theme codes

See: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
