#!/usr/bin/env python3
"""
Fetch public GitHub repositories for kjsb25 and write to data/github_repos.json.

On every run:
  - All public repos are discovered from the GitHub API.
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
  - package.json (if present) for marketable npm dependency labels

Usage:
    python scripts/fetch_github_repos.py

Environment variables:
    GITHUB_TOKEN  Optional. Raises rate limit from 60 to 5000 req/hr.
"""

import base64
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
# Map from Python import names to marketable tech labels.
# Keys are top-level module names as they appear in `import X` or `from X import ...`.
# None = suppress (don't surface as a tag).
PYTHON_IMPORT_LABELS = {
    # ── ML / Data Science ─────────────────────────────────────────────────
    "sklearn":          "scikit-learn",
    "torch":            "PyTorch",
    "tensorflow":       "TensorFlow",
    "keras":            "Keras",
    "xgboost":          "XGBoost",
    "lightgbm":         "LightGBM",
    "catboost":         "CatBoost",
    "transformers":     "Hugging Face",
    "diffusers":        "Hugging Face",
    # ── Numerical / Scientific ────────────────────────────────────────────
    "numpy":            "NumPy",
    "np":               None,               # alias — numpy is the canonical key
    "scipy":            "SciPy",
    "pandas":           "pandas",
    "polars":           "Polars",
    "sympy":            "SymPy",
    # ── Computer Vision ───────────────────────────────────────────────────
    "cv2":              "OpenCV",
    "PIL":              "Pillow",
    "skimage":          "scikit-image",
    "imageio":          None,               # utility, not marketable alone
    # ── Visualisation ─────────────────────────────────────────────────────
    "matplotlib":       "Matplotlib",
    "pylab":            None,               # matplotlib alias
    "seaborn":          "Seaborn",
    "plotly":           "Plotly",
    "bokeh":            "Bokeh",
    # ── Web frameworks ────────────────────────────────────────────────────
    "flask":            "Flask",
    "fastapi":          "FastAPI",
    "django":           "Django",
    "aiohttp":          "aiohttp",
    "tornado":          "Tornado",
    "starlette":        "Starlette",
    # ── Databases / ORM ───────────────────────────────────────────────────
    "sqlalchemy":       "SQLAlchemy",
    "pymongo":          "MongoDB",
    "redis":            "Redis",
    "psycopg2":         "PostgreSQL",
    "pymysql":          "MySQL",
    "motor":            "MongoDB",
    # ── Async / Networking ────────────────────────────────────────────────
    "asyncio":          None,               # stdlib
    "requests":         None,               # too generic
    "httpx":            None,               # too generic
    "websockets":       "WebSockets",
    # ── Hardware / IoT ────────────────────────────────────────────────────
    "picamera":         "Raspberry Pi Camera",
    "RPi":              "Raspberry Pi",
    "smbus":            None,
    "serial":           None,
    # ── Game / Simulation ─────────────────────────────────────────────────
    "pygame":           "Pygame",
    # ── Cloud / Infrastructure ────────────────────────────────────────────
    "boto3":            "AWS SDK",
    "google":           None,               # too broad
    "azure":            "Azure SDK",
    "openai":           "OpenAI SDK",
    "anthropic":        "Anthropic SDK",
    # ── Automation / Testing ──────────────────────────────────────────────
    "selenium":         "Selenium",
    "playwright":       "Playwright",
    "pytest":           None,               # test tooling, not marketable
    # ── Suppress stdlib and other noise ───────────────────────────────────
    "os":               None,
    "sys":              None,
    "re":               None,
    "json":             None,
    "math":             None,
    "time":             None,
    "datetime":         None,
    "io":               None,
    "glob":             None,
    "pathlib":          None,
    "pickle":           None,
    "threading":        None,
    "subprocess":       None,
    "logging":          None,
    "typing":           None,
    "collections":      None,
    "itertools":        None,
    "functools":        None,
    "random":           None,
    "copy":             None,
    "abc":              None,
    "tty":              None,
}

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

