import feedparser
from datetime import datetime


def read_url(filename):
    try:
        with open(filename, 'r') as f:
            urls = [line.strip() for line in f 
                   if line.strip() and not line.strip().startswith('#')]
        return urls
    except FileNotFoundError:
        print(f"Error: Could not find {filename}")
        return


def fetch_headline(feed_url):
    feed = feedparser.parse(feed_url)

    if feed.bozo:
        print(f"feed has problems - {feed.get('bozo_exception', 'Unknown error')}")
    
    if hasattr(feed, 'feed') and hasattr(feed.feed, 'title'):
        print(f"\nFeed: {feed.feed.title}")
        if hasattr(feed.feed, 'subtitle'):
            print(f"Description: {feed.feed.subtitle}")
    
    if not feed.entries:
        print("No entries.")
        return
    
    print(f"\nFound {len(feed.entries)} stories:\n")
    
    for idx, entry in enumerate(feed.entries, 1):
        title = entry.get('title', 'No title')

        published = 'Unknown date'
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            published = datetime(*entry.published_parsed[:6]).strftime('%Y.%m.%d %H:%M')
        elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
            published = datetime(*entry.updated_parsed[:6]).strftime('%Y.%m.%d %H:%M')
        
        link = entry.get('link', 'No link')
        
        summary = entry.get('summary', entry.get('description', ''))
        if summary:

            import re
            summary = re.sub('<[^<]+?>', '', summary)
            summary = summary[:150] + '...' if len(summary) > 150 else summary
        
        print(f"{idx}. {title}")
        print(f"   Date: {published}")
        if summary:
            print(f"   Summary: {summary}")
        print(f"   Link: {link}")
        print()


def main():
    feeds_file = 'feeds.txt'
    print("="*80)
    
    feed_urls = read_url(feeds_file)
    
    if not feed_urls:
        print(f"\nNo RSS feeds found in {feeds_file}")
        print(f"Please add RSS feed URLs to {feeds_file} (one per line)")
        return
    
    print(f"\nFound {len(feed_urls)} feed(s) to process\n")
    
    for feed_url in feed_urls:
        fetch_headline(feed_url)
    
    print(f"\n{'='*80}")
    print("Summary complete!")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()