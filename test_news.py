import pytest
import os
from news import read_url

def test_read_url(tmp_path):
    # Create a temporary feeds file
    feeds_file = tmp_path / "feeds.txt"
    feeds_file.write_text("https://feeds.bbci.co.uk/news/rss.xml\nhttps://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml\n")
    
    urls = read_url(str(feeds_file))
    assert len(urls) == 2

def test_read_url(tmp_path):
    feeds_file = tmp_path / "feeds.txt"
    feeds_file.write_text("# this is a comment\nhttps://feeds.bbci.co.uk/news/rss.xml\n")
    
    urls = read_url(str(feeds_file))
    assert len(urls) == 1

def test_file_not_found():
    result = read_url('nonexistent.txt')
    assert result is None


from unittest.mock import patch, MagicMock
from news import fetch_headline

def test_fetch(capsys):
    mock_feed = MagicMock()
    mock_feed.bozo = False
    mock_feed.entries = []
    mock_feed.feed.title = "Test Feed"
    
    with patch('feedparser.parse', return_value=mock_feed):
        fetch_headline('http://fake-url.com')
    
    captured = capsys.readouterr()
    assert "No entries." in captured.out

def test_fetch_stories(capsys):
    mock_entry = MagicMock()
    mock_entry.get.side_effect = lambda key, default='': {
        'title': 'Test Story',
        'link': 'http://example.com',
        'summary': 'Test summary'
    }.get(key, default)
    mock_entry.published_parsed = None
    mock_entry.updated_parsed = None

    mock_feed = MagicMock()
    mock_feed.bozo = False
    mock_feed.entries = [mock_entry]
    mock_feed.feed.title = "Test Feed"

    with patch('feedparser.parse', return_value=mock_feed):
        fetch_headline('http://fake-url.com')

    captured = capsys.readouterr()
    assert "Test Story" in captured.out