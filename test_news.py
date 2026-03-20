import pytest
import sqlite3
from unittest.mock import patch, MagicMock
from news_at_12 import (
    strip_html,
    url_hash,
    parse_date,
    pretty_date,
    read_urls,
    get_db,
    upsert_feed,
    upsert_headline,
    store_feed,
    fetch_feed,
)

def test_strip_html_cleanup():
    assert strip_html('<p>Hello <b>World</b></p>') == 'Hello World'

def test_strip_html_mtcheck():
    assert strip_html('') == ''

def test_strip_html_none():
    assert strip_html(None) == ''

def test_url_hash_cons_check():
    assert url_hash('https://example.com') == url_hash('https://example.com')

def test_url_hash_unique_check():
    assert url_hash('https://example.com') != url_hash('https://other.com')

def test_parse_date_pub_check():
    mock_entry = MagicMock()
    mock_entry.published_parsed = (2026, 3, 20, 12, 0, 0)
    mock_entry.updated_parsed = None
    assert parse_date(mock_entry) == '2026-03-20T12:00:00'

def test_parse_update_date_check():
    mock_entry = MagicMock()
    mock_entry.published_parsed = None
    mock_entry.updated_parsed = (2026, 3, 20, 12, 0, 0)
    assert parse_date(mock_entry) == '2026-03-20T12:00:00'

def test_parse_date_returns_none():
    mock_entry = MagicMock()
    mock_entry.published_parsed = None
    mock_entry.updated_parsed = None
    assert parse_date(mock_entry) is None

def test_pretty_date_formats_correctly():
    result = pretty_date('2026-03-20T12:00:00')
    assert result == 'March 20, 2026  12:00'

def test_pretty_date_none():
    assert pretty_date(None) == 'Date unknown'

def test_pretty_date_invalid():
    assert pretty_date('not-a-date') == 'not-a-date'


def test_read_urls_returns_urls(tmp_path):
    feeds_file = tmp_path / 'feeds.txt'
    feeds_file.write_text('https://feeds.bbci.co.uk/news/rss.xml\nhttps://example.com/rss\n')
    urls = read_urls(str(feeds_file))
    assert len(urls) == 2

def test_read_urls_ignores_comments(tmp_path):
    feeds_file = tmp_path / 'feeds.txt'
    feeds_file.write_text('# this is a comment\nhttps://feeds.bbci.co.uk/news/rss.xml\n')
    urls = read_urls(str(feeds_file))
    assert len(urls) == 1

def test_read_urls_ignores_blank_lines(tmp_path):
    feeds_file = tmp_path / 'feeds.txt'
    feeds_file.write_text('\nhttps://example.com/rss\n\n')
    urls = read_urls(str(feeds_file))
    assert len(urls) == 1

def test_read_urls_file_not_found():
    result = read_urls('nonexistent.txt')
    assert result == []


@pytest.fixture
def db():
    import news_at_12
    original = news_at_12.DB_FILE
    news_at_12.DB_FILE = ':memory:'
    conn = get_db(':memory:')
    yield conn
    conn.close()
    news_at_12.DB_FILE = original

def test_get_db_creates_tables(db):
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = [t['name'] for t in tables]
    assert 'feeds' in table_names
    assert 'headlines' in table_names
    assert 'runs' in table_names

def test_upsert_feed_inserts(db):
    feed_id = upsert_feed(db, 'https://example.com/rss', 'Example Feed', 'https://example.com')
    assert feed_id is not None
    row = db.execute('SELECT * FROM feeds WHERE id = ?', (feed_id,)).fetchone()
    assert row['title'] == 'Example Feed'

def test_upsert_feed_updates_on_conflict(db):
    upsert_feed(db, 'https://example.com/rss', 'Old Title', 'https://example.com')
    upsert_feed(db, 'https://example.com/rss', 'New Title', 'https://example.com')
    rows = db.execute('SELECT * FROM feeds').fetchall()
    assert len(rows) == 1
    assert rows[0]['title'] == 'New Title'

def test_upsert_headline_inserts_new(db):
    feed_id = upsert_feed(db, 'https://example.com/rss', 'Test Feed', 'https://example.com')
    row, is_new = upsert_headline(db, feed_id, 'Test Title', 'https://example.com/article1', None, 'Summary')
    assert is_new is True
    assert row['title'] == 'Test Title'
    assert row['seen_count'] == 1

def test_upsert_headline_deduplicates(db):
    feed_id = upsert_feed(db, 'https://example.com/rss', 'Test Feed', 'https://example.com')
    upsert_headline(db, feed_id, 'Test Title', 'https://example.com/article1', None, 'Summary')
    row, is_new = upsert_headline(db, feed_id, 'Test Title', 'https://example.com/article1', None, 'Summary')
    assert is_new is False
    assert row['seen_count'] == 2

def test_fetch_feed_returns_data():
    mock_entry = MagicMock()
    mock_entry.get.side_effect = lambda key, default='': {
        'title': 'Test Story',
        'link': 'https://example.com/story',
        'summary': 'A test summary',
    }.get(key, default)
    mock_entry.published_parsed = (2026, 3, 20, 12, 0, 0)
    mock_entry.updated_parsed = None

    mock_feed = MagicMock()
    mock_feed.bozo = False
    mock_feed.entries = [mock_entry]
    mock_feed.feed.title = 'Test Feed'
    mock_feed.feed.link = 'https://example.com'

    with patch('feedparser.parse', return_value=mock_feed):
        result = fetch_feed('https://example.com/rss')

    assert result is not None
    assert result['feed_title'] == 'Test Feed'
    assert len(result['raw_entries']) == 1
    assert result['raw_entries'][0]['title'] == 'Test Story'

def test_fetch_feed_skips_entries_without_url():
    mock_entry = MagicMock()
    mock_entry.get.side_effect = lambda key, default='': {
        'title': 'No Link Story',
        'link': '',  # empty URL — should be skipped
    }.get(key, default)
    mock_entry.published_parsed = None
    mock_entry.updated_parsed = None

    mock_feed = MagicMock()
    mock_feed.bozo = False
    mock_feed.entries = [mock_entry]
    mock_feed.feed.title = 'Test Feed'
    mock_feed.feed.link = 'https://example.com'

    with patch('feedparser.parse', return_value=mock_feed):
        result = fetch_feed('https://example.com/rss')

    assert result['raw_entries'] == []

def test_fetch_feed_handles_exception():
    with patch('feedparser.parse', side_effect=Exception('Network error')):
        result = fetch_feed('https://example.com/rss')
    assert result is None