# Map from npm package name to a marketable tech label.
# Keys are exact package names (including scope where needed).
# A package can map to None to explicitly suppress it (noise packages).
# Evaluated after all deps are collected; labels are deduplicated.
NPM_PACKAGE_LABELS = {
    # ── Frameworks & meta-frameworks ──────────────────────────────────────
    "react":                    "React",
    "react-dom":                None,           # same as react
    "react-native":             "React Native",
    "next":                     "Next.js",
    "nuxt":                     "Nuxt",
    "vue":                      "Vue",
    "@angular/core":            "Angular",
    "svelte":                   "Svelte",
    "@sveltejs/kit":            "SvelteKit",
    "solid-js":                 "Solid.js",
    "gatsby":                   "Gatsby",
    "astro":                    "Astro",
    "remix":                    "Remix",
    "@remix-run/react":         "Remix",
    # ── State management ──────────────────────────────────────────────────
    "redux":                    "Redux",
    "@reduxjs/toolkit":         "Redux Toolkit",
    "zustand":                  "Zustand",
    "mobx":                     "MobX",
    "recoil":                   "Recoil",
    "jotai":                    "Jotai",
    "@tanstack/react-query":    "React Query",
    "react-query":              "React Query",
    "swr":                      "SWR",
    # ── UI libraries ──────────────────────────────────────────────────────
    "@mui/material":            "Material UI",
    "@material-ui/core":        "Material UI",
    "antd":                     "Ant Design",
    "@chakra-ui/react":         "Chakra UI",
    "tailwindcss":              "Tailwind CSS",
    "@radix-ui/react-dialog":   "Radix UI",   # representative package
    "shadcn-ui":                "shadcn/ui",
    "styled-components":        "styled-components",
    "@emotion/react":           "Emotion",
    "bootstrap":                "Bootstrap",
    "react-bootstrap":          "Bootstrap",
    # ── Routing ───────────────────────────────────────────────────────────
    "react-router":             "React Router",
    "react-router-dom":         "React Router",
    "wouter":                   "Wouter",
    # ── Build tools ───────────────────────────────────────────────────────
    "vite":                     "Vite",
    "webpack":                  "Webpack",
    "esbuild":                  "esbuild",
    "rollup":                   "Rollup",
    "parcel":                   "Parcel",
    "turbo":                    "Turborepo",
    # ── Backend / server ──────────────────────────────────────────────────
    "express":                  "Express",
    "fastify":                  "Fastify",
    "koa":                      "Koa",
    "hono":                     "Hono",
    "nestjs":                   "NestJS",
    "@nestjs/core":             "NestJS",
    "hapi":                     "Hapi",
    "socket.io":                "Socket.IO",
    "ws":                       None,           # low-level, not marketable alone
    # ── Databases & ORMs ──────────────────────────────────────────────────
    "prisma":                   "Prisma",
    "@prisma/client":           "Prisma",
    "typeorm":                  "TypeORM",
    "sequelize":                "Sequelize",
    "drizzle-orm":              "Drizzle ORM",
    "mongoose":                 "Mongoose",
    "pg":                       "PostgreSQL",
    "mysql2":                   "MySQL",
    "better-sqlite3":           "SQLite",
    "redis":                    "Redis",
    "ioredis":                  "Redis",
    "@supabase/supabase-js":    "Supabase",
    "firebase":                 "Firebase",
    # ── Auth ──────────────────────────────────────────────────────────────
    "next-auth":                "NextAuth",
    "@auth/core":               "Auth.js",
    "passport":                 "Passport.js",
    "jsonwebtoken":             "JWT",
    # ── Testing ───────────────────────────────────────────────────────────
    # (kept because they're genuinely marketable)
    "jest":                     "Jest",
    "vitest":                   "Vitest",
    "@playwright/test":         "Playwright",
    "cypress":                  "Cypress",
    "@testing-library/react":   "Testing Library",
    "mocha":                    "Mocha",
    "chai":                     None,           # companion to mocha, not standalone
    # ── GraphQL ───────────────────────────────────────────────────────────
    "graphql":                  "GraphQL",
    "@apollo/client":           "Apollo Client",
    "apollo-server":            "Apollo Server",
    "@apollo/server":           "Apollo Server",
    "urql":                     "urql",
    # ── API / data fetching ───────────────────────────────────────────────
    "axios":                    "Axios",
    "trpc":                     "tRPC",
    "@trpc/server":             "tRPC",
    "openai":                   "OpenAI SDK",
    # ── TypeScript tooling ────────────────────────────────────────────────
    "typescript":               "TypeScript",
    "zod":                      "Zod",
    "yup":                      None,           # validation, less prominent
    # ── Mobile / cross-platform ───────────────────────────────────────────
    "expo":                     "Expo",
    "electron":                 "Electron",
    "tauri":                    "Tauri",
    # ── Monorepo / tooling ────────────────────────────────────────────────
    "nx":                       "Nx",
    "lerna":                    None,           # internal tooling
    # ── Map common noise to None (suppressed) ─────────────────────────────
    "eslint":                   None,
    "prettier":                 None,
    "husky":                    None,
    "lint-staged":              None,
    "rimraf":                   None,
    "cross-env":                None,
    "dotenv":                   None,
    "concurrently":             None,
    "nodemon":                  None,
    "ts-node":                  None,
    "tsup":                     None,
    "postcss":                  None,
    "autoprefixer":             None,
    "classnames":               None,
    "clsx":                     None,
    "lodash":                   None,
    "date-fns":                 None,
    "dayjs":                    None,
    "uuid":                     None,
    "nanoid":                   None,
}


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
    """Fetch all public repos for USERNAME, paginated."""
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
    return [r for r in repos if not r.get("archived")]


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


