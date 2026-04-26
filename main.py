import argparse
import json
import random
import re
import time
from pathlib import Path
from playwright.sync_api import sync_playwright
from tqdm import tqdm

MAX_RETRIES = 3

def normalize_text(s: str) -> str:
    lines = [line.rstrip() for line in s.splitlines()]
    out = []
    blank = False
    for line in lines:
        if line.strip() == "":
            if not blank:
                out.append("")
            blank = True
        else:
            out.append(line)
            blank = False
    return "\n".join(out).strip()

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
    page.wait_for_selector("td.vbmenu_control[style], .pagenav a[href], [id^='post_message_']", timeout=15000)

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

EXTRACT_POSTS_JS = """
() => {
  const out = [];
  for (const el of document.querySelectorAll("[id^='post_message_']")) {
    const postId = el.id.replace("post_message_", "");
    let name = "";
    const span = document.querySelector(
      `#postmenu_${postId} > div:nth-child(1) > a:nth-child(1) > span:nth-child(1)`
    );
    if (span) {
      name = (span.textContent || "").trim();
    } else {
      const link = document.querySelector(
        `#postmenu_${postId} > div:nth-child(1) > a:nth-child(1)`
      );
      if (link) name = (link.textContent || "").trim();
    }
    if (!name) name = "Unknown";
    let date = null;
    const dateEl = document.querySelector(`#post${postId} .thead`);
    if (dateEl) date = (dateEl.textContent || "").trim();
    out.push({ postId, name, date, text: el.innerText });
  }
  return out;
}
"""

def scrape_page(page, base_url: str, page_num: int) -> list[dict]:
    url = get_page_url(base_url, page_num)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector("[id^='post_message_']", timeout=15000)

    raw = page.evaluate(EXTRACT_POSTS_JS)

    return [
        {
            "page": page_num,
            "postId": p["postId"],
            "name": p["name"],
            "date": p["date"],
            "text": normalize_text(p["text"]),
        }
        for p in raw
    ]

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

    def save():
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(kept_posts + new_posts, f, ensure_ascii=False, indent=2)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
        context.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("image", "font", "media")
            else route.continue_(),
        )
        page = context.new_page()

        total_pages = detect_total_pages(page, base_url)
        print(f"Detected {total_pages} pages")

        if start_page > total_pages:
            print("Nothing new to scrape")
            browser.close()
            return

        bar = tqdm(
            range(start_page, total_pages + 1),
            desc="Scraping",
            unit="pg",
            dynamic_ncols=True,
        )
        for page_num in bar:
            success = False
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    posts = scrape_page(page, base_url, page_num)
                    new_posts.extend(posts)
                    bar.set_postfix(posts=len(new_posts) + len(kept_posts))
                    success = True
                    break
                except Exception as e:
                    bar.write(f"Page {page_num} attempt {attempt}/{MAX_RETRIES} failed: {e}")
                    if attempt < MAX_RETRIES:
                        time.sleep(5)

            if not success:
                bar.write(f"WARNING: Skipping page {page_num} after {MAX_RETRIES} failed attempts")

            if page_num % 10 == 0:
                save()

            if page_num < total_pages:
                time.sleep(random.uniform(0.5, 1.5))
        bar.close()

        browser.close()

    save()
    all_posts = kept_posts + new_posts
    print(f"\nDone! {len(new_posts)} new posts scraped, {len(all_posts)} total saved to {output_file}")

if __name__ == "__main__":
    main()
