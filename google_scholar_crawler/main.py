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
import signal
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
PROFILE_BUDGET = 600    # seconds; a blocked profile fetch otherwise burns the 20 min job

# Papers that are published on Google Scholar but not (yet) merged into the
# author profile. They are looked up by title search instead, and the result is
# emitted under data["extra"] so the page can fall back to it.
EXTRA_TITLES = [
    "Qwen3.8-Omni: Towards Native Omni-Modal Agents",
    "Qwen3.5-Omni Technical Report",
]
EXTRA_SCAN = 8          # how many search hits to inspect per title
EXTRA_ATTEMPTS = 2
EXTRA_BUDGET = 780      # give up on title searches this long after start (job is 20 min)
START_TS = time.time()

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


class BudgetExceeded(BaseException):
    """Raised when a phase takes longer than its wall-clock budget.

    Deliberately a BaseException: the retry helpers catch Exception broadly so
    they can absorb network/captcha errors, and a deadline must never be
    swallowed by one of those retries.
    """


def _on_alarm(signum, frame):  # noqa: ARG001
    raise BudgetExceeded("wall-clock budget exceeded")


def arm(seconds):
    """Hard deadline for the current phase (POSIX only; no-op elsewhere)."""
    try:
        signal.signal(signal.SIGALRM, _on_alarm)
        signal.alarm(int(seconds))
        return True
    except (AttributeError, ValueError):
        return False


def disarm():
    try:
        signal.alarm(0)
    except (AttributeError, ValueError):
        pass


def norm_title(value):
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def title_matches(wanted_key, found_title):
    """Strict title comparison, mirroring the page's matching rule."""
    key = norm_title(found_title)
    if not key:
        return False
    if key == wanted_key:
        return True
    if wanted_key in key or key in wanted_key:
        return len(key) >= max(12, len(wanted_key) * 0.6)
    return False


def fetch_extra():
    """Look up profile-missing papers through Scholar's title search.

    Never fatal: a blocked / empty search only means the page keeps showing "—".
    """
    extra = {}
    for title in EXTRA_TITLES:
        elapsed = time.time() - START_TS
        if elapsed > EXTRA_BUDGET:
            msg = "extra-search skipped (budget): " + title[:40]
            print("[extra] " + msg)
            ERRORS.append(msg)
            break
        wanted = norm_title(title)
        hit = None
        arm(150)  # one title: search + at most EXTRA_SCAN hits
        try:
            for attempt in range(1, EXTRA_ATTEMPTS + 1):
                try:
                    results = scholarly.search_pubs(title)
                    for idx, res in enumerate(results):
                        if idx >= EXTRA_SCAN:
                            break
                        bib = res.get("bib") or {}
                        if title_matches(wanted, bib.get("title", "")):
                            hit = {
                                "title": bib.get("title", ""),
                                "num_citations": res.get("num_citations", 0),
                            }
                            break
                    break
                except BudgetExceeded:
                    raise
                except Exception as exc:  # noqa: BLE001
                    msg = "extra-search {}#{}: {}: {}".format(
                        title[:40], attempt, type(exc).__name__, str(exc)[:160]
                    )
                    print("[extra] " + msg)
                    ERRORS.append(msg)
                    if attempt < EXTRA_ATTEMPTS:
                        time.sleep(10 * attempt + random.randint(0, 10))
        except BudgetExceeded:
            msg = "extra-search timed out: " + title[:40]
            print("[extra] " + msg)
            ERRORS.append(msg)
        finally:
            disarm()
        if hit:
            extra[title] = hit
            print("[extra] {} -> {} citations".format(title[:50], hit["num_citations"]))
        else:
            print("[extra] {} -> no strict match".format(title[:50]))
    return extra


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
    https://<site>/google-scholar-stats/gs_data.json

    The last good snapshot is reused when this run produced nothing: results/ is
    gitignored, so on a fresh checkout a failed crawl would otherwise publish
    zeros and wipe every badge on the page.
    """
    previous = {}
    for candidate in (
        os.path.join(RESULTS_DIR, "gs_data.json"),
        os.path.join("..", "google-scholar-stats", "gs_data.json"),
    ):
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate, encoding="utf-8") as fh:
                loaded = json.load(fh)
        except Exception:  # noqa: BLE001
            continue
        if (loaded.get("publications") or {}):
            previous = loaded
            break
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
    author = None
    arm(PROFILE_BUDGET)
    try:
        author = run_with_retries(fetch_profile, ATTEMPTS_DIRECT, "direct")
        if author is None:
            author = run_with_retries(fetch_profile_via_free_proxies, ATTEMPTS_PROXY, "free-proxy")
    except BudgetExceeded:
        msg = "profile fetch exceeded {}s budget".format(PROFILE_BUDGET)
        print("[profile] " + msg)
        ERRORS.append(msg)
        author = None
    finally:
        disarm()
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

    extra = fetch_extra()

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
        "extra": extra,
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
        "citedby={citedby} hindex={hindex} publications={n} extra={e} at {updated}".format(
            citedby=data["citedby"],
            hindex=data["hindex"],
            n=len(publications),
            e=len(extra),
            updated=data["updated"],
        )
    )


try:
    main()
except SystemExit:
    raise
except BudgetExceeded:
    # a deadline escaped its phase (e.g. raised while unwinding a retry helper)
    ERRORS.append("TIMEOUT: " + traceback.format_exc()[-500:])
    emit_failure()
except Exception:  # noqa: BLE001 - never let the pipeline go red without a record
    ERRORS.append("CRASH: " + traceback.format_exc()[-500:])
    emit_failure()

# bump: trigger crawler run
