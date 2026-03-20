#!/usr/bin/env python3
import asyncio
import feedparser
import hashlib
import json
import logging
import re
import sqlite3
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


#Configs
FEEDS_FILE        = 'feeds.txt'
DB_FILE           = 'headlines.db'
mococo = datetime.now().strftime('%m%d%y_%H')
HTML_OUTPUT       = f'News_Report_{mococo}.html'
JSON_OUTPUT       = f'News_Report_{mococo}.json'
LOG_FILE          = 'news_at_12.log'
ERROR_LOG_FILE    = 'news_errors.log' 
LOG_MAX_BYTES     = 1_000_000         
LOG_BACKUP_COUNT  = 3                 
SUMMARY_LIMIT     = 300 
MAX_WORKERS       = 10
FETCH_TIMEOUT     = 15 #Not sure about this
AUTO_OPEN_BROWSER = True


def setup_logging():
    log = logging.getLogger()
    log.setLevel(logging.DEBUG)

    fmt_file    = logging.Formatter(
        '%(asctime)s  %(levelname)-8s  %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    fmt_console = logging.Formatter('%(levelname)-8s  %(message)s')

    
    fh = RotatingFileHandler(
        LOG_FILE, maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT, encoding='utf-8',
    )
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt_file)

    
    eh = RotatingFileHandler(
        ERROR_LOG_FILE, maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT, encoding='utf-8',
    )
    eh.setLevel(logging.ERROR)
    eh.setFormatter(fmt_file)

    
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt_console)

    log.addHandler(fh)
    log.addHandler(eh)
    log.addHandler(ch)


def strip_html(text):
    """Remove HTML tags from a string."""
    return re.sub(r'<[^>]+>', '', text or '').strip()


def url_hash(url):
    return hashlib.sha256(url.encode('utf-8')).hexdigest()


def parse_date(entry):
    for attr in ('published_parsed', 'updated_parsed'):
        val = getattr(entry, attr, None)
        if val:
            return datetime(*val[:6]).isoformat()
    return None


def pretty_date(iso):
    if not iso:
        return 'Date unknown'
    try:
        return datetime.fromisoformat(iso).strftime('%B %d, %Y  %H:%M')
    except ValueError:
        return iso


def read_urls(filename):
    try:
        with open(filename, 'r') as f:
            urls = [line.strip() for line in f
                    if line.strip() and not line.strip().startswith('#')]
        return urls
    except FileNotFoundError:
        logging.error(f"Could not find feeds file: '{filename}'")
        return []


