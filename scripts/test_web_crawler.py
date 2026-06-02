import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.services.web_crawler import CrawlerConfig, crawl_website


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Selenium web crawler against one or more URLs.")
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--visible", action="store_true")
    args = parser.parse_args()

    config = CrawlerConfig(
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        headless=not args.visible,
    )

    for url in args.urls:
        started = time.time()
        print(f"\n=== CRAWLING {url} ===")
        result = crawl_website(url, config=config)
        print(f"pages={len(result.pages)} chars={len(result.content)} duration={time.time() - started:.1f}s")
        for page in result.pages:
            print(f"\n--- {page.url}")
            if page.error:
                print(f"ERROR: {page.error}")
                continue
            print(f"title={page.title!r} text_chars={len(page.text)} links={len(page.links)}")
            print(page.text[:900].replace("\n", " ") + ("..." if len(page.text) > 900 else ""))


if __name__ == "__main__":
    main()
