"""
Huse Infinity — Wix blog extraction v2
---------------------------------------
Improvements over v1:
- Converts HTML formatting to Markdown (bold, italic, blockquote, headings, lists)
- Extracts featured image URL, publish date, read time from page metadata
- Extracts categories/tags from og: and JSON-LD as well as link hrefs
- Calculates word count
- Accepts a TARGET_URLS list so specific posts can be (re)extracted
- Writes manifest_full.json with complete metadata per post
"""

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

BASE = "https://www.huseinfinity.com"
OUT_DIR = Path("posts")
OUT_DIR.mkdir(exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; HuseInfinityMigrationBot/1.0)"}

# All known post URLs (seeds + manually added)
TARGET_URLS = [
    "/post/free-before-paid-the-decision-sequence-non-profits-should-use-before-any-technology-investment",
    "/post/impact-measurement-ideas-that-matter",
    "/post/investing-in-women-smarter",
    "/post/digital-divide-in-the-future-of-work-asian-context",
    "/post/small-moves-big-impact-practical-talent-development-for-resource-constrained-non-profits",
    "/post/a-new-chapter-in-social-impact-investing",
    "/post/driving-innovation-for-social-impact",
    "/post/investing-in-women-s-leadership-strengthening-the-foundations-of-change",
    "/post/growing-together-strengthening-the-foundations-of-enduring-change-in-asia",
    "/post/making-a-difference-what-we-re-learning-about-supporting-meaningful-change",
    "/post/from-intention-to-action-a-practical-guide-to-building-strategic-capacity",
    "/post/beyond-the-buzzword-3-ways-technology-is-tackling-asia-s-unique-educational-divides",
    "/post/build-apps-that-build-futures-in-asia",
    "/post/governance-for-non-profits-the-foundation-that-makes-your-impact-last",
    "/post/more-than-a-handout-asia-impact-investment-fund-for-a-better-future",
    "/post/rethinking-governance-it-s-simpler-and-more-valuable-than-you-think",
    "/post/small-moves-big-impact-practical-talent-development-for-resource-constrained-non-profits",
    "/post/talent-management-challenges-of-non-profits-and-ideas-of-solving-them",
    "/post/the-250-million-question-how-to-prepare-for-game-changing-opportunities",
    "/post/the-importance-of-corporate-governance-in-non-profit-organizations-for-sustainable-impact",
    "/post/why-talent-management-matters-for-non-profits",
    # 4 manually added by user
    "/post/reimagining-better-future-of-work",
    "/post/transforming-lives-through-tech-impact-investing",
    "/post/8-practical-talent-development-ideas-for-resource-constrained-non-profits",
    "/post/the-firefighting-trap-when-strategy-becomes-tomorrow-s-problem",
]

# Deduplicate while preserving order
seen = set()
UNIQUE_URLS = []
for u in TARGET_URLS:
    if u not in seen:
        seen.add(u)
        UNIQUE_URLS.append(u)


def fetch(url: str) -> BeautifulSoup | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=25)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        print(f"  ! failed: {url} ({e})")
        return None


def node_to_md(node) -> str:
    """Recursively convert a BeautifulSoup node to Markdown text."""
    if isinstance(node, NavigableString):
        return str(node)

    tag = node.name
    children_md = "".join(node_to_md(c) for c in node.children)

    if tag in ("strong", "b"):
        inner = children_md.strip()
        return f"**{inner}**" if inner else ""
    if tag in ("em", "i"):
        inner = children_md.strip()
        return f"*{inner}*" if inner else ""
    if tag == "u":
        inner = children_md.strip()
        return f"<u>{inner}</u>" if inner else ""
    if tag == "a":
        href = node.get("href", "")
        inner = children_md.strip()
        if href and inner:
            return f"[{inner}]({href})"
        return inner
    if tag == "br":
        return "  \n"
    # span/div: just pass through children
    if tag in ("span", "div"):
        return children_md

    return children_md


def block_to_md(block) -> str:
    """Convert a block-level element (p, h2, h3, blockquote, li) to a Markdown string."""
    tag = block.name
    inner = node_to_md(block).strip()
    if not inner:
        return ""

    if tag == "h1":
        return f"# {inner}"
    if tag == "h2":
        return f"## {inner}"
    if tag == "h3":
        return f"### {inner}"
    if tag == "h4":
        return f"#### {inner}"
    if tag == "blockquote":
        lines = inner.splitlines()
        return "\n".join(f"> {l}" for l in lines)
    if tag == "li":
        return f"- {inner}"
    return inner  # p, default


