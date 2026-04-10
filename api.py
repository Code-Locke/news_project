import threading
import sqlite3
import logging
from flask import Blueprint, jsonify, request
from news_at_12 import load_config, get_db

CONFIG_FILE = 'config.toml'

# ── Shared state (imported from app at registration time) ─────────────────────
# These are set by app.py via init_api() so the blueprint shares the same
# lock and flag as the rest of the application.
_run_lock   = None
_is_running_ref = None  # a one-element list so we can mutate it by reference


def init_api(run_lock, is_running_ref):
    """
    Called once from app.py after the blueprint is registered.
    Wires the blueprint up to the shared run-state owned by app.py.
    """
    global _run_lock, _is_running_ref
    _run_lock       = run_lock
    _is_running_ref = is_running_ref


api = Blueprint('api', __name__, url_prefix='/api/v1')


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_connection():
    """Open a SQLite connection using the db_file from config.toml."""
    config = load_config(CONFIG_FILE)
    db_file = config['settings'].get('db_file', 'headlines.db') if config else 'headlines.db'
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    return conn


def paginate(request):
    """Parse and validate ?limit= and ?offset= query params."""
    try:
        limit  = max(1, min(int(request.args.get('limit',  25)), 100))
        offset = max(0, int(request.args.get('offset', 0)))
    except ValueError:
        limit, offset = 25, 0
    return limit, offset


# ── Headlines ─────────────────────────────────────────────────────────────────

@api.route('/headlines', methods=['GET'])
def get_headlines():
    """
    Get all headlines (paginated).
    ---
    tags:
      - Headlines
    parameters:
      - name: limit
        in: query
        type: integer
        default: 25
        description: Number of results to return (max 100)
      - name: offset
        in: query
        type: integer
        default: 0
        description: Number of results to skip
    responses:
      200:
        description: A paginated list of headlines
        schema:
          type: object
          properties:
            data:
              type: array
              items:
                type: object
                properties:
                  id:
                    type: integer
                  title:
                    type: string
                  url:
                    type: string
                  published:
                    type: string
                  summary:
                    type: string
                  first_seen:
                    type: string
                  last_seen:
                    type: string
                  seen_count:
                    type: integer
                  feed_id:
                    type: integer
                  feed_title:
                    type: string
            meta:
              type: object
              properties:
                total:
                  type: integer
                limit:
                  type: integer
                offset:
                  type: integer
    """
    limit, offset = paginate(request)
    conn = get_connection()

    total = conn.execute('SELECT COUNT(*) FROM headlines').fetchone()[0]

    rows = conn.execute("""
        SELECT
            h.id,
            h.title,
            h.url,
            h.published,
            h.summary,
            h.first_seen,
            h.last_seen,
            h.seen_count,
            h.feed_id,
            f.title AS feed_title
        FROM headlines h
        JOIN feeds f ON f.id = h.feed_id
        ORDER BY h.first_seen DESC
        LIMIT ? OFFSET ?
    """, (limit, offset)).fetchall()
    conn.close()

    return jsonify({
        'data': [dict(r) for r in rows],
        'meta': {
            'total':  total,
            'limit':  limit,
            'offset': offset,
        }
    }), 200


@api.route('/headlines/<int:headline_id>', methods=['GET'])
def get_headline(headline_id):
    """
    Get a single headline by ID.
    ---
    tags:
      - Headlines
    parameters:
      - name: headline_id
        in: path
        type: integer
        required: true
        description: The headline's database ID
    responses:
      200:
        description: A single headline
        schema:
          type: object
          properties:
            data:
              type: object
              properties:
                id:
                  type: integer
                title:
                  type: string
                url:
                  type: string
                published:
                  type: string
                summary:
                  type: string
                first_seen:
                  type: string
                last_seen:
                  type: string
                seen_count:
                  type: integer
                feed_id:
                  type: integer
                feed_title:
                  type: string
      404:
        description: Headline not found
        schema:
          type: object
          properties:
            error:
              type: string
    """
    conn = get_connection()
    row = conn.execute("""
        SELECT
            h.id,
            h.title,
            h.url,
            h.published,
            h.summary,
            h.first_seen,
            h.last_seen,
            h.seen_count,
            h.feed_id,
            f.title AS feed_title
        FROM headlines h
        JOIN feeds f ON f.id = h.feed_id
        WHERE h.id = ?
    """, (headline_id,)).fetchone()
    conn.close()

    if row is None:
        return jsonify({'error': 'headline not found'}), 404

    return jsonify({'data': dict(row)}), 200


