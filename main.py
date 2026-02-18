import argparse
import json
import random
import re
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

MAX_RETRIES = 3

def parse_base_url(thread_url: str) -> str:
    url = re.sub(r"-\d+\.html$", "", thread_url)
    url = re.sub(r"\.html$", "", url)
    return url

def get_page_url(base_url: str, page_num: int) -> str:
    if page_num == 1:
        return f"{base_url}.html"
    return f"{base_url}-{page_num}.html"

def detect_total_pages(page, base_url: str) -> int:
    url = get_page_url(base_url, 1)
    page.goto(url, wait_until="domcontentloaded")

    # "Page X of Y" element
    page_of = page.query_selector("td.vbmenu_control[style]")
    if page_of:
        match = re.search(r"Page\s+\d+\s+of\s+(\d+)", page_of.text_content())
        if match:
            return int(match.group(1))

    # Fallback: highest page number from pagination links
    last_links = page.query_selector_all(".pagenav a[href]")
    max_page = 1
    for link in last_links:
        href = link.get_attribute("href") or ""
        match = re.search(r"-(\d+)\.html", href)
        if match:
            max_page = max(max_page, int(match.group(1)))

    if max_page > 1:
        return max_page

    return 1  # single-page thread

def scrape_page(page, base_url: str, page_num: int) -> list[dict]:
    url = get_page_url(base_url, page_num)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector("[id^='post_message_']", timeout=15000)

    post_elements = page.query_selector_all("[id^='post_message_']")
    posts = []

    for el in post_elements:
        el_id = el.get_attribute("id")
        post_id = el_id.replace("post_message_", "")

        # Username: styled <span> or plain <a> fallback
        name = None
        name_el = page.query_selector(f"#postmenu_{post_id} > div:nth-child(1) > a:nth-child(1) > span:nth-child(1)")
        if name_el:
            name = name_el.text_content().strip()
        else:
            name_el = page.query_selector(f"#postmenu_{post_id} > div:nth-child(1) > a:nth-child(1)")
            if name_el:
                name = name_el.text_content().strip()

        if not name:
            name = "Unknown"

        date = None
        date_el = page.query_selector(f"#post{post_id} .thead")
        if date_el:
            date = date_el.text_content().strip()

        text = el.text_content().strip()

        posts.append({
            "page": page_num,
            "postId": post_id,
            "name": name,
            "date": date,
            "text": text,
        })

    return posts

def load_existing_posts(output_file: Path) -> list[dict]:
    if output_file.exists():
        with open(output_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def main():
    parser = argparse.ArgumentParser(
        description="Scrape all posts from an UnknownCheats forum thread into JSON."
    )
    parser.add_argument(
        "url",
        help="URL of any page in the thread (e.g. https://www.unknowncheats.me/forum/section/12345-thread-title.html)",
    )
    parser.add_argument(
        "--output", "-o",
        default="output/posts.json",
        help="Output JSON file path (default: output/posts.json)",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run browser in headless mode (default: headless)",
    )
    args = parser.parse_args()

    base_url = parse_base_url(args.url)
    output_file = Path(args.output)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Resume: re-scrape from the last page (may have been partial)
    existing_posts = load_existing_posts(output_file)
    if existing_posts:
        last_scraped_page = max(p["page"] for p in existing_posts)
        kept_posts = [p for p in existing_posts if p["page"] < last_scraped_page]
        start_page = last_scraped_page
        print(f"Loaded {len(existing_posts)} existing posts (pages 1–{last_scraped_page})")
        print(f"Keeping {len(kept_posts)} posts from pages 1–{last_scraped_page - 1}, re-scraping from page {start_page}")
    else:
        kept_posts = []
        start_page = 1
        print("No existing data, scraping from scratch")

    new_posts = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        total_pages = detect_total_pages(page, base_url)
        print(f"Detected {total_pages} pages")

        if start_page > total_pages:
            print("Nothing new to scrape")
            browser.close()
            return

        for page_num in range(start_page, total_pages + 1):
            success = False
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    posts = scrape_page(page, base_url, page_num)
                    new_posts.extend(posts)
                    print(f"Page {page_num}/{total_pages} — {len(posts)} posts extracted")
                    success = True
                    break
                except Exception as e:
                    print(f"Page {page_num} attempt {attempt}/{MAX_RETRIES} failed: {e}")
                    if attempt < MAX_RETRIES:
                        time.sleep(5)

            if not success:
                print(f"WARNING: Skipping page {page_num} after {MAX_RETRIES} failed attempts")

            if page_num < total_pages:
                delay = random.uniform(3, 5)
                time.sleep(delay)

        browser.close()

    all_posts = kept_posts + new_posts
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_posts, f, ensure_ascii=False, indent=2)

    print(f"\nDone! {len(new_posts)} new posts scraped, {len(all_posts)} total saved to {output_file}")

if __name__ == "__main__":
    main()