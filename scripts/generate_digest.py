#!/usr/bin/env python3
"""
generate_digest.py — Weekly Marketing Digest Generator

Runs via GitHub Actions every Monday. Fetches RSS feeds, queries Perplexity
and Claude APIs, generates an HTML digest file, and updates the digest index.
"""

import os
import sys
import time
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests
from anthropic import Anthropic
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")

FEEDS = [
    "https://www.adweek.com/feed/",
    "https://marketingland.com/feed",
    "https://www.businessoffashion.com/feed/",
    "http://feeds.harvardbusiness.org/harvardbusiness",
]

CZECH_MONTHS = {
    1: "ledna", 2: "února", 3: "března", 4: "dubna",
    5: "května", 6: "června", 7: "července", 8: "srpna",
    9: "září", 10: "října", 11: "listopadu", 12: "prosince",
}

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent
DIGEST_DIR = REPO_ROOT / "marketing-digest"
MAIN_INDEX = REPO_ROOT / "index.html"

SHARED_CSS = """    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #FAF8F5; --bg-card: #F0EAE0; --bg-card-hover: #E8DDD0;
      --text: #1C1410; --text-muted: #7A6A5A; --accent: #C8A96E;
      --accent-dark: #A8893E; --border: #E2D8CC; --white: #FFFFFF;
    }
    html { scroll-behavior: smooth; }
    body { background-color: var(--bg); color: var(--text); font-family: 'Inter', sans-serif; font-weight: 400; line-height: 1.6; -webkit-font-smoothing: antialiased; }
    nav { position: fixed; top: 0; left: 0; right: 0; z-index: 100; padding: 1.25rem 2rem; display: flex; align-items: center; justify-content: space-between; background: rgba(250,248,245,0.85); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); border-bottom: 1px solid var(--border); }
    .nav-logo { font-family: 'Playfair Display', serif; font-style: italic; font-size: 1.15rem; color: var(--text); text-decoration: none; letter-spacing: 0.01em; }
    .nav-links { display: flex; gap: 1.75rem; list-style: none; }
    .nav-links a { font-size: 0.8rem; font-weight: 500; letter-spacing: 0.1em; text-transform: uppercase; color: var(--text-muted); text-decoration: none; transition: color 0.2s; }
    .nav-links a:hover { color: var(--accent); }
    .section-label { font-size: 0.72rem; font-weight: 600; letter-spacing: 0.18em; text-transform: uppercase; color: var(--accent); margin-bottom: 0.75rem; }
    .section-title { font-family: 'Playfair Display', serif; font-size: clamp(2rem, 5vw, 3rem); font-weight: 700; line-height: 1.15; margin-bottom: 2.5rem; }
    .section-title em { font-style: italic; font-weight: 400; color: var(--accent); }
    .section-inner { max-width: 900px; margin: 0 auto; }
    .digest-list { display: flex; flex-direction: column; gap: 0.75rem; }
    .digest-item { display: flex; align-items: center; gap: 1.5rem; padding: 1.1rem 1.5rem; background: var(--bg-card); border-radius: 12px; border: 1px solid var(--border); text-decoration: none; color: var(--text); transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease; }
    .digest-item:hover { transform: translateX(4px); box-shadow: 0 4px 16px rgba(200,169,110,0.12); border-color: var(--accent); }
    .digest-date { font-size: 0.75rem; font-weight: 600; letter-spacing: 0.08em; color: var(--accent); text-transform: uppercase; white-space: nowrap; flex-shrink: 0; }
    .digest-title { flex: 1; font-size: 0.9rem; color: var(--text-muted); }
    .digest-arrow { color: var(--accent); flex-shrink: 0; }
    .digest-coming-soon { font-size: 0.85rem; color: var(--text-muted); font-style: italic; padding: 1.5rem; text-align: center; background: var(--bg-card); border-radius: 12px; border: 1px dashed var(--border); }
    footer { text-align: center; padding: 2.5rem 2rem; border-top: 1px solid var(--border); font-size: 0.78rem; color: var(--text-muted); letter-spacing: 0.04em; }
    footer a { color: var(--accent); text-decoration: none; }
    footer a:hover { text-decoration: underline; }
    @media (max-width: 700px) { nav { padding: 1rem 1.25rem; } .nav-links { gap: 1.25rem; } .digest-item { flex-direction: column; align-items: flex-start; gap: 0.3rem; } }"""