# ── Feeds ─────────────────────────────────────────────────────────────────────

@api.route('/feeds', methods=['GET'])
def get_feeds():
    """
    Get all configured RSS feeds.
    ---
    tags:
      - Feeds
    responses:
      200:
        description: A list of all feeds
        schema:
          type: object
          properties:
            data:
              type: array
              items:
                type: object
                properties:
                  id:
                    type: integer
                  title:
                    type: string
                  url:
                    type: string
                  site_link:
                    type: string
                  first_seen:
                    type: string
                  last_fetched:
                    type: string
                  headline_count:
                    type: integer
            meta:
              type: object
              properties:
                total:
                  type: integer
    """
    conn = get_connection()
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
    return jsonify({
        'data': feed_list,
        'meta': {'total': len(feed_list)},
    }), 200


@api.route('/feeds/<int:feed_id>', methods=['GET'])
def get_feed(feed_id):
    """
    Get a single feed by ID.
    ---
    tags:
      - Feeds
    parameters:
      - name: feed_id
        in: path
        type: integer
        required: true
        description: The feed's database ID
    responses:
      200:
        description: A single feed
        schema:
          type: object
          properties:
            data:
              type: object
              properties:
                id:
                  type: integer
                title:
                  type: string
                url:
                  type: string
                site_link:
                  type: string
                first_seen:
                  type: string
                last_fetched:
                  type: string
                headline_count:
                  type: integer
      404:
        description: Feed not found
        schema:
          type: object
          properties:
            error:
              type: string
    """
    conn = get_connection()
    row = conn.execute("""
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
        WHERE f.id = ?
        GROUP BY f.id
    """, (feed_id,)).fetchone()
    conn.close()

    if row is None:
        return jsonify({'error': 'feed not found'}), 404

    return jsonify({'data': dict(row)}), 200


# ── Runs ──────────────────────────────────────────────────────────────────────

@api.route('/runs', methods=['GET'])
def get_runs():
    """
    Get aggregator run history (last 50 runs).
    ---
    tags:
      - Runs
    responses:
      200:
        description: A list of recent aggregator runs
        schema:
          type: object
          properties:
            data:
              type: array
              items:
                type: object
                properties:
                  id:
                    type: integer
                  started_at:
                    type: string
                  finished_at:
                    type: string
                  elapsed_sec:
                    type: number
                  feeds_fetched:
                    type: integer
                  feeds_failed:
                    type: integer
                  articles_total:
                    type: integer
                  articles_new:
                    type: integer
            meta:
              type: object
              properties:
                total:
                  type: integer
    """
    conn = get_connection()
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
    return jsonify({
        'data': run_list,
        'meta': {'total': len(run_list)},
    }), 200


# ── Status & trigger ──────────────────────────────────────────────────────────

@api.route('/status', methods=['GET'])
def get_status():
    """
    Get the current aggregator status.
    ---
    tags:
      - Aggregator
    responses:
      200:
        description: Current run status
        schema:
          type: object
          properties:
            data:
              type: object
              properties:
                running:
                  type: boolean
    """
    return jsonify({'data': {'running': _is_running_ref[0]}}), 200


@api.route('/run', methods=['POST'])
def trigger_run():
    """
    Trigger an aggregator run.
    ---
    tags:
      - Aggregator
    responses:
      202:
        description: Run accepted (started or already running)
        schema:
          type: object
          properties:
            data:
              type: object
              properties:
                status:
                  type: string
                  enum: [started, already_running]
    """
    with _run_lock:
        if _is_running_ref[0]:
            return jsonify({'data': {'status': 'already_running'}}), 202
        _is_running_ref[0] = True

    # Import here to avoid circular import — app.py owns _run_aggregator
    from app import _run_aggregator
    import threading
    t = threading.Thread(target=_run_aggregator, daemon=True)
    t.start()

    return jsonify({'data': {'status': 'started'}}), 202