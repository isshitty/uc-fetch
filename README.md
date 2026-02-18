# uc-fetch

Scrape all posts from an UnknownCheats forum thread into JSON for AI consumption.

Navigates every page of an UnknownCheats thread using Playwright, extracts each post (author, date, text) and saves the results as a single JSON file. Designed to feed forum threads to LLMs for analysis.

## Install

```bash
pip install -r requirements.txt
playwright install chromium
```

## Usage

```bash
python scraper.py <thread-url>
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--output`, `-o` | `output/posts.json` | Output file path |
| `--headless` / `--no-headless` | `--headless` | Show or hide the browser |

Examples:

```bash
# Basic usage
python scraper.py "https://www.unknowncheats.me/forum/section/12345-thread-title.html"

# Custom output path
python scraper.py "https://www.unknowncheats.me/forum/section/12345-thread-title.html" -o data/thread.json

# Show the browser window
python scraper.py "https://www.unknowncheats.me/forum/section/12345-thread-title.html" --no-headless
```

The scraper resumes from where it left off - re-run the same command to pick up new pages.

## Output format

```json
[
  {
    "page": 1,
    "postId": "1234567",
    "name": "username",
    "date": "1st January 2025, 12:00 PM",
    "text": "Post content here..."
  }
]
```
