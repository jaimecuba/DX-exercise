"""
Fetch merged pull requests from the GitHub API and write them to a CSV file.

For each merged PR, the CSV includes:
  - Author details (GitHub user)
  - Merger details (the user who merged the PR)
  - PR details: additions, deletions, created/merged timestamps,
    and the time between creation and merge

Usage:
  export GITHUB_TOKEN=your_token_here      # recommended (needed for private repos)
  python merged_prs_to_csv.py OWNER/REPO
  python merged_prs_to_csv.py OWNER/REPO --max 50 --out merged_prs.csv

Requires: pip install requests
"""

import argparse
import csv
import os
import sys
from datetime import datetime

import requests

API_URL = "https://api.github.com"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def make_session():
    """Create a reusable HTTP session with GitHub headers (and token, if set)."""
    session = requests.Session()
    session.headers["Accept"] = "application/vnd.github+json"
    token = os.getenv("GITHUB_TOKEN")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    return session


def get(session, url, params=None):
    """GET a URL and return the response, exiting with a clear message on errors."""
    response = session.get(url, params=params, timeout=30)

    if response.status_code == 404:
        sys.exit(f"Not found: {url} (check the repo name, or set GITHUB_TOKEN for private repos)")
    if response.status_code == 401:
        sys.exit("Unauthorized: GITHUB_TOKEN is invalid or expired.")
    if response.status_code in (403, 429) and response.headers.get("X-RateLimit-Remaining") == "0":
        sys.exit("GitHub rate limit reached. Set GITHUB_TOKEN or try again later.")

    response.raise_for_status()  # any other 4xx/5xx raises an error
    return response


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def list_merged_pr_numbers(session, repo, max_prs):
    """Page through closed PRs and return the numbers of the ones that were merged.

    The list endpoint includes closed-but-not-merged PRs, so we keep only
    those with a merged_at timestamp.
    """
    url = f"{API_URL}/repos/{repo}/pulls"
    params = {"state": "closed", "per_page": 100, "sort": "updated", "direction": "desc"}
    numbers = []

    while url and len(numbers) < max_prs:
        response = get(session, url, params)
        for pr in response.json():
            if pr.get("merged_at"):
                numbers.append(pr["number"])
                if len(numbers) >= max_prs:
                    break
        # GitHub puts the next page's URL in the "Link" header; requests parses it for us.
        url = response.links.get("next", {}).get("url")
        params = None  # the "next" URL already contains the query string

    return numbers


def get_pr_details(session, repo, number):
    """Fetch a single PR.

    The single-PR endpoint includes fields the list endpoint does not:
    additions, deletions, and merged_by.
    """
    return get(session, f"{API_URL}/repos/{repo}/pulls/{number}").json()


def get_user_details(session, login, cache):
    """Fetch a user's full profile (name, company, etc.), caching by login
    so each user is only requested once."""
    if login not in cache:
        cache[login] = get(session, f"{API_URL}/users/{login}").json()
    return cache[login]


# ---------------------------------------------------------------------------
# Transformation
# ---------------------------------------------------------------------------

def parse_timestamp(value):
    """Convert a GitHub timestamp like '2026-09-01T10:00:00Z' into a datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_duration(seconds):
    """Turn seconds into a readable string like '2d 03:15:42'."""
    seconds = int(seconds)
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"


def user_columns(prefix, user):
    """Flatten a GitHub user object into prefixed CSV columns."""
    user = user or {}
    return {
        f"{prefix}_login": user.get("login"),
        f"{prefix}_id": user.get("id"),
        f"{prefix}_name": user.get("name"),
        f"{prefix}_email": user.get("email"),        # only set if the user made it public
        f"{prefix}_company": user.get("company"),
        f"{prefix}_type": user.get("type"),          # "User" or "Bot"
        f"{prefix}_profile_url": user.get("html_url"),
    }


def build_row(session, pr, user_cache):
    """Build one CSV row from a detailed PR object."""
    author_login = (pr.get("user") or {}).get("login")
    # merged_by can be missing in some cases; fall back to the author,
    # since in this exercise the author is also the merger.
    merger_login = (pr.get("merged_by") or {}).get("login") or author_login

    author = get_user_details(session, author_login, user_cache) if author_login else None
    merger = get_user_details(session, merger_login, user_cache) if merger_login else None

    created = parse_timestamp(pr["created_at"])
    merged = parse_timestamp(pr["merged_at"])
    seconds_to_merge = (merged - created).total_seconds()

    row = {
        "pr_number": pr["number"],
        "pr_title": pr["title"],
        "pr_url": pr["html_url"],
    }
    row.update(user_columns("author", author))
    row.update(user_columns("merged_by", merger))
    row.update({
        "additions": pr.get("additions"),
        "deletions": pr.get("deletions"),
        "created_at": pr["created_at"],
        "merged_at": pr["merged_at"],
        "time_to_merge_seconds": int(seconds_to_merge),
        "time_to_merge_hours": round(seconds_to_merge / 3600, 2),
        "time_to_merge": format_duration(seconds_to_merge),
    })
    return row


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Export merged GitHub PRs to CSV.")
    parser.add_argument("repo", help="Repository in OWNER/REPO format")
    parser.add_argument("--max", type=int, default=50, help="Maximum number of merged PRs (default 50)")
    parser.add_argument("--out", default="merged_prs.csv", help="Output CSV path (default merged_prs.csv)")
    args = parser.parse_args()

    session = make_session()
    user_cache = {}

    print(f"Finding merged PRs in {args.repo}...")
    numbers = list_merged_pr_numbers(session, args.repo, args.max)
    if not numbers:
        print("No merged PRs found.")
        return

    rows = []
    for i, number in enumerate(numbers, start=1):
        print(f"  [{i}/{len(numbers)}] PR #{number}")
        pr = get_pr_details(session, args.repo, number)
        rows.append(build_row(session, pr, user_cache))

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} merged PRs to {args.out}")


if __name__ == "__main__":
    main()