# ─── Helpers ─────────────────────────────────────────────────────────────────

def format_date_czech(dt: datetime) -> str:
    return f"{dt.day}. {CZECH_MONTHS[dt.month]} {dt.year}"


def parse_entry_date(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)
            except Exception:
                pass
    return None


# ─── Phase A: RSS Feeds ───────────────────────────────────────────────────────

def fetch_rss_articles(days: int = 7) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    articles = []

    for feed_url in FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            count = 0
            for entry in feed.entries:
                pub_date = parse_entry_date(entry)
                if pub_date and pub_date < cutoff:
                    continue

                summary = ""
                if hasattr(entry, "summary"):
                    soup = BeautifulSoup(entry.summary, "html.parser")
                    summary = soup.get_text()[:300].strip()

                articles.append({
                    "title": entry.get("title", "").strip(),
                    "link": entry.get("link", ""),
                    "summary": summary,
                    "source": feed.feed.get("title", feed_url),
                    "date": pub_date.strftime("%Y-%m-%d") if pub_date else "",
                })
                count += 1
            log.info(f"RSS OK ({count} articles): {feed_url}")
        except Exception as e:
            log.warning(f"RSS SKIP: {feed_url} — {e}")

    log.info(f"Total RSS articles: {len(articles)}")
    return articles


# ─── Phase B: Perplexity ──────────────────────────────────────────────────────

def fetch_perplexity_insights() -> str:
    if not PERPLEXITY_API_KEY:
        log.warning("PERPLEXITY_API_KEY missing — skipping")
        return ""
    try:
        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.1-sonar-large-128k-online",
                "messages": [{
                    "role": "user",
                    "content": (
                        "What are the most important marketing news, trends, viral ads "
                        "and campaigns from the past 7 days? Include specific brand names, "
                        "campaign names and explain why they matter. Be specific and thorough."
                    ),
                }],
            },
            timeout=45,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        log.info("Perplexity OK")
        return content
    except Exception as e:
        log.warning(f"Perplexity FAILED: {e}")
        return ""


# ─── Phase C: Claude ──────────────────────────────────────────────────────────

def generate_digest_html(articles: list[dict], perplexity_text: str) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    articles_text = "\n".join(
        f"- [{a['date']}] {a['title']} | {a['source']}\n  {a['summary']}"
        for a in articles[:30]
    ) or "(žádné RSS články nebyly dostupné)"

    perplexity_block = perplexity_text or "(Perplexity nebyl dostupný)"

    prompt = f"""Napiš weekly marketing digest v češtině, 800–1200 slov.

RSS ČLÁNKY Z TOHOTO TÝDNE:
{articles_text}

PERPLEXITY INSIGHTS (čerstvé marketingové dění):
{perplexity_block}

Výstup musí být čistý HTML fragment — BEZ tagů <html>, <head>, <body>.
Struktura: přesně 4 sekce s <h2> nadpisy:
  1. Top novinky týdne
  2. Virální reklamy & kampaně
  3. Trendy & insights
  4. Zajímavosti

Pro každou položku: <strong>název</strong>, 2–4 věty popis, zdroj/odkaz kde relevantní.
Piš přirozeně, osobně — jako UGC creatorka zaměřená na marketing.
Pouze HTML tagy: <h2>, <p>, <strong>, <em>, <ul>, <li>, <a href="...">."""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    content = response.content[0].text
    log.info("Claude OK — digest generated")
    return content


