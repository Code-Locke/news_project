import sqlite3
import threading
#logging functionality added
import logging
import asyncio
import tomllib
from datetime import datetime
from flask import Flask, render_template, redirect, url_for, jsonify

from news_at_12 import fetch_all, get_db, log_run_summary, export_json, save_html

app = Flask(__name__)
CONFIG_FILE = 'config.toml'


_run_lock   = threading.Lock()
_is_running = False  


def load_config():
    try:
        with open(CONFIG_FILE, 'rb') as f:
            config = tomllib.load(f)
        settings     = config.get('settings', {})
        all_feeds    = config.get('feeds', [])
        enabled      = [fd for fd in all_feeds if fd.get('enabled', True)]
        return settings, enabled
    except Exception:
        return None, None


def get_connection():
    settings, _ = load_config()
    db_file = settings.get('db_file', 'headlines.db') if settings else 'headlines.db'
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    return conn, db_file


def _run_aggregator():
    global _is_running

    settings, feeds = load_config()
    if not settings or not feeds:
        with _run_lock:
            _is_running = False
        return

    feed_urls        = [fd['url'] for fd in feeds]
    db_file          = settings.get('db_file', 'headlines.db')
    max_workers      = settings.get('max_workers', 10)
    summary_limit    = settings.get('summary_limit', 300)
    html_output      = settings.get('html_output', 'headlines.html')
    json_output      = settings.get('json_output', 'headlines.json')
    auto_open        = False  

    import time
    conn        = get_db(db_file)
    started_at  = datetime.now().isoformat()
    t_start     = time.monotonic()

    try:
        all_feeds_data = asyncio.run(fetch_all(feed_urls, conn, max_workers, summary_limit))
    except Exception:
        #This was eating up the news_at_12 script's errors.
        #If the script dies, no errors would be shown.
        logging.exception("We've got some news for you, coming right up, news_at_12 died")
        all_feeds_data = []

    elapsed     = time.monotonic() - t_start
    finished_at = datetime.now().isoformat()

    total        = sum(len(f['entries']) for f in all_feeds_data)
    total_new    = sum(f['new_count']    for f in all_feeds_data)
    feeds_failed = len(feed_urls) - len(all_feeds_data)

    log_run_summary(
        conn,
        started_at    = started_at,
        finished_at   = finished_at,
        elapsed_sec   = round(elapsed, 3),
        feeds_fetched  = len(all_feeds_data),
        feeds_failed   = feeds_failed,
        articles_total = total,
        articles_new   = total_new,
    )

    conn.close()

    save_html(all_feeds_data, html_output, elapsed, db_file, auto_open_browser=auto_open)
    export_json(all_feeds_data, json_output)

    with _run_lock:
        _is_running = False


@app.route('/')
def index():
    conn, _ = get_connection()
    rows = conn.execute("""
        SELECT
            h.title,
            h.url,
            h.published,
            h.summary,
            h.first_seen,
            h.last_seen,
            h.seen_count,
            f.title  AS feed_title,
            f.url    AS feed_url,
            f.site_link
        FROM headlines h
        JOIN feeds f ON f.id = h.feed_id
        ORDER BY f.title ASC, h.first_seen DESC
    """).fetchall()
    conn.close()

    groups = {}
    for row in rows:
        r = dict(row)
        key = r['feed_title']
        if key not in groups:
            groups[key] = {
                'feed_title': r['feed_title'],
                'feed_url':   r['feed_url'],
                'site_link':  r['site_link'],
                'articles':   [],
            }
        groups[key]['articles'].append(r)

    feed_groups = list(groups.values())
    total = sum(len(g['articles']) for g in feed_groups)
    return render_template('index.html', feed_groups=feed_groups,
                           total=total, is_running=_is_running)


@app.route('/feeds')
def feeds():
    """All RSS sources with headline counts."""
    conn, _ = get_connection()
    rows = conn.execute("""
        SELECT
            f.id,
            f.title,
            f.url,
            f.site_link,
            f.first_seen,
            f.last_fetched,
            COUNT(h.id) AS headline_count
        FROM feeds f
        LEFT JOIN headlines h ON h.feed_id = f.id
        GROUP BY f.id
        ORDER BY f.title ASC
    """).fetchall()
    conn.close()

    feed_list = [dict(r) for r in rows]
    return render_template('feeds.html', feeds=feed_list, is_running=_is_running)


@app.route('/runs')
def runs():
    """Aggregator run history."""
    conn, _ = get_connection()
    rows = conn.execute("""
        SELECT
            id,
            started_at,
            finished_at,
            elapsed_sec,
            feeds_fetched,
            feeds_failed,
            articles_total,
            articles_new
        FROM runs
        ORDER BY started_at DESC
        LIMIT 50
    """).fetchall()
    conn.close()

    run_list = [dict(r) for r in rows]
    return render_template('runs.html', runs=run_list, is_running=_is_running)


@app.route('/run', methods=['POST'])
def trigger_run():
    """
    Fire the aggregator in a background thread.
    Returns immediately — UI polls /status for completion.
    Ignores the request if a run is already in progress.
    """
    global _is_running

    with _run_lock:
        if _is_running:
            return jsonify({'status': 'already_running'}), 202
        _is_running = True

    t = threading.Thread(target=_run_aggregator, daemon=True)
    t.start()

    return jsonify({'status': 'started'}), 202


@app.route('/status')
def status():
    """Returns current run state as JSON. Polled by the frontend."""
    return jsonify({'running': _is_running})

if __name__ == '__main__':
    app.run(debug=True, port=5000)