def get_db(db_file):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS feeds (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            url          TEXT    NOT NULL UNIQUE,
            title        TEXT,
            site_link    TEXT,
            first_seen   TEXT    NOT NULL,
            last_fetched TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS headlines (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url_hash    TEXT    NOT NULL UNIQUE,  -- SHA-256 of article URL
            feed_id     INTEGER NOT NULL REFERENCES feeds(id),
            title       TEXT    NOT NULL,
            url         TEXT    NOT NULL,
            published   TEXT,                     -- ISO-8601 or NULL
            summary     TEXT,
            first_seen  TEXT    NOT NULL,
            last_seen   TEXT    NOT NULL,
            seen_count  INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS runs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at     TEXT    NOT NULL,  -- ISO-8601
            finished_at    TEXT    NOT NULL,  -- ISO-8601
            elapsed_sec    REAL    NOT NULL,
            feeds_fetched  INTEGER NOT NULL,
            feeds_failed   INTEGER NOT NULL,
            articles_total INTEGER NOT NULL,
            articles_new   INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_headlines_feed
            ON headlines(feed_id);
        CREATE INDEX IF NOT EXISTS idx_headlines_first_seen
            ON headlines(first_seen);
    """)
    conn.commit()
    return conn


def log_run_summary(conn, started_at, finished_at, elapsed_sec,
                    feeds_fetched, feeds_failed, articles_total, articles_new):
    
    with conn:
        conn.execute("""
            INSERT INTO runs
                (started_at, finished_at, elapsed_sec,
                 feeds_fetched, feeds_failed, articles_total, articles_new)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (started_at, finished_at, elapsed_sec,
              feeds_fetched, feeds_failed, articles_total, articles_new))
    logging.info(
        f"Run summary saved to DB — {feeds_fetched} feeds fetched, "
        f"{feeds_failed} failed, {articles_new}/{articles_total} new articles"
    )


def upsert_feed(conn, url, title, site_link):
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT INTO feeds (url, title, site_link, first_seen, last_fetched)
        VALUES (:url, :title, :site_link, :now, :now)
        ON CONFLICT(url) DO UPDATE SET
            title        = excluded.title,
            last_fetched = excluded.last_fetched
    """, {'url': url, 'title': title, 'site_link': site_link, 'now': now})
    row = conn.execute('SELECT id FROM feeds WHERE url = ?', (url,)).fetchone()
    return row['id']


def upsert_headline(conn, feed_id, title, url, published, summary):
    now   = datetime.now().isoformat()
    uhash = url_hash(url)

    existing = conn.execute(
        'SELECT * FROM headlines WHERE url_hash = ?', (uhash,)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE headlines
            SET last_seen  = ?,
                seen_count = seen_count + 1
            WHERE url_hash = ?
        """, (now, uhash))
        updated = conn.execute(
            'SELECT * FROM headlines WHERE url_hash = ?', (uhash,)
        ).fetchone()
        return dict(updated), False
    else:
        conn.execute("""
            INSERT INTO headlines
                (url_hash, feed_id, title, url, published, summary,
                 first_seen, last_seen, seen_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (uhash, feed_id, title, url, published, summary, now, now))
        row = conn.execute(
            'SELECT * FROM headlines WHERE url_hash = ?', (uhash,)
        ).fetchone()
        return dict(row), True

def fetch_feed(feed_url):
    t_start = time.monotonic()
    try:
        feed = feedparser.parse(feed_url, request_headers={
            'tekLocke': 'news_at_12/2.0',
        })
    except Exception as exc:
        logging.error(f"Failed to fetch {feed_url}: {exc}")
        return None

    elapsed = time.monotonic() - t_start

    feed_title = strip_html(getattr(feed.feed, 'title', feed_url))
    feed_link  = getattr(feed.feed, 'link', feed_url)

    raw_entries = []
    for entry in feed.entries:
        title   = strip_html(entry.get('title', 'No title'))
        url     = entry.get('link', '')
        if not url:
            continue

        published = parse_date(entry)
        raw_sum   = entry.get('summary', entry.get('description', ''))
        summary   = strip_html(raw_sum)
        if len(summary) > SUMMARY_LIMIT:
            summary = summary[:SUMMARY_LIMIT].rsplit(' ', 1)[0] + '...'

        raw_entries.append({
            'title':     title,
            'url':       url,
            'published': published,
            'summary':   summary,
        })

    logging.info(f"Fetched '{feed_title}' in {elapsed:.2f}s ({len(raw_entries)} entries)")
    return {
        'feed_url':    feed_url,
        'feed_title':  feed_title,
        'feed_link':   feed_link,
        'raw_entries': raw_entries,
    }

def store_feed(conn, raw):
    new_count = 0
    entries   = []

    with conn:
        feed_id = upsert_feed(conn, raw['feed_url'], raw['feed_title'], raw['feed_link'])

        for e in raw['raw_entries']:
            row, is_new = upsert_headline(
                conn, feed_id,
                e['title'], e['url'], e['published'], e['summary']
            )
            if is_new:
                new_count += 1

            entries.append({
                'title':      row['title'],
                'url':        row['url'],
                'published':  row['published'],
                'summary':    row['summary'],
                'first_seen': row['first_seen'],
                'last_seen':  row['last_seen'],
                'seen_count': row['seen_count'],
                'is_new':     is_new,
            })

    return {
        'feed_title': raw['feed_title'],
        'feed_url':   raw['feed_url'],
        'feed_link':  raw['feed_link'],
        'new_count':  new_count,
        'entries':    entries,
    }

async def fetch_all(feed_urls, conn):
    
    #loop = asyncio.get_event_loop()
    loop = asyncio.get_running_loop()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        tasks = [
            loop.run_in_executor(pool, fetch_feed, url)
            for url in feed_urls
        ]
        raw_results = await asyncio.gather(*tasks)

    all_feeds = []
    for raw in raw_results:
        if raw is not None:
            feed_data = store_feed(conn, raw)
            all_feeds.append(feed_data)

    return all_feeds

def export_json(all_feeds, filename):
    payload = {
        'generated_at':   datetime.now().isoformat(),
        'feed_count':     len(all_feeds),
        'total_articles': sum(len(f['entries']) for f in all_feeds),
        'feeds': [
            {
                'feed_title': f['feed_title'],
                'feed_url':   f['feed_url'],
                'articles': [
                    {
                        'title':      e['title'],
                        'url':        e['url'],
                        'published':  e['published'],
                        'summary':    e['summary'],
                        'first_seen': e['first_seen'],
                        'seen_count': e['seen_count'],
                    }
                    for e in f['entries']
                ]
            }
            for f in all_feeds
        ]
    }
    with open(filename, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    logging.info(f"JSON saved -> {filename}")


def build_html(all_feeds, elapsed_seconds):

    feed_cards = ''
    for feed in all_feeds:
        entries_html = ''
        for e in feed['entries']:
            badge = '<span class="badge new">NEW</span>' if e['is_new'] else \
                    f'<span class="badge seen">seen {e["seen_count"]}×</span>'
            summary_block = (
                f'<p class="summary">{e["summary"]}</p>' if e['summary'] else ''
            )
            first_seen_block = '' if e['is_new'] else \
                f'<span class="meta">First seen: {pretty_date(e["first_seen"])}</span>'

            entries_html += f"""
            <article class="entry">
              <div class="entry-top">
                <a class="headline" href="{e['url']}" target="_blank" rel="noopener">
                  {e['title']}
                </a>
                {badge}
              </div>
              <span class="meta">{pretty_date(e['published'])}</span>
              {first_seen_block}
              {summary_block}
            </article>"""

        total  = len(feed['entries'])
        new    = feed['new_count']
        repeat = total - new

        feed_cards += f"""
        <section class="feed-card">
          <div class="feed-header">
            <h2 class="feed-title">
              <a href="{feed['feed_link']}" target="_blank" rel="noopener">
                {feed['feed_title']}
              </a>
            </h2>
            <p class="feed-url">{feed['feed_url']}</p>
            <p class="feed-stats">
              {total} articles &nbsp;·&nbsp; {new} new &nbsp;·&nbsp; {repeat} repeat
            </p>
          </div>
          {entries_html}
        </section>"""

    generated      = datetime.now().strftime('%B %d, %Y at %H:%M')
    total_articles = sum(len(f['entries']) for f in all_feeds)
    total_new      = sum(f['new_count']    for f in all_feeds)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>RSS Headlines</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #f0f2f5;
      color: #1a1a2e;
      padding: 2rem 1rem;
    }}

    header {{
      max-width: 860px;
      margin: 0 auto 2rem;
      border-left: 5px solid #4f46e5;
      padding-left: 1rem;
    }}
    header h1 {{ font-size: 1.8rem; color: #4f46e5; }}
    header p  {{ color: #555; margin-top: .3rem; font-size: .9rem; }}

    .feed-card {{
      max-width: 860px;
      margin: 0 auto 2rem;
      background: #fff;
      border-radius: 12px;
      box-shadow: 0 2px 12px rgba(0,0,0,.08);
      overflow: hidden;
    }}

    .feed-header {{
      background: #4f46e5;
      padding: .9rem 1.4rem .75rem;
    }}
    .feed-title {{ font-size: 1.05rem; }}
    .feed-title a {{ color: #fff; text-decoration: none; }}
    .feed-title a:hover {{ text-decoration: underline; }}
    .feed-url   {{ font-size: .75rem; color: #c7d2fe; margin-top: .2rem; }}
    .feed-stats {{ font-size: .78rem; color: #a5b4fc; margin-top: .3rem; }}

    .entry {{
      padding: 1rem 1.4rem;
      border-bottom: 1px solid #f0f0f0;
      display: grid;
      gap: .3rem;
    }}
    .entry:last-child {{ border-bottom: none; }}
    .entry:hover {{ background: #fafafa; }}

    .entry-top {{
      display: flex;
      align-items: flex-start;
      gap: .6rem;
    }}

    .headline {{
      font-size: 1rem;
      font-weight: 600;
      color: #4f46e5;
      text-decoration: none;
      line-height: 1.4;
      flex: 1;
    }}
    .headline:hover {{ text-decoration: underline; }}

    .badge {{
      font-size: .7rem;
      font-weight: 700;
      padding: .2rem .5rem;
      border-radius: 99px;
      white-space: nowrap;
      margin-top: .15rem;
      flex-shrink: 0;
    }}
    .badge.new  {{ background: #dcfce7; color: #166534; }}
    .badge.seen {{ background: #f1f5f9; color: #64748b; }}

    .meta {{
      font-size: .78rem;
      color: #999;
    }}

    .summary {{
      font-size: .88rem;
      color: #444;
      line-height: 1.55;
    }}

    footer {{
      text-align: center;
      font-size: .8rem;
      color: #aaa;
      margin-top: 1rem;
    }}
  </style>
</head>
<body>
  <header>
    <h1>RSS Headlines</h1>
    <p>
      Generated {generated}
      &nbsp;&middot;&nbsp; {len(all_feeds)} feeds
      &nbsp;&middot;&nbsp; {total_articles} articles
      &nbsp;&middot;&nbsp; <strong>{total_new} new</strong>
      &nbsp;&middot;&nbsp; fetched in {elapsed_seconds:.2f}s
    </p>
  </header>

  {feed_cards}

  <footer>Generated by rss_summarizer.py &nbsp;&middot;&nbsp; DB: {DB_FILE}</footer>
</body>
</html>"""


def save_html(all_feeds, filename, elapsed_seconds):
    with open(filename, 'w', encoding='utf-8') as fh:
        fh.write(build_html(all_feeds, elapsed_seconds))
    logging.info(f"HTML saved -> {filename}")
    if AUTO_OPEN_BROWSER:
        webbrowser.open(Path(filename).resolve().as_uri())

def main():
    setup_logging()
    logging.info("=" * 50)
    logging.info(f"Good Morning World, I am News_at_12,and I am here for you and it's {mococo}")

    feed_urls = read_urls(FEEDS_FILE)
    if not feed_urls:
        logging.error(f"No URLs found in '{FEEDS_FILE}'. Add some and try again.")
        return

    logging.info(f"Connecting to database: {DB_FILE}")
    conn = get_db(DB_FILE)

    logging.info(
        f"Fetching {len(feed_urls)} feed(s) concurrently "
        f"(up to {MAX_WORKERS} at a time)"
    )

    started_at = datetime.now().isoformat()
    t_start    = time.monotonic()
    all_feeds  = asyncio.run(fetch_all(feed_urls, conn))
    elapsed    = time.monotonic() - t_start
    finished_at = datetime.now().isoformat()

    total        = sum(len(f['entries']) for f in all_feeds)
    total_new    = sum(f['new_count']    for f in all_feeds)
    feeds_failed = len(feed_urls) - len(all_feeds)

    logging.info(
        f"Fetched {total} articles ({total_new} new) "
        f"from {len(all_feeds)} feed(s) in {elapsed:.2f}s "
        f"({feeds_failed} feed(s) failed)"
    )

    log_run_summary(
        conn,
        started_at   = started_at,
        finished_at  = finished_at,
        elapsed_sec  = round(elapsed, 3),
        feeds_fetched = len(all_feeds),
        feeds_failed  = feeds_failed,
        articles_total = total,
        articles_new   = total_new,
    )

    conn.close()

    logging.info("Saving output files...")
    save_html(all_feeds, HTML_OUTPUT, elapsed)
    export_json(all_feeds, JSON_OUTPUT)
    logging.info(f"Done. Logs: {LOG_FILE} | Errors: {ERROR_LOG_FILE} | DB: {DB_FILE}")


if __name__ == "__main__":
    main()