# ─── Phase D: Save files ──────────────────────────────────────────────────────

def build_digest_page(date_str: str, date_display: str, content_html: str) -> Path:
    DIGEST_DIR.mkdir(exist_ok=True)
    html = f"""<!DOCTYPE html>
<html lang="cs">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="description" content="Marketing Digest {date_display} · Arina Isakova" />
  <title>Digest {date_display} · Arina Isakova</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;1,400;1,700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet" />
  <style>
{SHARED_CSS}
    .digest-page {{ max-width: 720px; margin: 0 auto; padding: 7rem 2rem 5rem; }}
    .digest-page h2 {{ font-family: 'Playfair Display', serif; font-size: 1.45rem; font-weight: 700; margin: 2.5rem 0 1rem; color: var(--text); border-bottom: 1px solid var(--border); padding-bottom: 0.5rem; }}
    .digest-page p {{ color: var(--text-muted); line-height: 1.85; margin-bottom: 1rem; font-size: 0.95rem; }}
    .digest-page strong {{ color: var(--text); }}
    .digest-page ul {{ list-style: none; padding: 0; margin-bottom: 1rem; }}
    .digest-page li {{ padding: 0.3rem 0; color: var(--text-muted); line-height: 1.75; font-size: 0.95rem; }}
    .digest-page li::before {{ content: "·"; color: var(--accent); margin-right: 0.6rem; font-weight: bold; }}
    .digest-page a {{ color: var(--accent); text-decoration: none; }}
    .digest-page a:hover {{ text-decoration: underline; }}
    .digest-meta {{ font-size: 0.8rem; color: var(--text-muted); letter-spacing: 0.06em; margin-bottom: 3rem; padding-bottom: 2rem; border-bottom: 1px solid var(--border); }}
    @media (max-width: 700px) {{ .digest-page {{ padding: 6rem 1.25rem 4rem; }} }}
  </style>
</head>
<body>
  <nav>
    <a href="../index.html" class="nav-logo">arina</a>
    <ul class="nav-links">
      <li><a href="../index.html#digest">← Digest</a></li>
    </ul>
  </nav>

  <main class="digest-page">
    <p class="section-label">Weekly Digest</p>
    <h1 class="section-title">Marketing <em>týdne</em></h1>
    <p class="digest-meta">{date_display}</p>
    {content_html}
  </main>

  <footer>
    <p>&copy; 2026 Arina Isakova &nbsp;·&nbsp; <a href="../index.html">Zpět na hlavní stránku</a></p>
  </footer>
</body>
</html>"""

    out_path = DIGEST_DIR / f"{date_str}.html"
    out_path.write_text(html, encoding="utf-8")
    log.info(f"Saved: {out_path}")
    return out_path


