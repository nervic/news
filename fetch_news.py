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
    "Leadership", "Balkan Politics", "Niš News",
]

TOPIC_ICONS = {
    "Football": "⚽", "AI & Tech": "🤖", "Space": "🚀",
    "Video Gaming": "🎮", "Global Finance": "📈", "Cryptocurrencies": "₿",
    "Gadgets & Hardware": "🔧", "Leadership": "💡",
    "Balkan Politics": "🗺️", "Niš News": "🏙️",
}

TOPIC_COLORS = {
    "Football":          "#5a9e6e",
    "AI & Tech":         "#6e8ebe",
    "Space":             "#9e7ab5",
    "Video Gaming":      "#be8c4a",
    "Global Finance":    "#5a9e8a",
    "Cryptocurrencies":  "#c4a35a",
    "Gadgets & Hardware":"#7a9ebe",
    "Leadership":        "#be7a6e",
    "Balkan Politics":   "#8ebe6e",
    "Niš News":          "#be6e8c",
}

FETCH_TIMEOUT = 12
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PersonalNewsDigest/1.0)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}
NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}

# Normalise topic names from config to match TOPICS_ORDER
TOPIC_ALIASES = {
    "nis news":    "Niš News",
    "niš news":    "Niš News",
    "nish news":   "Niš News",
    "niš":         "Niš News",
    "nis":         "Niš News",
}

def normalise_topic(t: str) -> str:
    return TOPIC_ALIASES.get(t.lower().strip(), t.strip())


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # normalise topic names in sources
    for s in cfg["sources"]:
        s["topic"] = normalise_topic(s["topic"])
    return cfg


def fetch_rss(url: str, max_items: int) -> list:
    try:
        req = Request(url, headers=HEADERS)
        with urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            raw = resp.read()
    except (URLError, HTTPError) as e:
        print(f"    ⚠  {url}: {e}")
        return []
    except Exception as e:
        print(f"    ⚠  {url}: {e}")
        return []

    # Try parsing as-is first
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # Strip default namespace and retry
        raw_str = raw.decode("utf-8", errors="replace")
        raw_str = re.sub(r'\sxmlns="[^"]+"', '', raw_str, count=1)
        try:
            root = ET.fromstring(raw_str)
        except ET.ParseError as e:
            print(f"    ⚠  XML parse error {url}: {e}")
            return []

    articles = []

    # RSS 2.0
    for item in root.findall(".//item")[:max_items]:
        title   = item.findtext("title", "")
        link    = item.findtext("link", "")
        desc    = item.findtext("description", "") or item.findtext("summary", "")
        content = item.findtext("content:encoded", namespaces=NS)
        pub     = (item.findtext("pubDate", "") or
                   item.findtext("dc:date", namespaces=NS, default=""))
        if not title or not link:
            continue
        articles.append({
            "title":   _clean(title),
            "link":    link.strip(),
            "summary": _clean(content or desc or ""),
            "date":    pub or "",
        })

    # Atom fallback
    if not articles:
        for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry")[:max_items]:
            title   = entry.findtext("{http://www.w3.org/2005/Atom}title", "")
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            link    = link_el.get("href", "") if link_el is not None else ""
            summary = (entry.findtext("{http://www.w3.org/2005/Atom}summary", "") or
                       entry.findtext("{http://www.w3.org/2005/Atom}content", ""))
            pub     = (entry.findtext("{http://www.w3.org/2005/Atom}updated", "") or
                       entry.findtext("{http://www.w3.org/2005/Atom}published", ""))
            if not title or not link:
                continue
            articles.append({
                "title":   _clean(title),
                "link":    link.strip(),
                "summary": _clean(summary),
                "date":    pub,
            })

    return articles


