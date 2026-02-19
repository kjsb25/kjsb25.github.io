#!/usr/bin/env python3
"""
Fetch public GitHub repositories for kjsb25 and write to data/github_repos.json.

Only repos listed in scripts/repo_config.yaml (include list) are shown.
For each repo, fetches:
  - Base metadata (description, homepage, stars, topics, license, etc.)
  - Full language breakdown with byte counts
  - File tree for framework/tech-stack detection

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
# Checked in order; first match wins for each category.
FRAMEWORK_INDICATORS = [
    # JS frameworks (more specific before generic)
    ("next.config.js",        "Next.js"),
    ("next.config.ts",        "Next.js"),
    ("next.config.mjs",       "Next.js"),
    ("gatsby-config.js",      "Gatsby"),
    ("gatsby-config.ts",      "Gatsby"),
    ("astro.config.mjs",      "Astro"),
    ("astro.config.ts",       "Astro"),
    ("svelte.config.js",      "Svelte"),
    ("vue.config.js",         "Vue"),
    ("angular.json",          "Angular"),
    ("vite.config.js",        "Vite"),
    ("vite.config.ts",        "Vite"),
    # Java/JVM build tools
    ("pom.xml",               "Maven"),
    ("build.gradle",          "Gradle"),
    ("build.gradle.kts",      "Gradle"),
    # Python packaging
    ("pyproject.toml",        "Python"),
    ("Pipfile",               "Python"),
    ("requirements.txt",      "Python"),
    ("setup.py",              "Python"),
    # Other languages
    ("Cargo.toml",            "Rust"),
    ("go.mod",                "Go"),
    ("Gemfile",               "Ruby"),
    ("composer.json",         "PHP"),
    ("mix.exs",               "Elixir"),
    ("pubspec.yaml",          "Flutter"),
    # Infrastructure / DevOps
    ("docker-compose.yml",    "Docker Compose"),
    ("docker-compose.yaml",   "Docker Compose"),
    ("Dockerfile",            "Docker"),
    # Static site generators
    ("hugo.toml",             "Hugo"),
    ("hugo.yaml",             "Hugo"),
    ("_config.yml",           "Jekyll"),
    # .NET (match by suffix)
    (".csproj",               ".NET"),
    (".sln",                  ".NET"),
]


def load_include_list():
    """Read the include list from repo_config.yaml using stdlib only."""
    include = []
    if not CONFIG_FILE.exists():
        return include
    with open(CONFIG_FILE) as f:
        in_block = False
        for line in f:
            stripped = line.strip()
            if stripped.startswith("include:"):
                in_block = True
                continue
            if in_block:
                if stripped.startswith("- "):
                    include.append(stripped[2:].strip())
                elif stripped and not stripped.startswith("#"):
                    in_block = False
    return include


def api_get(url, headers):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"  API error {e.code} for {url}: {e.reason}")
        return None


def fetch_repo_base(name, headers):
    return api_get(
        f"https://api.github.com/repos/{USERNAME}/{name}",
        headers,
    )


def fetch_languages(name, headers):
    """Returns dict of {language: bytes}, e.g. {'JavaScript': 18453, 'CSS': 2100}"""
    result = api_get(
        f"https://api.github.com/repos/{USERNAME}/{name}/languages",
        headers,
    )
    return result or {}


def fetch_file_paths(name, default_branch, headers):
    """Returns a flat list of all file paths in the repo via the git tree API."""
    data = api_get(
        f"https://api.github.com/repos/{USERNAME}/{name}/git/trees/{default_branch}?recursive=1",
        headers,
    )
    if not data:
        return []
    if data.get("truncated"):
        print(f"  Warning: file tree truncated for {name}")
    return [entry["path"] for entry in data.get("tree", []) if entry.get("type") == "blob"]


def detect_frameworks(file_paths):
    """Scan file paths and return a list of detected framework/tool labels."""
    path_set = set(file_paths)
    # Also keep just the basenames for matching
    basenames = {Path(p).name for p in file_paths}

    detected = []
    seen_labels = set()

    for indicator, label in FRAMEWORK_INDICATORS:
        if label in seen_labels:
            continue
        # Suffix match (for .csproj, .sln)
        if indicator.startswith("."):
            if any(p.endswith(indicator) for p in file_paths):
                detected.append(label)
                seen_labels.add(label)
        # Exact basename match
        elif indicator in basenames:
            detected.append(label)
            seen_labels.add(label)

    return detected


def language_breakdown(lang_bytes):
    """Convert raw byte counts to percentage breakdown, dropping tiny (<1%) entries."""
    total = sum(lang_bytes.values())
    if not total:
        return []
    breakdown = []
    for lang, count in lang_bytes.items():
        pct = round(count / total * 100, 1)
        if pct >= 1.0:
            breakdown.append({"name": lang, "percent": pct})
    return breakdown  # already sorted desc by GitHub


def main():
    include = load_include_list()
    if not include:
        print("Include list is empty — nothing to fetch. Add repo names to scripts/repo_config.yaml.")
        # Write empty array so Hugo partial renders nothing cleanly
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_FILE, "w") as f:
            json.dump([], f)
        return

    print(f"Include list: {include}")

    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USERNAME}-site-builder",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        print("Warning: GITHUB_TOKEN not set. Using unauthenticated rate limit (60 req/hr).")

    results = []
    for name in include:
        print(f"\nFetching {name}...")

        base = fetch_repo_base(name, headers)
        if not base:
            print(f"  Skipping {name} (failed to fetch base data)")
            continue
        if base.get("private") or base.get("fork"):
            print(f"  Skipping {name} (private or fork)")
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

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nWrote {len(results)} repos to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