def rebuild_index_page() -> list[dict]:
    """List all digest HTML files, regenerate index.html, return sorted list."""
    DIGEST_DIR.mkdir(exist_ok=True)
    digest_files = sorted(
        [f for f in DIGEST_DIR.glob("????-??-??.html")],
        reverse=True,
    )

    digests = []
    for f in digest_files:
        date_str = f.stem
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            date_display = format_date_czech(dt)
        except ValueError:
            date_display = date_str
        digests.append({"filename": f.name, "date_str": date_str, "date_display": date_display})

    if digests:
        items_html = "\n".join(
            f'      <a href="{d["filename"]}" class="digest-item">\n'
            f'        <span class="digest-date">{d["date_display"]}</span>\n'
            f'        <span class="digest-title">Marketing Digest</span>\n'
            f'        <span class="digest-arrow">→</span>\n'
            f'      </a>'
            for d in digests
        )
        list_html = f'    <div class="digest-list">\n{items_html}\n    </div>'
    else:
        list_html = '    <p class="digest-coming-soon">Zatím žádné digesty — první vyjde příští pondělí.</p>'

    html = f"""<!DOCTYPE html>
<html lang="cs">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="description" content="Marketing Digest archiv · Arina Isakova" />
  <title>Marketing Digest · Arina Isakova</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;1,400;1,700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet" />
  <style>
{SHARED_CSS}
    section {{ padding: 8rem 2rem 6rem; }}
  </style>
</head>
<body>
  <nav>
    <a href="../index.html" class="nav-logo">arina</a>
    <ul class="nav-links">
      <li><a href="../index.html">← Zpět</a></li>
    </ul>
  </nav>

  <section>
    <div class="section-inner">
      <p class="section-label">Weekly Digest</p>
      <h1 class="section-title">Marketing <em>Digest</em></h1>
      <p style="color: var(--text-muted); margin-bottom: 3rem; max-width: 520px; font-size: 0.95rem; line-height: 1.8;">
        Týdenní přehled z marketingového světa — novinky, virální kampaně, trendy a zajímavosti. Každé pondělí.
      </p>
{list_html}
    </div>
  </section>

  <footer>
    <p>&copy; 2026 Arina Isakova &nbsp;·&nbsp; <a href="../index.html">Hlavní stránka</a></p>
  </footer>
</body>
</html>"""

    idx_path = DIGEST_DIR / "index.html"
    idx_path.write_text(html, encoding="utf-8")
    log.info(f"Index rebuilt: {idx_path} ({len(digests)} digests)")
    return digests


def update_main_index(digests: list[dict]) -> None:
    """Update the DIGEST_LIST section in the main index.html."""
    if not MAIN_INDEX.exists():
        log.warning("Main index.html not found, skipping")
        return

    content = MAIN_INDEX.read_text(encoding="utf-8")
    start_marker = "<!-- DIGEST_LIST_START -->"
    end_marker = "<!-- DIGEST_LIST_END -->"

    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)
    if start_idx == -1 or end_idx == -1:
        log.warning("Digest markers not found in main index.html")
        return

    recent = digests[:3]
    if recent:
        items = "\n".join(
            f'        <a href="marketing-digest/{d["filename"]}" class="digest-item">\n'
            f'          <span class="digest-date">{d["date_display"]}</span>\n'
            f'          <span class="digest-title">Marketing Digest</span>\n'
            f'          <span class="digest-arrow">→</span>\n'
            f'        </a>'
            for d in recent
        )
        new_block = (
            f"{start_marker}\n"
            f'      <div class="digest-list">\n'
            f'{items}\n'
            f'      </div>\n'
            f"      {end_marker}"
        )
    else:
        new_block = (
            f"{start_marker}\n"
            f'      <div class="digest-list">\n'
            f'        <p class="digest-coming-soon">První digest vychází brzy — sleduj a nezmeškej.</p>\n'
            f'      </div>\n'
            f"      {end_marker}"
        )

    new_content = content[:start_idx] + new_block + content[end_idx + len(end_marker):]
    MAIN_INDEX.write_text(new_content, encoding="utf-8")
    log.info("Main index.html updated")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    today = datetime.now(timezone.utc)
    date_str = today.strftime("%Y-%m-%d")
    date_display = format_date_czech(today)
    log.info(f"=== Generating digest for {date_str} ===")

    # A: RSS
    articles = fetch_rss_articles(days=7)

    # B: Perplexity (optional — failure is non-fatal)
    perplexity_text = fetch_perplexity_insights()

    # C: Claude (required — exit on failure)
    try:
        digest_html = generate_digest_html(articles, perplexity_text)
    except Exception as e:
        log.error(f"Claude generation failed: {e}")
        sys.exit(1)

    # D: Save files and update indexes
    build_digest_page(date_str, date_display, digest_html)
    digests = rebuild_index_page()
    update_main_index(digests)

    log.info(f"=== Done! Digest {date_str} created. ===")


if __name__ == "__main__":
    main()
