"""GitHub source-code fetching for the interpretation pipeline.

Resolves GitHub file and repository URLs and returns concatenated source
text (respecting a byte cap). No authentication required for public repos;
the httpx client is used for its built-in redirect/TLS handling.

Storage-agnostic: does not know about Mongo, FastAPI, or the CLI. Adapters
(ServerSoftwareFetcher / LocalSoftwareFetcher) call `prefetch_software_code`
as the GitHub branch of their resolution logic.
"""

from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 50KB per software entity.
MAX_SOFTWARE_BYTES = 50_000

CODE_EXTENSIONS = {
    ".py", ".r", ".R", ".sh", ".pl", ".java", ".scala", ".jl", ".m",
    ".cpp", ".go", ".rs", ".ipynb", ".md",
}

GITHUB_REPO_PATTERN = re.compile(
    r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/.*)?$"
)
GITHUB_FILE_PATTERN = re.compile(
    r"https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)"
)


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def _fetch_github_file(owner: str, repo: str, branch: str, path: str) -> str:
    """Fetch a single file from GitHub via raw URL."""
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    try:
        resp = httpx.get(raw_url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning(f"Failed to fetch {raw_url}: {e}")
        return ""


def _fetch_github_repo_code(owner: str, repo: str, max_bytes: int = MAX_SOFTWARE_BYTES) -> str:
    """Fetch code files from a GitHub repo up to max_bytes total."""
    try:
        # Get default branch
        api_url = f"https://api.github.com/repos/{owner}/{repo}"
        resp = httpx.get(api_url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        default_branch = resp.json().get("default_branch", "main")

        # Get tree
        tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{default_branch}?recursive=1"
        resp = httpx.get(tree_url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        tree = resp.json().get("tree", [])

        # Filter for code files
        code_files = []
        for item in tree:
            if item.get("type") != "blob":
                continue
            path = item.get("path", "")
            ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
            if ext.lower() in {e.lower() for e in CODE_EXTENSIONS}:
                code_files.append(path)

        # Sort: prefer top-level files, then by name
        code_files.sort(key=lambda p: (p.count("/"), p))

        # Fetch files up to limit
        collected = []
        total_bytes = 0
        for path in code_files:
            if total_bytes >= max_bytes:
                collected.append(f"\n--- [Truncated: reached {max_bytes} byte limit] ---\n")
                break
            content = _fetch_github_file(owner, repo, default_branch, path)
            if content:
                header = f"\n{'='*60}\n# FILE: {path}\n{'='*60}\n"
                collected.append(header + content)
                total_bytes += len(content.encode("utf-8"))

        return "\n".join(collected)

    except Exception as e:
        logger.warning(f"Failed to fetch repo {owner}/{repo}: {e}")
        return f"[Could not fetch repository code: {e}]"


def prefetch_software_code(content_url: str) -> str:
    """Fetch source code from a contentUrl, handling GitHub URLs.

    - GitHub file URL (`github.com/owner/repo/blob/branch/path`): single-file fetch.
    - GitHub repo URL (`github.com/owner/repo`): walks the tree and concatenates
      code files up to MAX_SOFTWARE_BYTES.
    - Non-GitHub HTTP URL: returns a placeholder string (adapter is expected
      to handle such cases upstream, e.g., via a Fairscape download endpoint).
    - Relative/local path: returns a placeholder string.
    """
    if not content_url:
        return "[No contentUrl available]"

    # Check for GitHub file URL: github.com/owner/repo/blob/branch/path
    file_match = GITHUB_FILE_PATTERN.match(content_url)
    if file_match:
        owner, repo, branch, path = file_match.groups()
        code = _fetch_github_file(owner, repo, branch, path)
        return code if code else f"[Could not fetch file from {content_url}]"

    # Check for GitHub repo URL: github.com/owner/repo
    repo_match = GITHUB_REPO_PATTERN.match(content_url)
    if repo_match:
        owner, repo = repo_match.groups()
        return _fetch_github_repo_code(owner, repo)

    # Non-GitHub HTTP URL
    if content_url.startswith("http"):
        return f"[External URL, not fetched: {content_url}]"

    return f"[Local/relative path: {content_url}]"
