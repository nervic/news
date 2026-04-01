#!/usr/bin/env python3
"""
News digest generator — reads config.json, fetches RSS, summarizes via Claude API,
writes dated HTML files and updates index.html.
"""

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse

ROOT          = Path(__file__).parent
CONFIG_FILE   = ROOT / "config.json"
DOCS_DIR      = ROOT / "docs"
ARCHIVE_DIR   = DOCS_DIR / "archive"
INDEX_FILE    = DOCS_DIR / "index.html"
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

TOPICS_ORDER = [
    "Football", "AI & Tech", "Space", "Video Gaming",
    "Global Finance", "Cryptocurrencies", "Gadgets & Hardware",
    "Leadership", "Balkan Politics", "Nis News",
]
TOPIC_ICONS = {
    "Football": "⚽", "AI & Tech": "🤖", "Space": "🚀",
    "Video Gaming": "🎮", "Global Finance": "📈", "Cryptocurrencies": "₿",
    "Gadgets & Hardware": "🔧", "Leadership": "💡",
    "Balkan Politics": "🗺️", "Nis News": "🏙️",
}

FETCH_TIMEOUT = 10
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PersonalNewsDigest/1.0)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}
NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_rss(url: str, max_items: int) -> list:
    try:
        req = Request(url, headers=HEADERS)
        with urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            raw = resp.read()
    except (URLError, HTTPError) as e:
        print(f"    ⚠  {url}: {e}")
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raw_str = raw.decode("utf-8", errors="replace")
        raw_str = re.sub(r'\sxmlns="[^"]+"', '', raw_str, count=1)
        try:
            root = ET.fromstring(raw_str)
        except ET.ParseError as e:
            print(f"    ⚠  XML parse error {url}: {e}")
            return []

    articles = []
    for item in root.findall(".//item")[:max_items]:
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        desc = item.findtext("description", "") or item.findtext("summary", "")
        content = item.findtext("content:encoded", namespaces=NS)
        pub = item.findtext("pubDate", "") or item.findtext("dc:date", namespaces=NS, default="")
        if not title or not link:
            continue
        articles.append({
            "title": _clean(title),
            "link": link.strip(),
            "summary": _clean(content or desc or ""),
            "date": pub or "",
        })

    if not articles:
        for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry")[:max_items]:
            title = entry.findtext("{http://www.w3.org/2005/Atom}title", "")
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            link = link_el.get("href", "") if link_el is not None else ""
            summary = (entry.findtext("{http://www.w3.org/2005/Atom}summary", "") or
                       entry.findtext("{http://www.w3.org/2005/Atom}content", ""))
            pub = (entry.findtext("{http://www.w3.org/2005/Atom}updated", "") or
                   entry.findtext("{http://www.w3.org/2005/Atom}published", ""))
            if not title or not link:
                continue
            articles.append({
                "title": _clean(title),
                "link": link.strip(),
                "summary": _clean(summary),
                "date": pub,
            })
    return articles


def _clean(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#?\w+;", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:800]


def summarize(topic: str, articles: list) -> list:
    if not ANTHROPIC_KEY:
        for a in articles:
            a["ai_summary"] = a["summary"][:220] + ("…" if len(a["summary"]) > 220 else "")
        return articles

    import urllib.request as ureq
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "system": (
            "You are a sharp news editor. Given article titles and text, return ONLY a JSON array "
            "where each element has 'index' (int) and 'summary' (string). "
            "Each summary: exactly 2 sentences, plain text, no markdown. "
            "Be direct and informative. Focus on the most newsworthy facts."
        ),
        "messages": [{"role": "user", "content": (
            f"Topic: {topic}\n\n"
            + "\n\n".join(f"[{i}] TITLE: {a['title']}\nTEXT: {a['summary']}" for i, a in enumerate(articles))
            + "\n\nReturn JSON array only."
        )}],
    }
    try:
        req = ureq.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with ureq.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        raw = data["content"][0]["text"].strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        idx_map = {s["index"]: s["summary"] for s in json.loads(raw)}
        for i, a in enumerate(articles):
            a["ai_summary"] = idx_map.get(i, a["summary"][:220])
    except Exception as e:
        print(f"    ⚠  Claude API error ({topic}): {e}")
        for a in articles:
            a["ai_summary"] = a["summary"][:220] + ("…" if len(a["summary"]) > 220 else "")
    return articles


