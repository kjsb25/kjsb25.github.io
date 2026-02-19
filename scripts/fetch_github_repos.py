#!/usr/bin/env python3
"""
Fetch public GitHub repositories for kjsb25 and write to data/github_repos.json.

Filters out forks, archived repos, and any repos listed in scripts/repo_config.yaml.
Repos with a homepage URL set on GitHub will include a demo link in the output.

Usage:
    python scripts/fetch_github_repos.py

Environment variables:
    GITHUB_TOKEN  Optional. Raises rate limit from 60 to 5000 req/hr.
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

USERNAME = "kjsb25"
SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent
CONFIG_FILE = SCRIPT_DIR / "repo_config.yaml"
OUTPUT_FILE = REPO_ROOT / "data" / "github_repos.json"


def load_exclusion_list():
    """Read the exclusion list from repo_config.yaml using stdlib only."""
    exclude = set()
    if not CONFIG_FILE.exists():
        return exclude
    with open(CONFIG_FILE) as f:
        in_exclude_block = False
        for line in f:
            stripped = line.strip()
            if stripped.startswith("exclude:"):
                in_exclude_block = True
                continue
            if in_exclude_block:
                if stripped.startswith("- "):
                    exclude.add(stripped[2:].strip())
                elif stripped and not stripped.startswith("#"):
                    in_exclude_block = False
    return exclude


def fetch_repos():
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USERNAME}-site-builder",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    repos = []
    page = 1
    while True:
        url = (
            f"https://api.github.com/users/{USERNAME}/repos"
            f"?type=public&sort=stars&per_page=100&page={page}"
        )
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:
                batch = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            print(f"GitHub API error {e.code}: {e.reason}")
            raise

        if not batch:
            break
        repos.extend(batch)
        page += 1

    return repos


def main():
    exclude = load_exclusion_list()
    print(f"Exclusion list: {exclude or '(empty)'}")

    print("Fetching repos from GitHub API...")
    all_repos = fetch_repos()
    print(f"Fetched {len(all_repos)} public repos")

    filtered = []
    for repo in all_repos:
        if repo.get("fork"):
            continue
        if repo.get("archived"):
            continue
        if repo["name"] in exclude:
            continue
        filtered.append({
            "name": repo["name"],
            "description": repo.get("description") or "",
            "html_url": repo["html_url"],
            "homepage": repo.get("homepage") or "",
            "language": repo.get("language") or "",
            "stargazers_count": repo.get("stargazers_count", 0),
        })

    # Sort by stars descending, then alphabetically for ties
    filtered.sort(key=lambda r: (-r["stargazers_count"], r["name"]))

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(filtered, f, indent=2)

    print(f"Wrote {len(filtered)} repos to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
