"""
Practice: fetch data from a third-party API, then summarize it.

Task: For a public GitHub repo, pull recently merged pull requests and report
  - how many PRs were merged
  - median hours from open -> merge
  - top 5 authors by merged PRs
and save the raw rows to prs.csv.

Run:
  python3 -m venv .venv && source .venv/bin/activate   (Windows: .venv\\Scripts\\activate)
  pip install requests
  python fetch_prs.py rails/rails --max 200
Optional: export GITHUB_TOKEN=... for a higher rate limit (60/hr without, 5000/hr with).
"""
import argparse
import csv
import os
import statistics
import sys
import time
from collections import Counter
from datetime import datetime

import requests

API = "https://api.github.com"


def get_session():
    s = requests.Session()
    s.headers["Accept"] = "application/vnd.github+json"
    token = os.getenv("GITHUB_TOKEN")
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    return s


def get_json(session, url, params=None, retries=3):
    """GET with basic error handling and rate-limit awareness."""
    for attempt in range(retries):
        resp = session.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json(), resp.links.get("next", {}).get("url")
        if resp.status_code in (403, 429) and resp.headers.get("X-RateLimit-Remaining") == "0":
            reset = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
            sys.exit(f"Rate limited. Resets at {datetime.fromtimestamp(reset)}. Set GITHUB_TOKEN.")
        if resp.status_code >= 500:
            time.sleep(2 ** attempt)  # back off and retry server errors
            continue
        resp.raise_for_status()  # 4xx: bad repo name, auth problem, etc.
    raise RuntimeError(f"Failed after {retries} attempts: {url}")


def fetch_merged_prs(session, repo, max_prs):
    url = f"{API}/repos/{repo}/pulls"
    params = {"state": "closed", "per_page": 100, "sort": "updated", "direction": "desc"}
    rows = []
    while url and len(rows) < max_prs:
        page, url = get_json(session, url, params)
        params = None  # the 'next' link already includes the query string
        for pr in page:
            if not pr.get("merged_at"):  # closed without merging
                continue
            rows.append({
                "number": pr["number"],
                "title": pr["title"],
                "author": (pr.get("user") or {}).get("login", "ghost"),
                "created_at": pr["created_at"],
                "merged_at": pr["merged_at"],
            })
            if len(rows) >= max_prs:
                break
    return rows


def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", nargs="?", default="rails/rails", help="owner/name, e.g. rails/rails")
    ap.add_argument("--max", type=int, default=200)
    args = ap.parse_args()

    rows = fetch_merged_prs(get_session(), args.repo, args.max)
    if not rows:
        print("No merged PRs found.")
        return

    for r in rows:
        r["hours_to_merge"] = round((parse(r["merged_at"]) - parse(r["created_at"])).total_seconds() / 3600, 1)

    with open("prs.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    print(f"Merged PRs: {len(rows)}")
    print(f"Median hours to merge: {statistics.median(r['hours_to_merge'] for r in rows)}")
    print("Top authors:")
    for author, n in Counter(r["author"] for r in rows).most_common(5):
        print(f"  {author}: {n}")
    print("Saved prs.csv")


if __name__ == "__main__":
    main()
