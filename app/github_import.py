"""Import public GitHub repos as Project rows.

Usage:
    python -m app.github_import                     # uses $GITHUB_USERNAME
    python -m app.github_import octocat             # explicit username
    python -m app.github_import --include-forks --include-archived
    python -m app.github_import --dry-run           # fetch and print, write nothing

Auth is optional: set $GITHUB_TOKEN to raise the rate limit from 60 to 5000
requests/hour. Each repo costs 2 extra calls (languages + readme), so
unauthenticated runs are limited to roughly 20 repos.
"""

import argparse
import base64
import os
import sys

import requests
from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.models import Project

API_ROOT = "https://api.github.com"
TIMEOUT = 20
PER_PAGE = 100


class GitHubError(RuntimeError):
    pass


def _session(token: str | None) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "resume-builder",
        }
    )
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    return session


def _get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    response = session.get(url, timeout=TIMEOUT, **kwargs)
    if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
        raise GitHubError(
            "GitHub rate limit exhausted. Set GITHUB_TOKEN for a higher limit, "
            "or wait for the window to reset."
        )
    if response.status_code == 401:
        raise GitHubError("GitHub rejected the token (401). Check GITHUB_TOKEN.")
    return response


def fetch_repos(session: requests.Session, username: str) -> list[dict]:
    """All public repos for a user, following pagination."""
    repos: list[dict] = []
    url = f"{API_ROOT}/users/{username}/repos"
    params = {"per_page": PER_PAGE, "type": "owner", "sort": "updated"}

    while url:
        response = _get(session, url, params=params)
        if response.status_code == 404:
            raise GitHubError(f"No such GitHub user: {username!r}")
        response.raise_for_status()
        repos.extend(response.json())
        url = response.links.get("next", {}).get("url")
        params = None  # the `next` link already carries the query string

    return repos


def fetch_languages(session: requests.Session, repo: dict) -> list[str]:
    """Languages for a repo, ordered by bytes written, descending."""
    response = _get(session, repo["languages_url"])
    if not response.ok:
        return [repo["language"]] if repo.get("language") else []
    languages = response.json()
    return sorted(languages, key=languages.get, reverse=True)


def fetch_readme(session: requests.Session, repo: dict) -> str | None:
    """Decoded README text, or None if the repo has no README."""
    response = _get(session, f"{API_ROOT}/repos/{repo['full_name']}/readme")
    if response.status_code == 404:
        return None
    if not response.ok:
        return None

    payload = response.json()
    if payload.get("encoding") != "base64":
        return payload.get("content")
    try:
        return base64.b64decode(payload["content"]).decode("utf-8", errors="replace")
    except (KeyError, ValueError):
        return None


def to_project_fields(repo: dict, languages: list[str], readme: str | None) -> dict:
    """Map a GitHub repo payload onto Project columns. Bullets stay empty."""
    return {
        "name": repo["name"],
        "description": repo.get("description"),
        "github_url": repo["html_url"],
        "tech_stack": languages,
        "source": "github",
        "github_repo_id": str(repo["id"]),
        "readme": readme,
    }


def import_repos(
    username: str,
    *,
    token: str | None = None,
    include_forks: bool = False,
    include_archived: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    """Returns (created, updated, skipped)."""
    session = _session(token)
    repos = fetch_repos(session, username)

    selected = [
        repo
        for repo in repos
        if (include_forks or not repo.get("fork"))
        and (include_archived or not repo.get("archived"))
    ]
    skipped = len(repos) - len(selected)
    if limit is not None:
        selected = selected[:limit]

    print(f"{len(repos)} repo(s) found, {skipped} skipped (fork/archived), {len(selected)} to import.")

    created = updated = 0
    with SessionLocal() as db:
        for repo in selected:
            languages = fetch_languages(session, repo)
            readme = fetch_readme(session, repo)
            fields = to_project_fields(repo, languages, readme)

            if dry_run:
                print(
                    f"  [dry-run] {fields['name']}  langs={fields['tech_stack']}  "
                    f"readme={'yes' if readme else 'no'}"
                )
                continue

            existing = db.scalar(
                select(Project).where(Project.github_repo_id == fields["github_repo_id"])
            )
            if existing:
                # Bullets are hand-written or generated later — never overwrite them.
                for key, value in fields.items():
                    setattr(existing, key, value)
                updated += 1
                print(f"  updated  {fields['name']}")
            else:
                db.add(Project(**fields, bullets=[]))
                created += 1
                print(f"  created  {fields['name']}")

        if not dry_run:
            db.commit()

    return created, updated, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="Import public GitHub repos as projects.")
    parser.add_argument(
        "username",
        nargs="?",
        default=os.environ.get("GITHUB_USERNAME"),
        help="GitHub username (defaults to $GITHUB_USERNAME).",
    )
    parser.add_argument("--include-forks", action="store_true", help="Also import forked repos.")
    parser.add_argument(
        "--include-archived", action="store_true", help="Also import archived repos."
    )
    parser.add_argument("--limit", type=int, help="Import at most N repos (useful when unauthenticated).")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print without writing.")
    args = parser.parse_args()

    if not args.username:
        parser.error("no username given and GITHUB_USERNAME is not set")

    init_db()
    try:
        created, updated, skipped = import_repos(
            args.username,
            token=os.environ.get("GITHUB_TOKEN"),
            include_forks=args.include_forks,
            include_archived=args.include_archived,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    except (GitHubError, requests.RequestException) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"Done. created={created} updated={updated} skipped={skipped}")


if __name__ == "__main__":
    main()