def _clean(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>",  " ",  text)
    text = re.sub(r"&amp;",    "&",   text)
    text = re.sub(r"&lt;",     "<",   text)
    text = re.sub(r"&gt;",     ">",   text)
    text = re.sub(r"&quot;",   '"',   text)
    text = re.sub(r"&#?\w+;",  "",    text)
    text = re.sub(r"\s+",      " ",   text).strip()
    return text[:900]


def summarize(topic: str, articles: list) -> list:
    if not ANTHROPIC_KEY:
        for a in articles:
            snip = a["summary"]
            a["ai_summary"] = snip[:260] + ("…" if len(snip) > 260 else "")
        return articles

    import urllib.request as ureq
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1200,
        "system": (
            "You are a sharp news editor. Given article titles and text, return ONLY a JSON array "
            "where each element has 'index' (int) and 'summary' (string). "
            "Each summary: exactly 2 clear sentences, plain text, no markdown, no bullet points. "
            "Be direct and informative. Preserve names, numbers, and key facts exactly."
        ),
        "messages": [{"role": "user", "content": (
            f"Topic: {topic}\n\n"
            + "\n\n".join(
                f"[{i}] TITLE: {a['title']}\nTEXT: {a['summary']}"
                for i, a in enumerate(articles)
            )
            + "\n\nReturn JSON array only."
        )}],
    }
    try:
        req = ureq.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         ANTHROPIC_KEY,
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
            a["ai_summary"] = idx_map.get(i, a["summary"][:260])
    except Exception as e:
        print(f"    ⚠  Claude API error ({topic}): {e}")
        for a in articles:
            a["ai_summary"] = a["summary"][:260] + ("…" if len(a["summary"]) > 260 else "")
    return articles


