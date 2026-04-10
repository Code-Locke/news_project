import asyncio
import feedparser
import hashlib
import json
import logging
import re
import sqlite3
import time
import tomllib
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

# New UPDATE!!! configuration is now stored in config.toml
#I chose TOML because I wanted to keep things as 'native' as possible
#The date HTML output is also deprecated due to me moving to having
#A Flask app as a proper frontend, no need for headlines of the hour
#Also cleaned up some other codes and indentations?
#Added Labels
CONFIG_FILE = 'config.toml'


#Logging
def setup_logging(log_file, error_log_file, log_max_bytes, log_backup_count):
    """Configure the root logger with file and console handlers.

    Sets up three handlers:

    - A rotating file handler writing INFO and above to ``log_file``.
    - A rotating file handler writing ERROR and above to ``error_log_file``.
    - A stream handler writing INFO and above to the terminal.

    Both file handlers rotate at ``log_max_bytes`` and keep
    ``log_backup_count`` backup copies so logs never grow unbounded.

    Args:
        log_file (str): Path to the main log file.
        error_log_file (str): Path to the error-only log file.
        log_max_bytes (int): Maximum size in bytes before a log file rotates.
        log_backup_count (int): Number of rotated backup files to keep.
    """
    log = logging.getLogger()
    log.setLevel(logging.DEBUG)

    fmt_file    = logging.Formatter(
        '%(asctime)s  %(levelname)-8s  %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    fmt_console = logging.Formatter('%(levelname)-8s  %(message)s')

    fh = RotatingFileHandler(
        log_file, maxBytes=log_max_bytes,
        backupCount=log_backup_count, encoding='utf-8',
    )
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt_file)


    eh = RotatingFileHandler(
        error_log_file, maxBytes=log_max_bytes,
        backupCount=log_backup_count, encoding='utf-8',
    )
    eh.setLevel(logging.ERROR)
    eh.setFormatter(fmt_file)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt_console)

    log.addHandler(fh)
    log.addHandler(eh)
    log.addHandler(ch)


#Utilities
def strip_html(text):
    """Remove HTML tags from a string.

    Args:
        text (str): Raw text that may contain HTML markup.

    Returns:
        str: The input string with all HTML tags removed and whitespace
        stripped. Returns an empty string if ``text`` is None or empty.
    """
    return re.sub(r'<[^>]+>', '', text or '').strip()


