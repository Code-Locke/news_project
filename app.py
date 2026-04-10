import sqlite3
import threading
#logging functionality added
import logging
import asyncio
import tomllib
import time
#Moved import to front
from datetime import datetime
from flask import Flask, render_template, jsonify
from flasgger import Swagger

from news_at_12 import fetch_all, get_db, log_run_summary, export_json, save_html, load_config
#Load config doesn't need to be here

app = Flask(__name__)
CONFIG_FILE = 'config.toml'

# ── Flasgger / Swagger UI config ──────────────────────────────────────────────
app.config['SWAGGER'] = {
    'title':       'News_at_12 API',
    'description': 'REST API for the News_at_12 RSS headline aggregator.',
    'version':     '1.0.0',
    'uiversion':   3,
}
Swagger(app)

# ── Shared run-state ──────────────────────────────────────────────────────────
_run_lock       = threading.Lock()
_is_running     = False
_is_running_ref = [False]   # mutable reference shared with the API blueprint


# ── Register API blueprint ────────────────────────────────────────────────────
from api import api as api_blueprint, init_api
app.register_blueprint(api_blueprint)
init_api(_run_lock, _is_running_ref)


def get_connection():
    """Open a SQLite connection using the database path from config.toml.

    Reads ``db_file`` from the ``[settings]`` section of ``config.toml``.
    Falls back to ``'headlines.db'`` if the config cannot be loaded.

    Returns:
        tuple[sqlite3.Connection, str]: A two-element tuple containing:

        - An open database connection with ``row_factory`` set to
          ``sqlite3.Row`` for dict-style column access.
        - The resolved database file path as a string.
    """
    settings = load_config(CONFIG_FILE)
    if settings:
        db_file = settings['settings'].get('db_file', 'headlines.db')
    else:
        db_file = 'headlines.db'
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    return conn, db_file


def _run_aggregator():
    """Run the full aggregation pipeline in a background thread.

    Intended to be called via ``threading.Thread`` — never directly.
    Loads config, fetches all enabled feeds concurrently, writes results
    to the database, logs the run summary, and exports a JSON snapshot.
    Resets ``_is_running`` and ``_is_running_ref[0]`` to ``False`` on
    completion, whether the run succeeded or failed.

    If config is missing or contains no enabled feeds, the function
    exits early and resets the running flag without fetching anything.
    """
    global _is_running
    config = load_config(CONFIG_FILE)
    if not config or not config['feeds']:
        with _run_lock:
            _is_running = False
            _is_running_ref[0] = False
        return
    settings      = config['settings']
    feeds         = config['feeds']
    feed_urls     = [fd['url'] for fd in feeds]
    db_file       = settings.get('db_file', 'headlines.db')
    max_workers   = settings.get('max_workers', 10)
    summary_limit = settings.get('summary_limit', 300)
    #html_output  = settings.get('html_output', 'headlines.html')
    json_output   = settings.get('json_output', 'headlines.json')
    auto_open     = False

    conn       = get_db(db_file)
    started_at = datetime.now().isoformat()
    t_start    = time.monotonic()

    try:
        all_feeds_data = asyncio.run(fetch_all(feed_urls, conn, max_workers, summary_limit))
    except Exception:
        #This was eating up the news_at_12 script's errors.
        #If the script dies, no errors would be shown.
        logging.exception(f"We've got some news for you, coming right up,\n news_at_12 has been found dead")
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

    #save_html(all_feeds_data, html_output, elapsed, db_file, auto_open_browser=auto_open)
    export_json(all_feeds_data, json_output)

    with _run_lock:
        _is_running = False
        _is_running_ref[0] = False


@app.route('/')
def index():
    """Render the main headlines page.

    Queries all headlines joined with their parent feed, groups them by
    feed title in Python, and passes the grouped structure to the
    ``index.html`` template.

    Returns:
        str: Rendered HTML response for the ``/`` route.
    """
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
    """Render the feeds page listing all RSS sources with headline counts.

    Queries all feeds with a count of their associated headlines using a
    LEFT JOIN so feeds with zero articles still appear.

    Returns:
        str: Rendered HTML response for the ``/feeds`` route.
    """
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
    """Render the run history page showing the last 50 aggregator runs.

    Returns:
        str: Rendered HTML response for the ``/runs`` route.
    """
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
    """Fire the aggregator in a background thread and return immediately.

    Uses ``_run_lock`` to ensure only one run can be in progress at a time.
    The frontend polls ``/status`` to detect completion. This route is
    used by the Jinja2 frontend — the API equivalent lives at
    ``/api/v1/run``.

    Returns:
        tuple[flask.Response, int]: A JSON response with HTTP 202 in both
        cases:

        - ``{'status': 'started'}`` if the run was successfully launched.
        - ``{'status': 'already_running'}`` if a run is already in progress.
    """
    global _is_running

    with _run_lock:
        if _is_running:
            return jsonify({'status': 'already_running'}), 202
        _is_running = True
        _is_running_ref[0] = True

    t = threading.Thread(target=_run_aggregator, daemon=True)
    t.start()

    return jsonify({'status': 'started'}), 202


@app.route('/status')
def status():
    """Return the current aggregator run state as JSON.

    Polled every 3 seconds by the frontend JavaScript to update the
    status indicator in the navigation bar. The API equivalent lives
    at ``/api/v1/status``.

    Returns:
        tuple[flask.Response, int]: A JSON response with HTTP 200::

            {'running': true}  # aggregator is currently running
            {'running': false} # aggregator is idle
    """
    return jsonify({'running': _is_running})


if __name__ == '__main__':
    app.run(debug=True, port=5000)