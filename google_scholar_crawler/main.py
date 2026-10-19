"""Crawl the Google Scholar profile and write a compact JSON snapshot.

Run by .github/workflows/google_scholar_crawler.yaml on a schedule; the output is
copied to google-scholar-stats/gs_data.json and read by index.html at runtime.

The snapshot is deliberately slim (title / year / citations per paper) so the file
stays small and the page only downloads what it renders.

Resilience strategy:
1. try scholarly directly (fast path);
2. on failure, retry a few times with jittered backoff;
3. on continued failure, retry through scholarly's FreeProxies pool
   (GitHub-hosted runners' IPs are frequently flagged by Google Scholar);
4. if everything fails (or anything crashes anywhere), still write the snapshot
   with a "last_error" field and exit 0 — the deployed JSON doubles as a public
   diagnostic record, and the page keeps rendering the previous numbers.
"""

import json
import os
import random
import sys
import time
import traceback
from datetime import datetime, timezone

import requests

# scholarly issues requests without a timeout; on GitHub-hosted runners the
# connection to Google can silently hang forever, burning the whole job
# timeout with zero output. Force a global ceiling on every request.
_ORIG_REQUEST = requests.Session.request


def _request_with_timeout(self, *args, **kwargs):
    kwargs.setdefault("timeout", 45)
    return _ORIG_REQUEST(self, *args, **kwargs)


requests.Session.request = _request_with_timeout

from scholarly import scholarly  # noqa: E402  (needs the timeout patch first)

SCHOLAR_ID = os.environ.get("GOOGLE_SCHOLAR_ID", "LHzzlAgAAAAJ")
ATTEMPTS_DIRECT = 3
ATTEMPTS_PROXY = 2

RESULTS_DIR = "results"
ERRORS = []


def fetch_profile():
    author = scholarly.search_author_id(SCHOLAR_ID)
    return scholarly.fill(author, sections=["basics", "indices", "counts", "publications"])


def fetch_profile_via_free_proxies():
    from scholarly import ProxyGenerator

    pg = ProxyGenerator()
    pg.FreeProxies()
    scholarly.use_proxy(pg)
    return fetch_profile()


def run_with_retries(fn, attempts, label):
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - network / captcha errors are expected
            msg = "{}#{}: {}: {}".format(label, attempt, type(exc).__name__, str(exc)[:200])
            print("[{} attempt {}] failed: {}: {}".format(label, attempt, type(exc).__name__, exc))
            ERRORS.append(msg)
            if attempt < attempts:
                time.sleep(15 * attempt + random.randint(0, 15))
    return None


def write_outputs(data, shieldsio):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    for name, payload in (("gs_data.json", data), ("gs_data_shieldsio.json", shieldsio)):
        tmp_path = os.path.join(RESULTS_DIR, name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, os.path.join(RESULTS_DIR, name))


def emit_failure():
    """Record the failure in the public JSON and exit 0 so the workflow can
    still deploy it. The reason becomes readable at
    https://<site>/google-scholar-stats/gs_data.json"""
    fallback_path = os.path.join(RESULTS_DIR, "gs_data.json")
    previous = {}
    if os.path.exists(fallback_path):
        try:
            with open(fallback_path, encoding="utf-8") as fh:
                previous = json.load(fh)
        except Exception:  # noqa: BLE001
            previous = {}
    previous.setdefault("citedby", 0)
    previous.setdefault("publications", {})
    previous["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    previous["crawl_status"] = "failed"
    previous["last_error"] = ("; ".join(ERRORS[-4:]) or "unexpected crash") + " at " + previous["updated"]
    write_outputs(previous, {
        "schemaVersion": 1,
        "label": "citations",
        "message": str(previous.get("citedby", 0)),
        "color": "3f78c5",
    })
    print(previous["last_error"])
    sys.exit(0)


def main():
    author = run_with_retries(fetch_profile, ATTEMPTS_DIRECT, "direct")
    if author is None:
        author = run_with_retries(fetch_profile_via_free_proxies, ATTEMPTS_PROXY, "free-proxy")
    if author is None:
        emit_failure()

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
        "crawl_status": "ok",
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

    write_outputs(data, shieldsio)
    print(
        "citedby={citedby} hindex={hindex} publications={n} at {updated}".format(
            citedby=data["citedby"],
            hindex=data["hindex"],
            n=len(publications),
            updated=data["updated"],
        )
    )


try:
    main()
except SystemExit:
    raise
except Exception:  # noqa: BLE001 - never let the pipeline go red without a record
    ERRORS.append("CRASH: " + traceback.format_exc()[-500:])
    emit_failure()

# bump: trigger crawler run
