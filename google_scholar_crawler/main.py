"""Crawl the Google Scholar profile and write a compact JSON snapshot.

Run by .github/workflows/google_scholar_crawler.yaml on a schedule; the output is
copied to google-scholar-stats/gs_data.json and read by index.html at runtime.

The snapshot is deliberately slim (title / year / citations per paper) so the file
stays small and the page only downloads what it renders.
"""

import json
import os
import random
import time
from datetime import datetime, timezone

from scholarly import scholarly

SCHOLAR_ID = os.environ.get("GOOGLE_SCHOLAR_ID", "LHzzlAgAAAAJ")
ATTEMPTS = 4


def fetch_profile():
    author = scholarly.search_author_id(SCHOLAR_ID)
    return scholarly.fill(author, sections=["basics", "indices", "counts", "publications"])


author = None
for attempt in range(1, ATTEMPTS + 1):
    try:
        author = fetch_profile()
        break
    except Exception as exc:  # noqa: BLE001 - network / captcha errors are expected
        print(f"[attempt {attempt}] failed: {type(exc).__name__}: {exc}")
        if attempt < ATTEMPTS:
            # vary the wait so retries do not hammer Scholar in lockstep
            time.sleep(20 * attempt + random.randint(0, 20))

if author is None:
    raise SystemExit("could not fetch the Google Scholar profile")

publications = {}
for pub in author.get("publications", []):
    bib = pub.get("bib", {})
    publications[pub["author_pub_id"]] = {
        "title": bib.get("title", ""),
        "year": bib.get("pub_year"),
        "num_citations": pub.get("num_citations", 0),
    }

data = {
    "citedby": author.get("citedby", 0),
    "hindex": author.get("hindex", 0),
    "i10index": author.get("i10index", 0),
    "citedby5y": author.get("citedby5y", 0),
    "name": author.get("name", ""),
    "affiliation": author.get("affiliation", ""),
    "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "publications": publications,
}

# same number in the format shields.io/endpoint understands, so a plain
# <img src="https://img.shields.io/endpoint?url=...gs_data_shieldsio.json">
# can render it from any static page (jsDelivr reads it from the
# google-scholar-stats branch, exactly like RayeRen's homepage does)
shieldsio = {
    "schemaVersion": 1,
    "label": "citations",
    "message": str(data["citedby"]),
    "color": "3f78c5",
}

os.makedirs("results", exist_ok=True)
for name, payload in (("gs_data.json", data), ("gs_data_shieldsio.json", shieldsio)):
    tmp_path = "results/%s.tmp" % name
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, "results/" + name)

print(
    "citedby={citedby} hindex={hindex} publications={n} at {updated}".format(
        n=len(publications), **data
    )
)