def url_hash(url):
    """Return a stable SHA-256 hex digest for a URL.

    Used as a unique key in the database to deduplicate headlines without
    storing or comparing full URL strings on every insert.

    Args:
        url (str): The article URL to hash.

    Returns:
        str: A 64-character lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(url.encode('utf-8')).hexdigest()


def parse_date(entry):
    """Extract a publication date from a feed entry and return it as ISO-8601.

    Tries ``published_parsed`` first, then falls back to ``updated_parsed``.
    Both attributes are time-tuples supplied by feedparser.

    Args:
        entry: A feedparser entry object.

    Returns:
        str or None: An ISO-8601 datetime string (e.g. ``'2026-04-10T12:00:00'``),
        or ``None`` if no parseable date attribute is found.
    """
    for attr in ('published_parsed', 'updated_parsed'):
        val = getattr(entry, attr, None)
        if val:
            return datetime(*val[:6]).isoformat()
    return None


def pretty_date(iso):
    """Format an ISO-8601 datetime string for human-readable display.

    Args:
        iso (str or None): An ISO-8601 datetime string, or ``None``.

    Returns:
        str: A formatted string such as ``'April 10, 2026  12:00'``.
        Returns ``'Date unknown'`` if ``iso`` is falsy, or the original
        string unchanged if it cannot be parsed.
    """
    if not iso:
        return 'Date unknown'
    try:
        return datetime.fromisoformat(iso).strftime('%B %d, %Y  %H:%M')
    except ValueError:
        return iso


def load_config(filename):
    """Load and validate configuration from a TOML file.

    Reads the file at ``filename``, checks that the required ``[settings]``
    and ``[[feeds]]`` sections exist, and filters out any feeds whose
    ``enabled`` key is set to ``false``.

    Args:
        filename (str): Path to the TOML configuration file.

    Returns:
        dict or None: A dict with two keys on success:

        - ``'settings'`` (dict): The ``[settings]`` table from the TOML file.
        - ``'feeds'`` (list[dict]): Only the feeds where ``enabled`` is
          ``true`` (or omitted, which defaults to ``true``).

        Returns ``None`` if the file is missing, contains invalid TOML, or
        is missing required sections.
    """
    try:
        with open(filename, 'rb') as f:
            config = tomllib.load(f)
        
        if 'settings' not in config:
            logging.error(f"Missing [settings] section in '{filename}'")
            return None
        
        if 'feeds' not in config:
            logging.error(f"Missing [[feeds]] section in '{filename}'")
            return None
        
        
        all_feeds = config.get('feeds', [])
        enabled_feeds = [
            feed for feed in all_feeds 
            if feed.get('enabled', True)
        ]
        
        if not enabled_feeds:
            logging.warning(f"No enabled feeds found in '{filename}'")
        
        
        return {
            'settings': config['settings'],
            'feeds': enabled_feeds,
        }
        
    except FileNotFoundError:
        logging.error(f"Could not find config file: '{filename}'")
        return None
    except tomllib.TOMLDecodeError as e:
        logging.error(f"Invalid TOML syntax in '{filename}': {e}")
        return None



def get_db(db_file):
    """Open (or create) the SQLite database and ensure the schema exists.

    Enables WAL journal mode for better concurrent read performance and
    creates the ``feeds``, ``headlines``, and ``runs`` tables along with
    their indexes if they do not already exist.

    Args:
        db_file (str): Path to the SQLite database file. The file is created
            if it does not exist.

    Returns:
        sqlite3.Connection: An open database connection with
        ``row_factory`` set to ``sqlite3.Row`` for dict-style column access.
    """
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
    """Write a single row to the ``runs`` table summarising a completed run.

    Uses its own explicit commit so it is not part of any feed transaction.

    Args:
        conn (sqlite3.Connection): An open database connection.
        started_at (str): ISO-8601 timestamp when the run began.
        finished_at (str): ISO-8601 timestamp when the run completed.
        elapsed_sec (float): Total wall-clock time for the run in seconds.
        feeds_fetched (int): Number of feeds successfully fetched.
        feeds_failed (int): Number of feeds that failed to fetch.
        articles_total (int): Total number of articles processed.
        articles_new (int): Number of articles that were new this run.
    """
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
    """Insert a feed row if it does not exist, or update its metadata if it does.

    Uses an ``ON CONFLICT`` clause to update ``title`` and ``last_fetched``
    when the URL already exists. The caller is responsible for committing
    the surrounding transaction.

    Args:
        conn (sqlite3.Connection): An open database connection.
        url (str): The RSS feed URL (used as the unique key).
        title (str): The feed's display title.
        site_link (str): The feed's associated website URL.

    Returns:
        int: The integer primary key (``id``) of the feed row.
    """
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
    """Insert a headline if it is new, or bump its seen count if it already exists.

    Keyed by a SHA-256 hash of the article URL so deduplication is fast and
    does not rely on string comparisons. The caller is responsible for
    committing the surrounding transaction.

    Args:
        conn (sqlite3.Connection): An open database connection.
        feed_id (int): The primary key of the parent feed row.
        title (str): The article headline.
        url (str): The article URL (hashed for deduplication).
        published (str or None): ISO-8601 publication date, or ``None``.
        summary (str): A plain-text article summary (HTML already stripped).

    Returns:
        tuple[dict, bool]: A two-element tuple containing:

        - A dict of the headline row as it exists in the database after
          the upsert.
        - ``True`` if the headline was newly inserted, ``False`` if it
          already existed and was updated.
    """
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


def fetch_feed(feed_url, summary_limit=300):
    """Fetch and parse a single RSS feed. No database access.

    Pure network function — safe to call from multiple threads simultaneously.
    Strips HTML from titles and summaries, truncates summaries to
    ``summary_limit`` characters at a word boundary, and normalises dates
    to ISO-8601 strings.

    Args:
        feed_url (str): The RSS feed URL to fetch.
        summary_limit (int): Maximum number of characters to keep per article
            summary. Defaults to 300.

    Returns:
        dict or None: A dict containing raw feed metadata and parsed entries
        on success::

            {
                'feed_url':    str,
                'feed_title':  str,
                'feed_link':   str,
                'raw_entries': list[dict],  # title, url, published, summary
            }

        Returns ``None`` if the feed could not be fetched or parsed.
    """
    t_start = time.monotonic()
    try:
        feed = feedparser.parse(feed_url, request_headers={
            'User-Agent': 'news_at_12/1.5',
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
        if len(summary) > summary_limit:
            summary = summary[:summary_limit].rsplit(' ', 1)[0] + '...'

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
    """Write the output of ``fetch_feed`` to the database.

    Called sequentially — one feed at a time — so SQLite is never touched
    by more than one thread at once. Uses a single ``with conn`` transaction
    per feed so all writes are committed in one disk flush and any failure
    rolls back the entire feed atomically.

    Args:
        conn (sqlite3.Connection): An open database connection.
        raw (dict): The dict returned by :func:`fetch_feed`.

    Returns:
        dict: A fully resolved feed dict ready for HTML/JSON rendering::

            {
                'feed_title': str,
                'feed_url':   str,
                'feed_link':  str,
                'new_count':  int,
                'entries':    list[dict],
            }
    """
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



async def fetch_all(feed_urls, conn, max_workers=10, summary_limit=300):
    """Fetch all feeds concurrently, then store results sequentially.

    Runs all :func:`fetch_feed` calls in a thread pool simultaneously, then
    calls :func:`store_feed` for each result one at a time on the main thread
    to keep SQLite writes safe.

    Args:
        feed_urls (list[str]): List of RSS feed URLs to fetch.
        conn (sqlite3.Connection): An open database connection passed through
            to :func:`store_feed`.
        max_workers (int): Maximum number of concurrent fetch threads.
            Defaults to 10.
        summary_limit (int): Maximum characters per article summary, passed
            through to :func:`fetch_feed`. Defaults to 300.

    Returns:
        list[dict]: A list of resolved feed dicts as returned by
        :func:`store_feed`, one per successfully fetched feed. Failed
        feeds are silently omitted.
    """
    loop = asyncio.get_event_loop()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        from functools import partial
        fetch_with_limit = partial(fetch_feed, summary_limit=summary_limit)
        
        tasks = [
            loop.run_in_executor(pool, fetch_with_limit, url)
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
    """Write a clean JSON snapshot of all feeds and articles to disk.

    The output is structured for easy LLM ingestion, including only
    human-readable fields (no internal database IDs or hashes).

    Args:
        all_feeds (list[dict]): The list of resolved feed dicts returned
            by :func:`fetch_all`.
        filename (str): Path to the output JSON file. Created or overwritten.
    """
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


def build_html(all_feeds, elapsed_seconds, db_file="headlines.db"):
    """Render all feeds and their headlines to a self-contained HTML string.

    Produces a styled, responsive HTML page with per-feed cards, NEW/repeat
    badges, clickable article links, and a summary header. No external
    dependencies — all CSS is inlined.

    Args:
        all_feeds (list[dict]): The list of resolved feed dicts returned
            by :func:`fetch_all`.
        elapsed_seconds (float): Total fetch duration, displayed in the
            page header.
        db_file (str): Path to the database file, displayed in the footer.
            Defaults to ``'headlines.db'``.

    Returns:
        str: A complete HTML document as a string.
    """
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
  <title> Headlines</title>
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
    <h1> Headlines</h1>
    <p>
      Generated {generated}
      &nbsp;&middot;&nbsp; {len(all_feeds)} feeds
      &nbsp;&middot;&nbsp; {total_articles} articles
      &nbsp;&middot;&nbsp; <strong>{total_new} new</strong>
      &nbsp;&middot;&nbsp; fetched in {elapsed_seconds:.2f}s
    </p>
  </header>

  {feed_cards}

  <footer>Generated by news_at_12.py &nbsp;&middot;&nbsp; DB: {db_file}</footer>
</body>
</html>"""


