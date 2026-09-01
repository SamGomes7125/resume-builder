"""Set which projects are featured.

The given names become the complete featured set: every named project is marked
featured=True and every other project is reset to featured=False.

Usage:
    python -m app.mark_featured my-repo another-repo
    python -m app.mark_featured --dry-run my-repo
    python -m app.mark_featured --list
"""

import argparse
import sys

from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.models import Project


def mark_featured(names: list[str], *, dry_run: bool = False) -> int:
    """Feature exactly `names`, unfeature the rest. Returns an exit code."""
    with SessionLocal() as db:
        projects = db.scalars(select(Project)).all()
        by_name = {p.name: p for p in projects}
        by_lower = {p.name.lower(): p for p in projects}

        targets: dict[int, Project] = {}
        unmatched: list[str] = []
        for name in names:
            match = by_name.get(name) or by_lower.get(name.lower())
            if match is None:
                unmatched.append(name)
            else:
                targets[match.id] = match

        featured, unfeatured = [], []
        for project in projects:
            should_feature = project.id in targets
            if project.featured != should_feature:
                (featured if should_feature else unfeatured).append(project.name)
            project.featured = should_feature

        if dry_run:
            db.rollback()
        else:
            db.commit()

        prefix = "[dry-run] " if dry_run else ""
        for name in featured:
            print(f"  {prefix}featured    {name}")
        for name in unfeatured:
            print(f"  {prefix}unfeatured  {name}")

        print(
            f"{prefix}{len(targets)} project(s) featured, "
            f"{len(projects) - len(targets)} not featured "
            f"({len(featured)} newly featured, {len(unfeatured)} cleared)."
        )

        if unmatched:
            print(f"warning: no project matched: {', '.join(unmatched)}", file=sys.stderr)
            return 1
        return 0


def list_projects() -> None:
    with SessionLocal() as db:
        for project in db.scalars(select(Project).order_by(Project.name)).all():
            print(f"  [{'x' if project.featured else ' '}] {project.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Feature the named projects, unfeature the rest.")
    parser.add_argument("names", nargs="*", help="Project names to mark as featured.")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without saving.")
    parser.add_argument("--list", action="store_true", help="List projects and their status.")
    args = parser.parse_args()

    init_db()

    if args.list:
        list_projects()
        return

    if not args.names:
        parser.error("give at least one project name (or --list)")

    raise SystemExit(mark_featured(args.names, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
