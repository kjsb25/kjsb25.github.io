#!/usr/bin/env python3
"""
Fetch public GitHub repositories for kjsb25 and write to data/github_repos.json.

On every run:
  - All public non-fork repos are discovered from the GitHub API.
  - Repos already listed under `include` in repo_config.yaml are shown on
    the site (with full tech-stack enrichment).
  - Repos already listed under `exclude` are skipped silently.
  - Any newly discovered repo not yet in the config is added to `exclude`
    automatically so the config stays current.

To show a repo: move its name from `exclude` to `include` in repo_config.yaml.

For each included repo, fetches:
  - Base metadata (description, homepage, stars, topics, license, etc.)
  - Full language breakdown with byte counts -> percentages
  - Recursive file tree for framework/tech-stack detection

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

# Map from filenames/patterns found in repo tree to framework/tool labels.
# Checked in order; first match wins per label.
FRAMEWORK_INDICATORS = [
    # JS frameworks (more specific before generic)
    ("next.config.js",       "Next.js"),
    ("next.config.ts",       "Next.js"),
    ("next.config.mjs",      "Next.js"),
    ("gatsby-config.js",     "Gatsby"),
    ("gatsby-config.ts",     "Gatsby"),
    ("astro.config.mjs",     "Astro"),
    ("astro.config.ts",      "Astro"),
    ("svelte.config.js",     "Svelte"),
    ("vue.config.js",        "Vue"),
    ("angular.json",         "Angular"),
    ("vite.config.js",       "Vite"),
    ("vite.config.ts",       "Vite"),
    # Java/JVM build tools
    ("pom.xml",              "Maven"),
    ("build.gradle",         "Gradle"),
    ("build.gradle.kts",     "Gradle"),
    # Python packaging
    ("pyproject.toml",       "Python"),
    ("Pipfile",              "Python"),
    ("requirements.txt",     "Python"),
    ("setup.py",             "Python"),
    # Other languages
    ("Cargo.toml",           "Rust"),
    ("go.mod",               "Go"),
    ("Gemfile",              "Ruby"),
    ("composer.json",        "PHP"),
    ("mix.exs",              "Elixir"),
    ("pubspec.yaml",         "Flutter"),
    # Infrastructure / DevOps
    ("docker-compose.yml",   "Docker Compose"),
    ("docker-compose.yaml",  "Docker Compose"),
    ("Dockerfile",           "Docker"),
    # Static site generators
    ("hugo.toml",            "Hugo"),
    ("hugo.yaml",            "Hugo"),
    ("_config.yml",          "Jekyll"),
    # .NET (match by suffix)
    (".csproj",              ".NET"),
    (".sln",                 ".NET"),
]


# ---------------------------------------------------------------------------
# Config file I/O
# ---------------------------------------------------------------------------

def load_config():
    """
    Parse repo_config.yaml and return (include_list, exclude_list).
    Uses stdlib only (no PyYAML dependency).
    """
    include, exclude = [], []
    if not CONFIG_FILE.exists():
        return include, exclude

    current_list = None
    with open(CONFIG_FILE) as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("include:"):
                current_list = include
            elif stripped.startswith("exclude:"):
                current_list = exclude
            elif stripped.startswith("- ") and current_list is not None:
                current_list.append(stripped[2:].strip())
            elif stripped and not stripped.startswith("#"):
                # Any non-empty, non-comment, non-list line resets context
                current_list = None

    return include, exclude


def write_config(include, exclude):
    """
    Write repo_config.yaml back out preserving the comment header,
    with sorted lists under each section.
    """
    lines = [
        "# Repos to show on the site (full tech-stack enrichment fetched for these).\n",
        "# Move a name from `exclude` to `include` to display it.\n",
        "include:\n",
    ]
    for name in sorted(include):
        lines.append(f"  - {name}\n")

    lines.append("\n")
    lines.append("# Repos discovered but not shown. Move entries to `include` to display them.\n")
    lines.append("exclude:\n")
    for name in sorted(exclude):
        lines.append(f"  - {name}\n")

    with open(CONFIG_FILE, "w") as f:
        f.writelines(lines)


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def make_headers():
    token = os.environ.get("GITHUB_TOKEN", "")
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USERNAME}-site-builder",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    else:
        print("Warning: GITHUB_TOKEN not set. Using unauthenticated rate limit (60 req/hr).")
    return h


def api_get(url, headers):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"  API error {e.code} for {url}: {e.reason}")
        return None


def fetch_all_public_repos(headers):
    """Fetch all public non-fork repos for USERNAME, paginated."""
    repos = []
    page = 1
    while True:
        batch = api_get(
            f"https://api.github.com/users/{USERNAME}/repos"
            f"?type=public&sort=full_name&per_page=100&page={page}",
            headers,
        )
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return [r for r in repos if not r.get("fork") and not r.get("archived")]


def fetch_languages(name, headers):
    result = api_get(f"https://api.github.com/repos/{USERNAME}/{name}/languages", headers)
    return result or {}


def fetch_file_paths(name, default_branch, headers):
    data = api_get(
        f"https://api.github.com/repos/{USERNAME}/{name}/git/trees/{default_branch}?recursive=1",
        headers,
    )
    if not data:
        return []
    if data.get("truncated"):
        print(f"  Warning: file tree truncated for {name}")
    return [e["path"] for e in data.get("tree", []) if e.get("type") == "blob"]


# ---------------------------------------------------------------------------
# Tech-stack detection
# ---------------------------------------------------------------------------

def detect_frameworks(file_paths):
    basenames = {Path(p).name for p in file_paths}
    detected, seen = [], set()
    for indicator, label in FRAMEWORK_INDICATORS:
        if label in seen:
            continue
        if indicator.startswith("."):
            if any(p.endswith(indicator) for p in file_paths):
                detected.append(label)
                seen.add(label)
        elif indicator in basenames:
            detected.append(label)
            seen.add(label)
    return detected


def language_breakdown(lang_bytes):
    total = sum(lang_bytes.values())
    if not total:
        return []
    breakdown = []
    for lang, count in lang_bytes.items():
        pct = round(count / total * 100, 1)
        if pct >= 1.0:
            breakdown.append({"name": lang, "percent": pct})
    return breakdown  # already sorted desc by GitHub


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    headers = make_headers()

    # 1. Discover all current public non-fork repos
    print("Fetching all public repos from GitHub API...")
    all_repos = fetch_all_public_repos(headers)
    all_names = {r["name"] for r in all_repos}
    repo_map = {r["name"]: r for r in all_repos}
    print(f"Found {len(all_names)} public non-fork repos")

    # 2. Load existing config
    include, exclude = load_config()
    include_set = set(include)
    exclude_set = set(exclude)

    # 3. Find repos not yet categorised and add them to exclude
    known = include_set | exclude_set
    new_repos = sorted(all_names - known)
    if new_repos:
        print(f"New repos discovered, adding to exclude: {new_repos}")
        exclude_set.update(new_repos)

    # 4. Remove any stale entries (repos that no longer exist)
    stale_include = include_set - all_names
    stale_exclude = exclude_set - all_names
    if stale_include:
        print(f"Removing stale entries from include: {stale_include}")
        include_set -= stale_include
    if stale_exclude:
        print(f"Removing stale entries from exclude: {stale_exclude}")
        exclude_set -= stale_exclude

    # 5. Write updated config
    write_config(sorted(include_set), sorted(exclude_set))
    print(f"Config updated: {len(include_set)} included, {len(exclude_set)} excluded")

    # 6. Enrich and collect only included repos
    results = []
    for name in sorted(include_set):
        print(f"\nEnriching {name}...")
        base = repo_map.get(name)
        if not base:
            print(f"  Skipping {name} (not found in API results)")
            continue

        default_branch = base.get("default_branch", "main")

        lang_bytes = fetch_languages(name, headers)
        print(f"  Languages: {dict(list(lang_bytes.items())[:5])}")

        file_paths = fetch_file_paths(name, default_branch, headers)
        print(f"  Files in tree: {len(file_paths)}")

        frameworks = detect_frameworks(file_paths)
        print(f"  Detected frameworks: {frameworks}")

        license_info = base.get("license") or {}
        results.append({
            "name": base["name"],
            "description": base.get("description") or "",
            "html_url": base["html_url"],
            "homepage": base.get("homepage") or "",
            "primary_language": base.get("language") or "",
            "languages": language_breakdown(lang_bytes),
            "frameworks": frameworks,
            "topics": base.get("topics") or [],
            "stargazers_count": base.get("stargazers_count", 0),
            "license": license_info.get("spdx_id") or "",
            "has_pages": base.get("has_pages", False),
        })

    # Sort by stars desc, then name
    results.sort(key=lambda r: (-r["stargazers_count"], r["name"]))

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nWrote {len(results)} repos to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