def save_html(all_feeds, filename, elapsed_seconds, db_file, auto_open_browser=True):
    """Write the rendered HTML to disk and optionally open it in the browser.

    Args:
        all_feeds (list[dict]): The list of resolved feed dicts returned
            by :func:`fetch_all`.
        filename (str): Path to the output HTML file. Created or overwritten.
        elapsed_seconds (float): Total fetch duration passed through to
            :func:`build_html` for display in the page header.
        db_file (str): Database file path passed through to :func:`build_html`
            for display in the page footer.
        auto_open_browser (bool): If ``True``, opens the saved file in the
            default web browser after writing. Defaults to ``True``.
    """
    with open(filename, 'w', encoding='utf-8') as fh:
        fh.write(build_html(all_feeds, elapsed_seconds, db_file))
    logging.info(f"HTML saved -> {filename}")
    if auto_open_browser:
        webbrowser.open(Path(filename).resolve().as_uri())


def main():
    """Entry point for running the aggregator as a standalone script.

    Loads configuration from ``config.toml``, sets up logging, connects to
    the database, fetches all enabled feeds concurrently, stores results,
    logs the run summary, and writes HTML and JSON output files.
    """
    config = load_config(CONFIG_FILE)
    if config is None:
        print(f"ERROR: Failed to load configuration from '{CONFIG_FILE}'. Exiting.")
        return
    
    settings = config['settings']
    feeds = config['feeds']
    
    log_file = settings.get('log_file', 'news_at_12.log')
    error_log_file = settings.get('error_log_file', 'news_errors.log')
    log_max_bytes = settings.get('log_max_bytes', 1_000_000)
    log_backup_count = settings.get('log_backup_count', 3)
    db_file = settings.get('db_file', 'headlines.db')
    html_output = settings.get('html_output', 'headlines.html')
    json_output = settings.get('json_output', 'headlines.json')
    max_workers = settings.get('max_workers', 10)
    summary_limit = settings.get('summary_limit', 300)
    auto_open_browser = settings.get('auto_open_browser', True)
    
    # Set up logging with config values
    setup_logging(log_file, error_log_file, log_max_bytes, log_backup_count)
    
    logging.info("=" * 50)
    logging.info("Good morning 21st century, this is your news feed app.")
    
    if not feeds:
        logging.error(f"No enabled feeds found in '{CONFIG_FILE}'. Please add some and try again.")
        return

    logging.info(f"Connecting to database: {db_file}")
    conn = get_db(db_file)

    logging.info(
        f"Fetching {len(feeds)} feed(s) concurrently "
        f"(up to {max_workers} at a time)"
    )

    feed_urls = [feed['url'] for feed in feeds]

    started_at = datetime.now().isoformat()
    t_start    = time.monotonic()
    all_feeds  = asyncio.run(fetch_all(feed_urls, conn, max_workers, summary_limit))
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
    save_html(all_feeds, html_output, elapsed, db_file, auto_open_browser)
    export_json(all_feeds, json_output)
    logging.info(f"Done. Logs: {log_file} | Errors: {error_log_file} | DB: {db_file}")


if __name__ == "__main__":
    main()