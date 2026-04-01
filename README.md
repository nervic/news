# 📰 Personal News Digest

A self-updating personal news aggregator. Runs daily via GitHub Actions, 
publishes to GitHub Pages, summarises articles with Claude AI.

---

## Setup (one-time, ~15 minutes)

### 1. Create a GitHub account
Go to github.com and sign up if you don't have one.

### 2. Create a new repository
- Click **+** → **New repository**
- Name: `news-digest`
- Visibility: **Public** (required for free GitHub Pages)
- Check **Add a README file**
- Click **Create repository**

### 3. Upload project files
In your repository, click **Add file → Upload files** and upload:
- `fetch_news.py`
- `config.json`
- `.github/workflows/digest.yml` (create the folder path manually or upload via GitHub UI)

### 4. Add your Anthropic API key
- In your repository, go to **Settings → Secrets and variables → Actions**
- Click **New repository secret**
- Name: `ANTHROPIC_API_KEY`
- Value: your API key from console.anthropic.com
- Click **Add secret**

### 5. Enable GitHub Pages
- Go to **Settings → Pages**
- Source: **Deploy from a branch**
- Branch: `main` / folder: `/docs`
- Click **Save**

### 6. Run it for the first time
- Go to the **Actions** tab in your repository
- Click **Daily News Digest** in the left sidebar
- Click **Run workflow → Run workflow**
- Wait ~2-3 minutes for it to complete

### 7. Visit your digest
Your digest will be live at:
```
https://YOUR-GITHUB-USERNAME.github.io/news-digest
```

---

## Customising

### Change the schedule
Edit `.github/workflows/digest.yml` — the `cron` line.
Format: `minute hour * * *` (UTC time).
Examples:
- `0 6 * * *`  — every day at 06:00 UTC
- `0 7,14 * * *` — twice daily at 07:00 and 14:00 UTC

### Add/remove sources
Two ways:
1. **Via the website** — click ⚙ Sources on your digest page, make changes, export `config.json`, replace it in your repo
2. **Directly** — edit `config.json` in your repository

### Custom domain
If you own a domain, go to **Settings → Pages → Custom domain** and follow GitHub's instructions.
Point your domain's DNS CNAME record to `YOUR-USERNAME.github.io`.

---

## File structure

```
news-digest/
├── fetch_news.py              ← main script
├── config.json                ← sources config (edit this to manage sources)
├── README.md
├── .github/
│   └── workflows/
│       └── digest.yml         ← GitHub Actions schedule
└── docs/                      ← generated output (GitHub Pages serves this)
    ├── index.html             ← today's digest
    ├── archive-index.json     ← archive manifest
    └── archive/
        ├── 2026-04-01.html
        ├── 2026-04-02.html
        └── ...
```