def fetch_python_imports(name, default_branch, file_paths, headers):
    """
    Fetch up to MAX_PY_FILES Python files from the repo root (depth-1 .py files)
    and extract top-level import names.  Returns a set of module name strings.
    Uses raw.githubusercontent.com to avoid contents-API auth issues with
    cross-repo tokens in GitHub Actions, and to avoid burning API rate limit.
    """
    MAX_PY_FILES = 10
    # Only consider .py files directly in the repo root (no subdir vendor code)
    root_py = [p for p in file_paths if p.endswith(".py") and "/" not in p][:MAX_PY_FILES]
    if not root_py:
        return set()

    import_names = set()
    for path in root_py:
        url = f"https://raw.githubusercontent.com/{USERNAME}/{name}/{default_branch}/{path}"
        req = urllib.request.Request(url, headers={"User-Agent": f"{USERNAME}-site-builder"})
        try:
            with urllib.request.urlopen(req) as resp:
                content = resp.read().decode("utf-8", errors="replace")
        except Exception:
            continue
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("import "):
                # "import foo, bar as baz" -> ["foo", "bar"]
                for part in line[7:].split(","):
                    mod = part.strip().split()[0].split(".")[0]
                    if mod:
                        import_names.add(mod)
            elif line.startswith("from "):
                # "from foo.bar import ..." -> "foo"
                mod = line[5:].split()[0].split(".")[0]
                if mod:
                    import_names.add(mod)
    return import_names


def extract_python_tech(import_names):
    """
    Given a set of top-level module names, return deduplicated marketable labels.
    """
    seen_labels, result = set(), []
    for mod in import_names:
        if mod not in PYTHON_IMPORT_LABELS:
            continue
        label = PYTHON_IMPORT_LABELS[mod]
        if label is None or label in seen_labels:
            continue
        result.append(label)
        seen_labels.add(label)
    return result


def fetch_package_json(name, default_branch, headers):
    """
    Fetch and decode package.json from the repo root.
    Returns the parsed dict, or None if not present or unparseable.
    """
    data = api_get(
        f"https://api.github.com/repos/{USERNAME}/{name}/contents/package.json"
        f"?ref={default_branch}",
        headers,
    )
    if not data or data.get("encoding") != "base64":
        return None
    try:
        content = base64.b64decode(data["content"]).decode("utf-8")
        return json.loads(content)
    except Exception as e:
        print(f"  Warning: could not parse package.json for {name}: {e}")
        return None


def extract_npm_tech(pkg_json):
    """
    Given a parsed package.json dict, return a deduplicated list of
    marketable tech labels derived from dependencies and devDependencies.
    Unknown packages are silently ignored; suppressed packages (None) are dropped.
    """
    if not pkg_json:
        return []
    all_deps = {}
    all_deps.update(pkg_json.get("dependencies") or {})
    all_deps.update(pkg_json.get("devDependencies") or {})

    seen_labels, result = set(), []
    for pkg in all_deps:
        if pkg not in NPM_PACKAGE_LABELS:
            continue
        label = NPM_PACKAGE_LABELS[pkg]
        if label is None or label in seen_labels:
            continue
        result.append(label)
        seen_labels.add(label)

    return result


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

    # 1. Discover all current public repos
    print("Fetching all public repos from GitHub API...")
    all_repos = fetch_all_public_repos(headers)
    all_names = {r["name"] for r in all_repos}
    repo_map = {r["name"]: r for r in all_repos}
    print(f"Found {len(all_names)} public repos")

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

        # If the repo is primarily Python, scan imports for popular library labels.
        basenames = {Path(p).name for p in file_paths}
        primary_lang = base.get("language") or ""
        if primary_lang == "Python":
            import_names = fetch_python_imports(name, default_branch, file_paths, headers)
            if import_names:
                print(f"  Python imports detected: {sorted(import_names)}")
            py_tech = extract_python_tech(import_names)
            existing = set(frameworks)
            for label in py_tech:
                if label not in existing:
                    frameworks.append(label)
                    existing.add(label)

        # If there's a package.json, enrich with marketable npm tech labels.
        # Merge with file-tree detection, deduplicating by label.
        if "package.json" in basenames:
            pkg_json = fetch_package_json(name, default_branch, headers)
            npm_tech = extract_npm_tech(pkg_json)
            existing = set(frameworks)
            for label in npm_tech:
                if label not in existing:
                    frameworks.append(label)
                    existing.add(label)

        print(f"  Detected tech: {frameworks}")

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