def extract_post(full_url: str) -> dict | None:
    soup = fetch(full_url)
    if soup is None:
        return None

    # --- Metadata from og: / JSON-LD ---
    def og(prop):
        m = soup.find("meta", property=prop)
        return m["content"].strip() if m and m.get("content") else ""

    def meta_name(name):
        m = soup.find("meta", attrs={"name": name})
        return m["content"].strip() if m and m.get("content") else ""

    title = og("og:title") or (soup.find("h1") or {}).get_text(strip=True) or ""
    meta_description = og("og:description") or meta_name("description")
    featured_image = og("og:image")
    publish_date = ""

    # JSON-LD for datePublished
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(s.string or "")
            if isinstance(d, dict):
                publish_date = publish_date or d.get("datePublished", "")[:10]
                featured_image = featured_image or d.get("image", {}).get("url", "") if isinstance(d.get("image"), dict) else featured_image
        except Exception:
            pass

    # article:published_time fallback
    if not publish_date:
        apt = soup.find("meta", property="article:published_time")
        if apt and apt.get("content"):
            publish_date = apt["content"][:10]

    # Read time (Wix renders it in a span)
    read_time_el = soup.find("span", {"data-hook": "time-to-read"})
    read_time = read_time_el.get_text(strip=True) if read_time_el else ""

    # --- Article body → Markdown ---
    article = soup.find("article") or soup.find("main") or soup.body

    # --- Categories and Tags (scoped to article to exclude nav links) ---
    scope = article or soup
    categories = sorted({
        a.get_text(strip=True)
        for a in scope.find_all("a", href=True)
        if "/white-papers/categories/" in a["href"]
    })
    tags = sorted({
        a.get_text(strip=True)
        for a in scope.find_all("a", href=True)
        if "/white-papers/tags/" in a["href"]
    })

    md_blocks = []
    if article:
        for block in article.find_all(["p", "h1", "h2", "h3", "h4", "blockquote", "li"]):
            md = block_to_md(block)
            if md:
                md_blocks.append(md)

    body_md = "\n\n".join(md_blocks)

    # Word count (rough)
    word_count = len(re.findall(r"\w+", body_md))

    return {
        "legacyUrl": full_url,
        "title": title,
        "publishDate": publish_date,
        "metaDescription": meta_description,
        "featuredImage": featured_image,
        "readTime": read_time,
        "categories": categories,
        "tags": tags,
        "body": body_md,
        "wordCount": word_count,
    }


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def write_mdx(post: dict, post_id: str):
    slug = slugify(post["title"]) if post["title"] else slugify(post["legacyUrl"].split("/post/")[-1])
    fm_lines = [
        "---",
        f'id: "{post_id}"',
        f'title: {json.dumps(post["title"])}',
        f'slug: "{slug}"',
        f'legacyUrl: "{post["legacyUrl"]}"',
        f'publishDate: "{post["publishDate"]}"',
        f'featuredImage: {json.dumps(post["featuredImage"])}',
        f'readTime: "{post["readTime"]}"',
        f'categories: {json.dumps(post["categories"])}',
        f'tags: {json.dumps(post["tags"])}',
        f'wordCount: {post["wordCount"]}',
        f'seo:',
        f'  metaDescription: {json.dumps(post["metaDescription"])}',
        f'migration:',
        f'  extractionStatus: "Extracted — review before publish"',
        f'  extractedAt: "2026-08-11"',
        "---",
        "",
    ]
    frontmatter = "\n".join(fm_lines) + "\n"
    out_path = OUT_DIR / f"{slug}.mdx"
    out_path.write_text(frontmatter + post["body"], encoding="utf-8")
    print(f"  -> wrote {out_path.name} ({post['wordCount']} words)")
    return slug


def main():
    print(f"Extracting {len(UNIQUE_URLS)} posts...\n")
    manifest = []
    failed = []

    for i, path in enumerate(UNIQUE_URLS, start=1):
        full_url = urljoin(BASE, path)
        post_id = f"P{i:03d}"
        print(f"[{i}/{len(UNIQUE_URLS)}] {full_url}")
        post = extract_post(full_url)
        if post:
            slug = write_mdx(post, post_id)
            manifest.append({
                "id": post_id,
                "title": post["title"],
                "legacyUrl": post["legacyUrl"],
                "publishDate": post["publishDate"],
                "featuredImage": post["featuredImage"],
                "readTime": post["readTime"],
                "categories": post["categories"],
                "tags": post["tags"],
                "wordCount": post["wordCount"],
                "metaDescription": post["metaDescription"],
                "mdxFile": f"posts/{slug}.mdx",
                "extractionStatus": "Extracted — review before publish",
            })
        else:
            failed.append(full_url)
            manifest.append({
                "id": post_id,
                "title": "",
                "legacyUrl": full_url,
                "publishDate": "",
                "featuredImage": "",
                "readTime": "",
                "categories": [],
                "tags": [],
                "wordCount": 0,
                "metaDescription": "",
                "mdxFile": None,
                "extractionStatus": "FAILED — retry manually",
            })
        time.sleep(0.6)

    Path("manifest_full.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nDone. {len(manifest) - len(failed)} extracted, {len(failed)} failed.")
    if failed:
        print("Failed URLs:")
        for u in failed:
            print(f"  {u}")


if __name__ == "__main__":
    main()