def slug(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def build_page(topics_data: dict, config: dict, archive_entries: list) -> str:
    now      = datetime.now()
    dt_str   = now.strftime("%A, %d %B %Y · %H:%M")
    title    = config["settings"].get("digest_title", "Your Daily Digest")
    all_sources   = config["sources"]
    total_arts    = sum(len(v) for v in topics_data.values())
    active_topics = [t for t in TOPICS_ORDER if topics_data.get(t)]

    # Nav links
    nav = "\n".join(
        f'<a href="#{slug(t)}">'
        f'<span class="ni">{TOPIC_ICONS.get(t,"")}</span>{t}</a>'
        for t in active_topics
    )

    # Article sections
    sections = ""
    for i, topic in enumerate(active_topics):
        arts  = topics_data[topic]
        color = TOPIC_COLORS.get(topic, "#c4a35a")
        cards = ""
        for j, a in enumerate(arts):
            src        = urlparse(a["link"]).netloc.replace("www.", "")
            is_lead    = j == 0
            lead_class = "lead" if is_lead else ""
            summary    = a.get("ai_summary", "")
            cards += f"""
        <article class="card {lead_class}">
          <div class="card-source">{src}</div>
          <h3 class="card-title">
            <a href="{a['link']}" target="_blank" rel="noopener">{a['title']}</a>
          </h3>
          {'<p class="card-summary">' + summary + '</p>' if summary else ''}
          <a class="card-read" href="{a['link']}" target="_blank" rel="noopener">Read full story →</a>
        </article>"""

        sections += f"""
    <section class="topic" id="{slug(topic)}" style="--tc:{color};animation-delay:{i*0.07:.2f}s">
      <header class="topic-head">
        <span class="topic-icon">{TOPIC_ICONS.get(topic,"")}</span>
        <span class="topic-name">{topic}</span>
        <span class="topic-count">{len(arts)} stories</span>
      </header>
      <div class="cards">{cards}</div>
    </section>"""

    # Archive footer
    archive_html = ""
    if archive_entries:
        items = "".join(
            f'<a class="arc-link" href="archive/{e["file"]}">{e["label"]}</a>'
            for e in sorted(archive_entries, key=lambda x: x["date"], reverse=True)[:30]
        )
        archive_html = f"""
    <section class="archive">
      <div class="arc-label">Past Issues</div>
      <div class="arc-links">{items}</div>
    </section>"""

    sources_json      = json.dumps(all_sources, ensure_ascii=False)
    topic_icons_json  = json.dumps(TOPIC_ICONS, ensure_ascii=False)
    topics_order_json = json.dumps(TOPICS_ORDER, ensure_ascii=False)
    topic_options     = "\n".join(
        f'<option value="{t}">{TOPIC_ICONS.get(t,"")} {t}</option>'
        for t in TOPICS_ORDER
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{{
  --bg:#0e0f0d;
  --surface:#161714;
  --surface2:#1c1e1a;
  --border:#252720;
  --border2:#2e312a;
  --ink:#e2ddd4;
  --ink2:#b0aa9e;
  --ink3:#686560;
  --accent:#c4a35a;
  --max:740px;
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
html{{scroll-behavior:smooth}}
body{{
  background:var(--bg);
  color:var(--ink);
  font-family:'Source Serif 4',Georgia,serif;
  font-size:18px;
  line-height:1.75;
  min-height:100vh;
}}

/* ── Masthead ── */
.mast{{
  background:var(--surface);
  border-bottom:1px solid var(--border2);
  padding:3rem 2rem 2rem;
  text-align:center;
}}
.mast::before{{
  content:'';
  display:block;
  height:2px;
  background:linear-gradient(90deg,transparent,var(--accent),transparent);
  margin-bottom:2.5rem;
}}
.mast h1{{
  font-family:'Playfair Display',serif;
  font-weight:900;
  font-size:clamp(2.6rem,8vw,4.8rem);
  letter-spacing:-0.02em;
  line-height:1;
  color:var(--ink);
}}
.mast .sub{{
  font-family:'JetBrains Mono',monospace;
  font-size:0.65rem;
  letter-spacing:0.25em;
  text-transform:uppercase;
  color:var(--accent);
  margin-top:0.6rem;
}}
.mast .date{{
  font-size:0.95rem;
  font-style:italic;
  color:var(--ink3);
  margin-top:0.4rem;
}}

/* ── Sticky nav ── */
.nav{{
  position:sticky;
  top:0;
  z-index:100;
  background:rgba(14,15,13,0.95);
  backdrop-filter:blur(10px);
  border-bottom:1px solid var(--border);
  display:flex;
  align-items:center;
  justify-content:space-between;
  padding:0 1.5rem;
  gap:1rem;
}}
.nav-links{{
  display:flex;
  overflow-x:auto;
  scrollbar-width:none;
  gap:0;
  flex:1;
}}
.nav-links a{{
  font-family:'JetBrains Mono',monospace;
  font-size:0.6rem;
  letter-spacing:0.12em;
  text-transform:uppercase;
  color:var(--ink3);
  text-decoration:none;
  padding:0.8rem 0.7rem;
  white-space:nowrap;
  border-bottom:2px solid transparent;
  transition:color 0.2s,border-color 0.2s;
  display:flex;
  align-items:center;
  gap:0.3rem;
}}
.nav-links a:hover{{color:var(--accent);border-bottom-color:var(--accent)}}
.nav-links .ni{{font-size:0.85rem}}
.src-btn{{
  font-family:'JetBrains Mono',monospace;
  font-size:0.6rem;
  letter-spacing:0.1em;
  text-transform:uppercase;
  color:var(--ink3);
  background:none;
  border:1px solid var(--border2);
  padding:0.32rem 0.7rem;
  border-radius:3px;
  cursor:pointer;
  white-space:nowrap;
  flex-shrink:0;
  transition:all 0.2s;
}}
.src-btn:hover{{color:var(--accent);border-color:var(--accent)}}

/* ── Main content ── */
.wrap{{
  max-width:var(--max);
  margin:0 auto;
  padding:0 1.5rem 6rem;
}}

/* ── Topic section ── */
.topic{{
  margin-top:4rem;
  animation:fadeUp 0.5s ease both;
}}
@keyframes fadeUp{{
  from{{opacity:0;transform:translateY(12px)}}
  to{{opacity:1;transform:none}}
}}
.topic-head{{
  display:flex;
  align-items:center;
  gap:0.6rem;
  padding-bottom:0.75rem;
  border-bottom:3px solid var(--tc,var(--accent));
  margin-bottom:2rem;
}}
.topic-icon{{font-size:1.2rem;line-height:1}}
.topic-name{{
  font-family:'JetBrains Mono',monospace;
  font-weight:500;
  font-size:0.72rem;
  letter-spacing:0.22em;
  text-transform:uppercase;
  color:var(--tc,var(--accent));
}}
.topic-count{{
  font-family:'JetBrains Mono',monospace;
  font-size:0.6rem;
  color:var(--ink3);
  margin-left:auto;
  letter-spacing:0.1em;
}}

/* ── Cards ── */
.cards{{display:flex;flex-direction:column;gap:0}}

.card{{
  padding:1.8rem 0;
  border-bottom:1px solid var(--border);
}}
.card:last-child{{border-bottom:none}}

.card.lead{{
  padding:2rem 1.75rem;
  background:var(--surface);
  border-radius:6px;
  border:none;
  margin-bottom:0.5rem;
}}
.card.lead .card-title{{font-size:1.55rem;line-height:1.3}}
.card.lead .card-summary{{font-size:1.05rem}}

.card-source{{
  font-family:'JetBrains Mono',monospace;
  font-size:0.62rem;
  letter-spacing:0.15em;
  text-transform:uppercase;
  color:var(--tc,var(--accent));
  opacity:0.8;
  margin-bottom:0.5rem;
}}
.card-title{{
  font-family:'Playfair Display',serif;
  font-weight:700;
  font-size:1.25rem;
  line-height:1.35;
  margin-bottom:0.65rem;
}}
.card-title a{{
  color:var(--ink);
  text-decoration:none;
  transition:color 0.15s;
}}
.card-title a:hover{{color:var(--tc,var(--accent))}}
.card-summary{{
  font-size:1rem;
  color:var(--ink2);
  line-height:1.7;
  margin-bottom:0.75rem;
}}
.card-read{{
  font-family:'JetBrains Mono',monospace;
  font-size:0.62rem;
  letter-spacing:0.1em;
  text-transform:uppercase;
  color:var(--tc,var(--accent));
  text-decoration:none;
  opacity:0.75;
  transition:opacity 0.15s;
}}
.card-read:hover{{opacity:1}}

/* ── Archive ── */
.archive{{
  margin-top:5rem;
  padding-top:2.5rem;
  border-top:1px solid var(--border2);
}}
.arc-label{{
  font-family:'JetBrains Mono',monospace;
  font-size:0.62rem;
  letter-spacing:0.2em;
  text-transform:uppercase;
  color:var(--ink3);
  margin-bottom:1rem;
}}
.arc-links{{display:flex;flex-wrap:wrap;gap:8px}}
.arc-link{{
  font-family:'JetBrains Mono',monospace;
  font-size:0.62rem;
  letter-spacing:0.08em;
  padding:0.3rem 0.85rem;
  border:1px solid var(--border2);
  border-radius:3px;
  color:var(--ink3);
  text-decoration:none;
  transition:all 0.2s;
}}
.arc-link:hover{{color:var(--accent);border-color:var(--accent)}}

/* ── Footer ── */
footer{{
  text-align:center;
  padding:2.5rem 2rem;
  border-top:1px solid var(--border);
  font-family:'JetBrains Mono',monospace;
  font-size:0.6rem;
  letter-spacing:0.15em;
  text-transform:uppercase;
  color:var(--ink3);
  margin-top:4rem;
}}
footer a{{color:var(--ink3);text-decoration:none;transition:color 0.2s}}
footer a:hover{{color:var(--accent)}}

/* ── Settings panel ── */
.overlay{{
  display:none;
  position:fixed;
  inset:0;
  background:rgba(0,0,0,0.82);
  z-index:200;
  justify-content:flex-end;
}}
.overlay.open{{display:flex}}
.panel{{
  background:var(--surface);
  border-left:1px solid var(--border2);
  width:min(500px,100vw);
  height:100vh;
  overflow-y:auto;
  padding:2rem;
  animation:slideIn 0.25s ease;
}}
@keyframes slideIn{{from{{transform:translateX(100%)}}to{{transform:none}}}}
.panel-head{{
  font-family:'JetBrains Mono',monospace;
  font-size:0.68rem;
  letter-spacing:0.2em;
  text-transform:uppercase;
  color:var(--accent);
  margin-bottom:1.5rem;
  display:flex;
  justify-content:space-between;
  align-items:center;
}}
.p-close{{
  background:none;border:none;color:var(--ink3);
  cursor:pointer;font-size:1.3rem;line-height:1;
  padding:0;transition:color 0.2s;
}}
.p-close:hover{{color:var(--ink)}}
.sec{{
  font-family:'JetBrains Mono',monospace;
  font-size:0.58rem;letter-spacing:0.15em;
  text-transform:uppercase;color:var(--ink3);
  border-bottom:1px solid var(--border);
  padding-bottom:0.4rem;margin:1.5rem 0 0.8rem;
}}
.src-row{{
  display:flex;align-items:center;gap:10px;
  padding:0.5rem 0;border-bottom:1px solid var(--border);
}}
.src-row:last-child{{border-bottom:none}}
.src-row input[type=checkbox]{{
  width:15px;height:15px;
  accent-color:var(--accent);cursor:pointer;flex-shrink:0;
}}
.src-name{{flex:1;font-size:0.9rem;color:var(--ink2);line-height:1.3}}
.add-form{{display:flex;flex-direction:column;gap:8px;margin-top:0.5rem}}
.add-form input,.add-form select{{
  background:var(--bg);border:1px solid var(--border2);
  border-radius:3px;color:var(--ink);
  font-family:'JetBrains Mono',monospace;font-size:0.75rem;
  padding:0.5rem 0.75rem;width:100%;outline:none;
  transition:border-color 0.2s;
}}
.add-form input:focus,.add-form select:focus{{border-color:var(--accent)}}
.add-form input::placeholder{{color:var(--ink3)}}
.add-form select option{{background:var(--surface)}}
.btn-row{{display:flex;gap:8px;margin-top:0.6rem;flex-wrap:wrap}}
.btn{{
  font-family:'JetBrains Mono',monospace;font-size:0.62rem;
  letter-spacing:0.1em;text-transform:uppercase;
  padding:0.45rem 0.9rem;border-radius:3px;cursor:pointer;
  border:1px solid var(--border2);background:none;color:var(--ink2);
  transition:all 0.2s;
}}
.btn:hover{{border-color:var(--accent);color:var(--accent)}}
.btn-p{{background:var(--accent);color:var(--bg);border-color:var(--accent);font-weight:500}}
.btn-p:hover{{background:#d4b06a;border-color:#d4b06a;color:var(--bg)}}
.notice{{
  font-size:0.78rem;color:var(--ink3);font-style:italic;
  margin-top:1rem;line-height:1.6;padding:0.75rem;
  border:1px solid var(--border);border-radius:3px;
}}
.notice strong{{color:var(--ink2);font-style:normal}}
.notice code{{font-family:'JetBrains Mono',monospace;font-size:0.7rem;color:var(--accent)}}
.exp-pre{{
  margin-top:0.75rem;background:var(--bg);
  border:1px solid var(--border2);border-radius:3px;
  padding:0.75rem;font-family:'JetBrains Mono',monospace;
  font-size:0.62rem;color:var(--ink2);white-space:pre;
  overflow:auto;max-height:220px;display:none;
}}

@media(max-width:600px){{
  .mast h1{{font-size:2.4rem}}
  .card.lead{{padding:1.4rem}}
  .card.lead .card-title{{font-size:1.3rem}}
  .wrap{{padding:0 1rem 4rem}}
}}
</style>
</head>
<body>

<div class="mast">
  <h1>{title}</h1>
  <div class="date">{dt_str} &nbsp;·&nbsp; {total_arts} stories</div>
</div>

<nav class="nav">
  <div class="nav-links">{nav}</div>
  <button class="src-btn" onclick="openSettings()">⚙ Sources</button>
</nav>

<div class="wrap">
  {sections}
  {archive_html}
</div>

<footer>
  Refreshed daily · Summarised by Claude AI ·
  <a href="#" onclick="openSettings();return false">Manage sources</a>
</footer>

<!-- Settings panel -->
<div class="overlay" id="overlay" onclick="maybeClose(event)">
  <div class="panel" id="panel">
    <div class="panel-head">
      Manage Sources
      <button class="p-close" onclick="closeSettings()">✕</button>
    </div>
    <div id="srcList"></div>
    <div class="sec">Add new source</div>
    <div class="add-form">
      <input type="url" id="newUrl" placeholder="RSS feed URL — https://example.com/feed"/>
      <input type="text" id="newLabel" placeholder="Display name — e.g. My Tech Blog"/>
      <select id="newTopic">
        <option value="">— Select topic —</option>
        {topic_options}
      </select>
      <button class="btn btn-p" onclick="addSource()">+ Add source</button>
    </div>
    <div class="sec">Save changes</div>
    <p class="notice">
      Toggle sources on/off — changes are saved in your browser instantly.
      To make them permanent, click <strong>Export config.json</strong> and replace
      <code>config.json</code> in your GitHub repo. The next scheduled run will pick up your changes.
    </p>
    <div class="btn-row">
      <button class="btn btn-p" onclick="exportConfig()">Export config.json</button>
      <button class="btn" onclick="togglePreview()">Preview JSON</button>
    </div>
    <pre class="exp-pre" id="expPre"></pre>
  </div>
</div>

<script>
const TOPICS = {topics_order_json};
const ICONS  = {topic_icons_json};
let sources  = JSON.parse(localStorage.getItem('nd_sources')||'null') || {sources_json};

function renderSrc(){{
  const g={{}};
  sources.forEach((s,i)=>{{(g[s.topic]=g[s.topic]||[]).push({{...s,_i:i}})}});
  let h='';
  for(const t of TOPICS){{
    if(!g[t]) continue;
    h+=`<div class="sec">${{ICONS[t]||''}} ${{t}}</div>`;
    g[t].forEach(s=>{{
      h+=`<div class="src-row">
        <input type="checkbox" ${{s.enabled?'checked':''}} onchange="tog(${{s._i}},this.checked)"/>
        <span class="src-name">${{s.label}}</span>
      </div>`;
    }});
  }}
  document.getElementById('srcList').innerHTML=h;
}}

function tog(i,v){{
  sources[i].enabled=v;
  localStorage.setItem('nd_sources',JSON.stringify(sources));
}}

function addSource(){{
  const url=document.getElementById('newUrl').value.trim();
  const label=document.getElementById('newLabel').value.trim();
  const topic=document.getElementById('newTopic').value;
  if(!url||!label||!topic){{alert('Please fill in all three fields.');return;}}
  sources.push({{url,label,topic,enabled:true}});
  localStorage.setItem('nd_sources',JSON.stringify(sources));
  document.getElementById('newUrl').value='';
  document.getElementById('newLabel').value='';
  document.getElementById('newTopic').value='';
  renderSrc();
}}

function getCfg(){{
  return {{
    settings:{{max_articles_per_topic:8,max_articles_per_source:5,digest_title:"Nervicev Dnevnik"}},
    sources:sources.map(s=>({{url:s.url,topic:s.topic,label:s.label,enabled:s.enabled}}))
  }};
}}

function exportConfig(){{
  const b=new Blob([JSON.stringify(getCfg(),null,2)],{{type:'application/json'}});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(b);a.download='config.json';a.click();
}}

function togglePreview(){{
  const el=document.getElementById('expPre');
  if(el.style.display==='block'){{el.style.display='none';return;}}
  el.textContent=JSON.stringify(getCfg(),null,2);
  el.style.display='block';
}}

function openSettings(){{document.getElementById('overlay').classList.add('open');renderSrc();}}
function closeSettings(){{document.getElementById('overlay').classList.remove('open');}}
function maybeClose(e){{if(e.target===document.getElementById('overlay'))closeSettings();}}
document.addEventListener('keydown',e=>{{if(e.key==='Escape')closeSettings();}});
</script>
</body>
</html>"""


def load_archive_index() -> list:
    f = DOCS_DIR / "archive-index.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def save_archive_index(entries: list):
    (DOCS_DIR / "archive-index.json").write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main():
    print("📰  News digest — starting\n")
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    config   = load_config()
    settings = config["settings"]
    max_src  = settings.get("max_articles_per_source", 5)
    max_top  = settings.get("max_articles_per_topic", 8)
    enabled  = [s for s in config["sources"] if s.get("enabled", True)]

    print(f"  {len(enabled)} sources enabled\n")

    topics_data = {t: [] for t in TOPICS_ORDER}
    seen        = set()

    for src in enabled:
        domain = urlparse(src["url"]).netloc.replace("www.", "")
        print(f"  ↓  {src['label']} ({domain})")
        for a in fetch_rss(src["url"], max_src):
            key   = a["title"].lower()[:80]
            if key in seen:
                continue
            seen.add(key)
            topic = src["topic"]
            if topic in topics_data and len(topics_data[topic]) < max_top:
                topics_data[topic].append(a)
        time.sleep(0.3)

    print(f"\n✅  {len(seen)} unique articles fetched")

    # Report any empty topics
    for t in TOPICS_ORDER:
        if not topics_data[t]:
            print(f"  ⚠  No articles for: {t}")

    print("\n🤖  Summarising...\n")
    for topic in TOPICS_ORDER:
        if not topics_data[topic]:
            continue
        print(f"  ✍  {topic} ({len(topics_data[topic])})")
        topics_data[topic] = summarize(topic, topics_data[topic])

    now        = datetime.now()
    date_slug  = now.strftime("%Y-%m-%d")
    date_label = now.strftime("%d %b %Y")
    archive    = load_archive_index()
    arc_file   = f"{date_slug}.html"

    print("\n🎨  Building HTML...")
    html = build_page(topics_data, config, archive)

    (ARCHIVE_DIR / arc_file).write_text(html, encoding="utf-8")

    if not any(e["file"] == arc_file for e in archive):
        archive.append({"file": arc_file, "label": date_label, "date": date_slug})
        save_archive_index(archive)

    # Rebuild index with updated archive
    html = build_page(topics_data, config, archive)
    INDEX_FILE.write_text(html, encoding="utf-8")

    print(f"✅  Written → {INDEX_FILE}")
    print("Done. ☕\n")


if __name__ == "__main__":
    main()
