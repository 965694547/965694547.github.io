# Zhifang Guo — Academic Homepage

Personal academic homepage of **Zhifang Guo (郭智方)** — Multimodal Algorithm Researcher at the
Qwen Team, Alibaba Group — live at **https://965694547.github.io/**.

Style inspired by [Yi Ren's homepage](https://rayeren.github.io/) (AcadHomepage), implemented as a
single static `index.html` — no Jekyll build required, GitHub Pages serves it directly from `main`.

## How to edit

- All content lives in `index.html`. Sections: About Me / News / Publications / Honors & Awards / Educations / Internships & Collaborations.
- Publications: the seven featured papers use `paper-box` cards (figure on the left in
  `assets/img/figs/`, blue venue badge on top); all other papers are one-line entries in
  **Other Publications**.
- To add a paper: copy a `paper-box` block for a featured paper, or add one `<li>` to
  `.pub-list` for a compact entry.
- To change the avatar: replace `assets/img/avatar.jpg`.

## Live numbers

- **GitHub stars** are live shields.io badges (`img.shields.io/github/stars/...`) — always current, nothing to maintain.
- **Citations** (the "35,672 citations" figure in About Me + per-paper counts in the featured
  cards) come from `google-scholar-stats/gs_data.json`, which is crawled from Google Scholar by
  `.github/workflows/google_scholar_crawler.yaml` (every 30 minutes, or run manually via
  *Run workflow*). Google itself recalculates Scholar citation counts about once a day,
  so that cadence is the practical ceiling for "real time".
- The page **auto-refreshes**: on load, every 10 minutes while the tab stays open, and whenever
  the tab returns to the foreground, `index.html` re-reads `gs_data.json` (cache-busted) and
  rewrites the numbers in place. There is no visible widget for this in the sidebar — the
  numbers simply stay current.
- **Paper count** ("22 papers" in About Me) is counted from the page itself at load time
  (featured cards + `Other Publications` entries), so it follows any paper you add or remove.
  If the crawler JSON reports a publication list, that length wins.
- If the JSON is missing, the static numbers written in the HTML are kept as fallback. Those
  fallback values are the last known good numbers — keep them in sync with `gs_data.json`
  when you re-seed manually.
- To re-seed the data locally: `pip install -r google_scholar_crawler/requirements.txt`, then
  `GOOGLE_SCHOLAR_ID=LHzzlAgAAAAJ python google_scholar_crawler/main.py` and copy
  `results/gs_data.json` into `google-scholar-stats/`. (Requires direct access to
  scholar.google.com — not possible from mainland networks.)

## Local preview

```bash
python -m http.server 8000
# open http://localhost:8000
```

## Search engine indexing

The page is meant to be findable by both **Zhifang Guo** and **郭智方**.

- `index.html` head: bilingual `<title>`, `description`, `keywords`, `author`, Open Graph
  (`og:locale: zh_CN`), and a schema.org `Person` block with `alternateName`
  (`郭智方`, `Guo Zhifang`, `Zhi-Fang Guo`) plus `alumniOf` / `worksFor` / `knowsAbout`.
- `robots.txt` explicitly allows `Baiduspider`, `Sogou web spider`, `360Spider`, `YisouSpider`,
  `Bytespider` next to the international crawlers; it points at `sitemap.xml`.
- `9907702c613dcd9660ac922225fb6285.txt` is the IndexNow key: after every content change, POST
  `{"host":"965694547.github.io","key":"...","keyLocation":"...","urlList":["https://965694547.github.io/"]}`
  to `api.indexnow.org/indexnow` (Bing / Yandex / Naver / Seznam all consume it).
- Google: verification meta is in the head; sitemap submission and "request indexing" are done in
  Search Console.
- 百度 / 360 / 搜狗 have no open API — the site has to be added once in each webmaster console
  (`ziyuan.baidu.com`, `zhanzhang.so.com`, `zhanzhang.sogou.com`) and the sitemap submitted there.
