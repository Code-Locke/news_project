# RSS Headline Summarizer

A self-hosted RSS feed aggregator that fetches headlines concurrently, deduplicates articles, tracks reading history, and outputs both human-readable HTML and JSON formats for LLM usage(maybe).

## Features

- **Concurrent async fetching** — fetches multiple RSS feeds in parallel using asyncio + ThreadPoolExecutor
- **SQLite database** — persistent storage with deduplication, tracking when headlines were first/last seen and how many times they've appeared
- **Dual output formats**:
  - Styled HTML page with clickable links, NEW/repeat badges, and responsive design
  - Clean JSON export optimized for LLM ingestion
- **Comprehensive logging** — rotating file logs with separate error tracking
- **Run history** — every fetch is logged to the database with timing and statistics
- **Batch transaction commits** — efficient database writes (one commit per feed instead of per-row)

## Requirements

- Python 3.11+
- `feedparser` library

### Basic setup

```bash
# Clone the repository
git clone https://github.com/username/news_project.git
cd news_project

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install feedparser

# Add your RSS feeds
nano feeds.txt  # Add one feed URL per line FOR NOW

# Run it
python news_at_12.py
```

## Configuration

Edit the config block that i bunched up at the top

```python
FEEDS_FILE        = 'feeds.txt'      # RSS feed URLs (one per line)
DB_FILE           = 'headlines.db'       # SQLite database
HTML_OUTPUT       = 'headlines_date.html'     # HTML output file name
JSON_OUTPUT       = 'headlines_date.json'     # JSON output file name
LOG_FILE          = 'news_at_12.log' # Main log file
ERROR_LOG_FILE    = 'news_errors.log'     # Error-only log
LOG_MAX_BYTES     = 1_000_000            # Rotate logs at 1 MB
LOG_BACKUP_COUNT  = 3                    # Keep 3 rotated log copies
SUMMARY_LIMIT     = 300                  # Max characters per summary
MAX_WORKERS       = 10                   # Concurrent fetch threads
FETCH_TIMEOUT     = 15                   # Timeout per feed (seconds)
AUTO_OPEN_BROWSER = True                 # Open HTML in browser after run
#HTML will change to Flask Frontend later.
```

## RSS Feed File Format

`feeds.txt` accepts one URL per line. Lines starting with `#` are treated as comments
This feed file format will change to be something more elegant. maybe TOML or JSON later on.


## Output Files

### HTML (`headlines{date}.html`)
- Opens automatically in your browser (unless `AUTO_OPEN_BROWSER = False`)
- Responsive design that works on mobile
- Each feed shown as a card with stats (total articles, new, repeat)
- Headlines clickable with NEW/repeat badges
- Shows when articles were first seen and how many times they've appeared

### JSON (`headlines_date.json`)
Structured format optimized for LLM consumption:

### Database (`headlines.db`)
SQLite database with three tables:

**`feeds`** — one row per RSS source
```sql
id, url, title, site_link, first_seen, last_fetched
```

**`headlines`** — one row per unique article (keyed by SHA-256 hash of URL)
```sql
id, url_hash, feed_id, title, url, published, summary,
first_seen, last_seen, seen_count
```

**`runs`** — one row per execution
```sql
id, started_at, finished_at, elapsed_sec, feeds_fetched,
feeds_failed, articles_total, articles_new
```

## Querying the Database

Example SQL queries you can run:

```bash
sqlite3 headlines.db
```

```sql
-- Most frequently recurring headlines
SELECT title, seen_count, first_seen 
FROM headlines 
ORDER BY seen_count DESC 
LIMIT 20;

-- Everything new in the last 24 hours
SELECT title, url 
FROM headlines 
WHERE first_seen >= datetime('now', '-1 day');

-- All headlines from a specific source
SELECT h.title, h.first_seen 
FROM headlines h
JOIN feeds f ON f.id = h.feed_id 
WHERE f.title = 'BBC News';

-- Run history with statistics
SELECT started_at, elapsed_sec, articles_new, feeds_failed
FROM runs 
ORDER BY started_at DESC 
LIMIT 10;
```

## Logging

Two log files are automatically created and rotated:

- **`news_at_12.log`** — all INFO and above messages
- **`news_errors.log`** — ERROR messages only

Logs rotate at 1 MB and keep 3 backup copies by default.

Example log output:
```
2026-03-20 11:51:44  INFO      ==================================================
2026-03-20 11:51:44  INFO      Good Morning World, I am News_at_12,and I am here for you and it's 032026_11
2026-03-20 11:51:44  INFO      Connecting to database: headlines.db
2026-03-20 11:51:44  INFO      Fetching 14 feed(s) concurrently (up to 10 at a time)
2026-03-20 11:51:44  INFO      Fetched 'Vox' in 0.26s (10 entries)
2026-03-20 11:51:44  INFO      Fetched 'TheHill.com Just In' in 0.36s (15 entries)
2026-03-20 11:51:44  INFO      Fetched 'The Race' in 0.23s (15 entries)
2026-03-20 11:51:44  INFO      Fetched 'NYT > U.S. News' in 0.54s (29 entries)
...
```

**Key design decisions:**

- **Fetch/store separation** — `fetch_feed()` does pure network calls (safe to parallelize), `store_feed()` does pure DB writes (sequential for SQLite safety)
- **Batch commits** — one transaction per feed instead of per-row for 10-60× fewer disk flushes
- **URL hashing** — SHA-256 of article URLs provides instant deduplication without string comparisons

## Deployment Options

### Personal laptop/desktop
- Run manually or via cron
- Access HTML output locally

### Raspberry Pi 4/5 (recommended)
- Works great with current configuration
- Can run 24/7 as a headless server
- Low power consumption

### Raspberry Pi Zero
- Reduce `MAX_WORKERS` to 2
- Consider sequential fetching instead of async
- Limit to 10-15 feeds maximum

## Future Enhancements

Features to add:

- [ ] Web UI (Flask/FastAPI) for managing feeds and viewing headlines
- [ ] Email delivery of daily/weekly digests
- [ ] Keyword watchlists and alerting
- [ ] Topic clustering across feeds
- [ ] Query CLI for database exploration
- [ ] OPML import/export for feed lists

## Troubleshooting

**"No URLs found in rss_feeds.txt"**
- Make sure `feeds.txt` exists and contains at least one uncommented URL

**Feeds timing out**
- Increase `FETCH_TIMEOUT` in the config
- Some feeds are slow or unreliable — check the error log

**High memory usage**
- Reduce `MAX_WORKERS` and/or `SUMMARY_LIMIT`
- Consider sequential fetching for low-memory systems

**HTML not opening automatically**
- Set `AUTO_OPEN_BROWSER = True` in config
- Or manually open the html file in your browser

## Contributing

Contributions welcome!

## License

MIT License - see LICENSE file for details

## Acknowledgments

- Built with [feedparser](https://github.com/kurtmckee/feedparser)
- Inspired by the need for a self-hosted, LLM-friendly RSS aggregator
- Built to stop doomscrolling, self-curated news aggregation for personal use

## Contact

https://github.com/Code-Locke

---

**Note**: This is a personal project built for self-hosting. It's not designed for large-scale multi-user deployments without modification. USE AT OWN RISK