def slug(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def build_page(topics_data: dict, config: dict, archive_entries: list, date_slug: str) -> str:
    now = datetime.now()
    dt_str = now.strftime("%d %b %Y · %H:%M")
    title = config["settings"].get("digest_title", "Your Daily Digest")
    all_sources = config["sources"]
    total_arts = sum(len(v) for v in topics_data.values())
    active_topics = [t for t in TOPICS_ORDER if topics_data.get(t)]

    nav = "\n".join(
        f'<a href="#{slug(t)}">{TOPIC_ICONS.get(t, "")} {t}</a>'
        for t in active_topics
    )

    sections = ""
    for i, topic in enumerate(active_topics):
        arts = topics_data[topic]
        cards = ""
        for j, a in enumerate(arts):
            lead_class = "lead-story" if j == 0 else "standard-story"
            src = urlparse(a["link"]).netloc.replace("www.", "")
            cards += f"""
        <article class="story {lead_class}">
          <div class="story-meta-top"><span class="story-source">{src}</span></div>
          <h3 class="story-headline"><a href="{a['link']}" target="_blank" rel="noopener">{a['title']}</a></h3>
          <p class="story-summary">{a.get('ai_summary', '')}</p>
          <a class="story-readmore" href="{a['link']}" target="_blank" rel="noopener">Continue reading →</a>
        </article>"""

        sections += f"""
      <section class="topic-section" id="{slug(topic)}" style="animation-delay:{i * 0.06:.2f}s">
        <header class="topic-header">
          <span class="topic-icon">{TOPIC_ICONS.get(topic, '')}</span>
          <h2 class="topic-title">{topic}</h2>
          <span class="topic-count">{len(arts)} stories</span>
        </header>
        <div class="stories-grid">{cards}</div>
      </section>"""

    archive_html = ""
    if archive_entries:
        items = ""
        for entry in sorted(archive_entries, key=lambda x: x["date"], reverse=True)[:30]:
            items += f'<a class="archive-item" href="archive/{entry["file"]}">{entry["label"]}</a>\n'
        archive_html = f"""
      <section class="archive-section">
        <h2 class="archive-title">Past Issues</h2>
        <div class="archive-list">{items}</div>
      </section>"""

    sources_json = json.dumps(all_sources, ensure_ascii=False)
    topics_icons_json = json.dumps(TOPIC_ICONS, ensure_ascii=False)
    topics_order_json = json.dumps(TOPICS_ORDER, ensure_ascii=False)
    topic_options = "\n".join(
        f'<option value="{t}">{TOPIC_ICONS.get(t, "")} {t}</option>'
        for t in TOPICS_ORDER
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — {dt_str}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;0,900;1,400&family=Libre+Baskerville:ital,wght@0,400;0,700;1,400&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {{
  --bg:#0d0f0c; --surface:#161814; --surface2:#1e201c;
  --border:#252720; --border2:#30332a;
  --ink:#ddd8cc; --ink2:#9a9588; --ink3:#5a5750;
  --accent:#c4a35a; --accent2:#7a9e5a; --col:880px;
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
html{{scroll-behavior:smooth}}
body{{background:var(--bg);color:var(--ink);font-family:'Libre Baskerville',Georgia,serif;font-size:16px;line-height:1.7;min-height:100vh}}
.masthead{{border-bottom:1px solid var(--border2);padding:2.5rem 2rem 1.5rem;text-align:center;background:var(--surface);position:relative}}
.masthead::before{{content:'';display:block;height:2px;background:linear-gradient(90deg,transparent,var(--accent),transparent);margin-bottom:2rem}}
.masthead h1{{font-family:'Playfair Display',serif;font-weight:900;font-size:clamp(2.4rem,7vw,4.2rem);letter-spacing:-0.01em;color:var(--ink);line-height:1}}
.masthead .tagline{{font-family:'JetBrains Mono',monospace;font-size:.62rem;letter-spacing:.25em;color:var(--accent);text-transform:uppercase;margin-top:.5rem}}
.masthead .dateline{{font-style:italic;color:var(--ink3);font-size:.82rem;margin-top:.4rem}}
.masthead::after{{content:'';display:block;height:1px;background:linear-gradient(90deg,transparent,var(--border2),transparent);margin-top:1.5rem}}
.top-nav{{background:var(--bg);border-bottom:1px solid var(--border);padding:0 2rem;display:flex;align-items:center;justify-content:space-between;gap:1rem;position:sticky;top:0;z-index:100;backdrop-filter:blur(8px)}}
.topic-links{{display:flex;overflow-x:auto;scrollbar-width:none;gap:0}}
.topic-links a{{color:var(--ink3);text-decoration:none;font-family:'JetBrains Mono',monospace;font-size:.58rem;letter-spacing:.12em;text-transform:uppercase;padding:.72rem .75rem;white-space:nowrap;transition:color .2s;border-bottom:2px solid transparent}}
.topic-links a:hover{{color:var(--accent);border-bottom-color:var(--accent)}}
.settings-btn{{font-family:'JetBrains Mono',monospace;font-size:.58rem;letter-spacing:.12em;text-transform:uppercase;color:var(--ink3);background:none;border:1px solid var(--border2);padding:.32rem .75rem;cursor:pointer;border-radius:3px;white-space:nowrap;transition:all .2s;flex-shrink:0}}
.settings-btn:hover{{color:var(--accent);border-color:var(--accent)}}
.wrapper{{max-width:var(--col);margin:0 auto;padding:0 1.5rem 5rem}}
.topic-section{{margin-top:3.5rem;animation:fadeUp .5s ease both}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(10px)}}to{{opacity:1;transform:none}}}}
.topic-header{{display:flex;align-items:baseline;gap:.6rem;padding-bottom:.6rem;border-bottom:1px solid var(--border2);margin-bottom:1.5rem}}
.topic-icon{{font-size:1.1rem;line-height:1}}
.topic-title{{font-family:'JetBrains Mono',monospace;font-weight:500;font-size:.72rem;letter-spacing:.2em;text-transform:uppercase;color:var(--accent)}}
.topic-count{{font-family:'JetBrains Mono',monospace;font-size:.58rem;color:var(--ink3);margin-left:auto;letter-spacing:.1em}}
.stories-grid{{display:grid;grid-template-columns:1fr 1fr;border:1px solid var(--border);border-radius:4px;overflow:hidden}}
.story{{padding:1.2rem 1.4rem;border-right:1px solid var(--border);border-bottom:1px solid var(--border);transition:background .2s}}
.story:hover{{background:var(--surface)}}
.story:nth-child(2n){{border-right:none}}
.story:nth-last-child(-n+2){{border-bottom:none}}
.lead-story{{grid-column:1/-1;border-right:none;background:var(--surface);padding:1.6rem}}
.lead-story .story-headline{{font-size:1.22rem}}
.lead-story .story-summary{{font-size:.94rem;max-width:72ch}}
.story-source{{font-family:'JetBrains Mono',monospace;font-size:.56rem;letter-spacing:.15em;text-transform:uppercase;color:var(--accent2)}}
.story-meta-top{{margin-bottom:.35rem}}
.story-headline{{font-family:'Playfair Display',serif;font-weight:700;font-size:.97rem;line-height:1.35;margin-bottom:.45rem}}
.story-headline a{{color:var(--ink);text-decoration:none;transition:color .15s}}
.story-headline a:hover{{color:var(--accent)}}
.story-summary{{font-size:.84rem;color:var(--ink2);line-height:1.65;margin-bottom:.65rem;font-style:italic}}
.story-readmore{{font-family:'JetBrains Mono',monospace;font-size:.58rem;letter-spacing:.1em;text-transform:uppercase;color:var(--accent);text-decoration:none;border-bottom:1px solid transparent;transition:border-color .15s}}
.story-readmore:hover{{border-bottom-color:var(--accent)}}
.archive-section{{margin-top:4rem;padding-top:2rem;border-top:1px solid var(--border2)}}
.archive-title{{font-family:'JetBrains Mono',monospace;font-size:.62rem;letter-spacing:.2em;text-transform:uppercase;color:var(--ink3);margin-bottom:1rem}}
.archive-list{{display:flex;flex-wrap:wrap;gap:8px}}
.archive-item{{font-family:'JetBrains Mono',monospace;font-size:.62rem;letter-spacing:.08em;padding:.3rem .8rem;border:1px solid var(--border2);border-radius:3px;color:var(--ink3);text-decoration:none;transition:all .2s}}
.archive-item:hover{{color:var(--accent);border-color:var(--accent)}}
footer{{text-align:center;padding:2.5rem 2rem;border-top:1px solid var(--border);font-family:'JetBrains Mono',monospace;font-size:.58rem;letter-spacing:.15em;text-transform:uppercase;color:var(--ink3);margin-top:4rem}}
footer a{{color:var(--ink3);text-decoration:none}}
footer a:hover{{color:var(--accent)}}
.settings-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:200;align-items:flex-start;justify-content:flex-end}}
.settings-overlay.open{{display:flex}}
.settings-panel{{background:var(--surface);border-left:1px solid var(--border2);width:min(480px,100vw);height:100vh;overflow-y:auto;padding:2rem;animation:slideIn .25s ease}}
@keyframes slideIn{{from{{transform:translateX(100%)}}to{{transform:none}}}}
.panel-heading{{font-family:'JetBrains Mono',monospace;font-size:.68rem;letter-spacing:.2em;text-transform:uppercase;color:var(--accent);margin-bottom:1.5rem;display:flex;justify-content:space-between;align-items:center}}
.settings-close{{background:none;border:none;color:var(--ink3);cursor:pointer;font-size:1.2rem;line-height:1;padding:0;transition:color .2s}}
.settings-close:hover{{color:var(--ink)}}
.sec-label{{font-family:'JetBrains Mono',monospace;font-size:.58rem;letter-spacing:.15em;text-transform:uppercase;color:var(--ink3);border-bottom:1px solid var(--border);padding-bottom:.4rem;margin:1.5rem 0 .8rem}}
.source-item{{display:flex;align-items:center;gap:10px;padding:.45rem 0;border-bottom:1px solid var(--border)}}
.source-item:last-child{{border-bottom:none}}
.source-item input[type=checkbox]{{width:14px;height:14px;accent-color:var(--accent);cursor:pointer;flex-shrink:0}}
.source-label{{flex:1;font-size:.82rem;color:var(--ink2)}}
.source-topic{{font-family:'JetBrains Mono',monospace;font-size:.52rem;letter-spacing:.08em;color:var(--ink3);text-transform:uppercase;flex-shrink:0}}
.add-form{{margin-top:.8rem;display:flex;flex-direction:column;gap:8px}}
.add-form input,.add-form select{{background:var(--bg);border:1px solid var(--border2);border-radius:3px;color:var(--ink);font-family:'JetBrains Mono',monospace;font-size:.72rem;padding:.48rem .72rem;width:100%;outline:none;transition:border-color .2s}}
.add-form input:focus,.add-form select:focus{{border-color:var(--accent)}}
.add-form input::placeholder{{color:var(--ink3)}}
.add-form select option{{background:var(--surface)}}
.btn-row{{display:flex;gap:8px;margin-top:.5rem;flex-wrap:wrap}}
.btn{{font-family:'JetBrains Mono',monospace;font-size:.62rem;letter-spacing:.1em;text-transform:uppercase;padding:.45rem .9rem;border-radius:3px;cursor:pointer;border:1px solid var(--border2);background:none;color:var(--ink2);transition:all .2s}}
.btn:hover{{border-color:var(--accent);color:var(--accent)}}
.btn-primary{{background:var(--accent);color:var(--bg);border-color:var(--accent);font-weight:500}}
.btn-primary:hover{{background:#d4b06a;border-color:#d4b06a;color:var(--bg)}}
.notice{{font-size:.75rem;color:var(--ink3);font-style:italic;margin-top:1rem;line-height:1.55;padding:.72rem;border:1px solid var(--border);border-radius:3px}}
.notice strong{{color:var(--ink2);font-style:normal}}
.notice code{{font-family:'JetBrains Mono',monospace;font-size:.68rem;color:var(--accent)}}
.export-area{{margin-top:.8rem;background:var(--bg);border:1px solid var(--border2);border-radius:3px;padding:.72rem;font-family:'JetBrains Mono',monospace;font-size:.62rem;color:var(--ink2);white-space:pre;overflow-x:auto;max-height:200px;overflow-y:auto;display:none}}
@media(max-width:640px){{
  .stories-grid{{grid-template-columns:1fr}}
  .lead-story{{grid-column:1}}
  .story{{border-right:none}}
  .masthead h1{{font-size:2.2rem}}
}}
</style>
</head>
<body>
<div class="masthead">
  <h1>{title}</h1>
  <div class="tagline">Personal · Curated · Unfiltered</div>
  <div class="dateline">{dt_str} &nbsp;·&nbsp; {total_arts} stories</div>
</div>
<nav class="top-nav">
  <div class="topic-links">{nav}</div>
  <button class="settings-btn" onclick="openSettings()">⚙ Sources</button>
</nav>
<div class="wrapper">
  {sections}
  {archive_html}
</div>
<footer>
  Generated automatically &nbsp;·&nbsp; Summarised by Claude AI &nbsp;·&nbsp;
  <a href="#" onclick="openSettings();return false">Manage sources</a>
</footer>

<div class="settings-overlay" id="overlay" onclick="maybeClose(event)">
  <div class="settings-panel" id="panel">
    <div class="panel-heading">
      Manage Sources
      <button class="settings-close" onclick="closeSettings()">✕</button>
    </div>
    <div id="sourcesList"></div>
    <div class="sec-label">Add new source</div>
    <div class="add-form">
      <input type="url" id="newUrl" placeholder="RSS feed URL — https://example.com/feed" />
      <input type="text" id="newLabel" placeholder="Display name — e.g. My Tech Blog" />
      <select id="newTopic">
        <option value="">— Select topic —</option>
        {topic_options}
      </select>
      <button class="btn btn-primary" onclick="addSource()">+ Add source</button>
    </div>
    <div class="sec-label">Save changes</div>
    <p class="notice">
      Toggle sources on/off above — changes are remembered in your browser instantly.
      To make them permanent, click <strong>Export config.json</strong>, then replace
      <code>config.json</code> in your GitHub repo. The next scheduled run will use your updated settings.
    </p>
    <div class="btn-row">
      <button class="btn btn-primary" onclick="exportConfig()">Export config.json</button>
      <button class="btn" onclick="togglePreview()">Preview JSON</button>
    </div>
    <pre class="export-area" id="exportPre"></pre>
  </div>
</div>

<script>
const TOPICS = {topics_order_json};
const ICONS  = {topics_icons_json};
let sources  = JSON.parse(localStorage.getItem('nd_sources') || 'null') || {sources_json};

function renderSources() {{
  const grouped = {{}};
  sources.forEach((s,i) => {{
    if (!grouped[s.topic]) grouped[s.topic] = [];
    grouped[s.topic].push({{...s, _i:i}});
  }});
  let h = '';
  for (const t of TOPICS) {{
    if (!grouped[t]) continue;
    h += `<div class="sec-label">${{ICONS[t]||''}} ${{t}}</div>`;
    grouped[t].forEach(s => {{
      h += `<div class="source-item">
        <input type="checkbox" ${{s.enabled?'checked':''}} onchange="toggle(${{s._i}},this.checked)"/>
        <span class="source-label">${{s.label}}</span>
      </div>`;
    }});
  }}
  document.getElementById('sourcesList').innerHTML = h;
}}

function toggle(i,v) {{
  sources[i].enabled = v;
  localStorage.setItem('nd_sources', JSON.stringify(sources));
}}

function addSource() {{
  const url   = document.getElementById('newUrl').value.trim();
  const label = document.getElementById('newLabel').value.trim();
  const topic = document.getElementById('newTopic').value;
  if (!url||!label||!topic) {{ alert('Please fill in all three fields.'); return; }}
  sources.push({{url,label,topic,enabled:true}});
  localStorage.setItem('nd_sources', JSON.stringify(sources));
  document.getElementById('newUrl').value='';
  document.getElementById('newLabel').value='';
  document.getElementById('newTopic').value='';
  renderSources();
}}

function getConfig() {{
  return {{
    settings:{{max_articles_per_topic:8,max_articles_per_source:5,digest_title:"{title}"}},
    sources:sources.map(s=>({{url:s.url,topic:s.topic,label:s.label,enabled:s.enabled}}))
  }};
}}

function exportConfig() {{
  const blob = new Blob([JSON.stringify(getConfig(),null,2)],{{type:'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'config.json';
  a.click();
}}

function togglePreview() {{
  const el = document.getElementById('exportPre');
  if (el.style.display==='block'){{ el.style.display='none'; return; }}
  el.textContent = JSON.stringify(getConfig(),null,2);
  el.style.display = 'block';
}}

function openSettings()  {{ document.getElementById('overlay').classList.add('open'); renderSources(); }}
function closeSettings() {{ document.getElementById('overlay').classList.remove('open'); }}
function maybeClose(e)   {{ if(e.target===document.getElementById('overlay')) closeSettings(); }}
document.addEventListener('keydown',e=>{{ if(e.key==='Escape') closeSettings(); }});
</script>
</body>
</html>"""


def load_archive_index() -> list:
    idx_file = DOCS_DIR / "archive-index.json"
    if idx_file.exists():
        return json.loads(idx_file.read_text(encoding="utf-8"))
    return []


def save_archive_index(entries: list):
    (DOCS_DIR / "archive-index.json").write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main():
    print("📰  News digest — starting\n")
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    config = load_config()
    settings = config["settings"]
    max_src = settings.get("max_articles_per_source", 5)
    max_top = settings.get("max_articles_per_topic", 8)
    enabled = [s for s in config["sources"] if s.get("enabled", True)]
    print(f"  {len(enabled)} sources enabled\n")

    topics_data = {t: [] for t in TOPICS_ORDER}
    seen = set()

    for src in enabled:
        domain = urlparse(src["url"]).netloc.replace("www.", "")
        print(f"  ↓ {src['label']} ({domain})")
        for a in fetch_rss(src["url"], max_src):
            key = a["title"].lower()[:80]
            if key in seen:
                continue
            seen.add(key)
            topic = src["topic"]
            if topic in topics_data and len(topics_data[topic]) < max_top:
                topics_data[topic].append(a)
        time.sleep(0.25)

    print(f"\n✅  {len(seen)} unique articles fetched")
    print("🤖  Summarising...\n")

    for topic in TOPICS_ORDER:
        if not topics_data[topic]:
            continue
        print(f"  ✍  {topic} ({len(topics_data[topic])})")
        topics_data[topic] = summarize(topic, topics_data[topic])

    now = datetime.now()
    date_slug = now.strftime("%Y-%m-%d")
    date_label = now.strftime("%d %b %Y")
    archive = load_archive_index()
    archive_file = f"{date_slug}.html"

    print("\n🎨  Building HTML...")
    html = build_page(topics_data, config, archive, date_slug)

    (ARCHIVE_DIR / archive_file).write_text(html, encoding="utf-8")

    if not any(e["file"] == archive_file for e in archive):
        archive.append({"file": archive_file, "label": date_label, "date": date_slug})
        save_archive_index(archive)

    html = build_page(topics_data, config, archive, date_slug)
    INDEX_FILE.write_text(html, encoding="utf-8")

    print(f"✅  Written to {INDEX_FILE}")
    print("Done. ☕\n")


if __name__ == "__main__":
    main()
