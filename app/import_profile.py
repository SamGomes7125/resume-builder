"""Import a profile JSON file into the database.

The file shape is documented in profile.example.json and validated by
schemas.ProfileDocument. Rows are matched on their natural key and updated in place, so
re-importing an edited file does not create duplicates.

On update, only the fields actually present in the JSON are written — a file
that omits `featured` or `bullets` leaves the existing values alone rather than
resetting them to schema defaults.

Usage:
    python -m app.import_profile                  # reads profile.json
    python -m app.import_profile path/to/file.json
    python -m app.import_profile --dry-run
"""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select

from app.database import PROJECT_ROOT, SessionLocal, init_db
from app.models import Certification, Education, Experience, Project
from app.models import Profile as ContactRow
from app.models import Skill
from app.schemas import ProfileDocument

# (profile field, model, key fields used to match an existing row)
SECTIONS = (
    ("experiences", Experience, ("company", "title", "start_date")),
    ("education", Education, ("institution", "degree", "start_date")),
    ("certifications", Certification, ("name", "issuer")),
    ("skills", Skill, ("name",)),
    ("projects", Project, ("name",)),
)


def _match(db, model, keys: tuple[str, ...], values: dict):
    """Find an existing row by natural key, or None."""
    # A GitHub-imported project is identified by its repo id, which survives a
    # rename; fall back to the name for manually written entries.
    if model is Project and values.get("github_repo_id"):
        found = db.scalar(
            select(Project).where(Project.github_repo_id == values["github_repo_id"])
        )
        if found:
            return found

    conditions = [getattr(model, key) == values[key] for key in keys if key in values]
    if len(conditions) != len(keys):
        return None
    return db.scalar(select(model).where(*conditions))


def import_profile(path: Path, *, dry_run: bool = False) -> dict[str, tuple[int, int]]:
    """Returns {section: (created, updated)}."""
    profile = ProfileDocument.model_validate(json.loads(path.read_text()))
    results: dict[str, tuple[int, int]] = {}

    with SessionLocal() as db:
        if profile.contact is not None:
            # Singleton: always row id=1, updated in place.
            existing = db.get(ContactRow, 1)
            if existing is None:
                db.add(ContactRow(id=1, **profile.contact.model_dump()))
                results["contact"] = (1, 0)
                if dry_run:
                    print(f"  [dry-run] create contact: name={profile.contact.name!r}")
            else:
                for key, value in profile.contact.model_dump(exclude_unset=True).items():
                    setattr(existing, key, value)
                results["contact"] = (0, 1)
                if dry_run:
                    print(f"  [dry-run] update contact: name={profile.contact.name!r}")

        for section, model, keys in SECTIONS:
            created = updated = 0
            for entry in getattr(profile, section):
                full = entry.model_dump()
                provided = entry.model_dump(exclude_unset=True)

                existing = _match(db, model, keys, full)
                if existing is None:
                    db.add(model(**full))
                    created += 1
                    label = "create"
                else:
                    for key, value in provided.items():
                        setattr(existing, key, value)
                    updated += 1
                    label = "update"

                if dry_run:
                    identity = ", ".join(f"{k}={full[k]!r}" for k in keys)
                    print(f"  [dry-run] {label:<6} {section}: {identity}")

            results[section] = (created, updated)

        if dry_run:
            db.rollback()
        else:
            db.commit()

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a profile JSON file.")
    parser.add_argument(
        "path",
        nargs="?",
        default=PROJECT_ROOT / "profile.json",
        type=Path,
        help="Profile JSON file (defaults to profile.json).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate and report, write nothing.")
    args = parser.parse_args()

    if not args.path.exists():
        print(f"error: no such file: {args.path}", file=sys.stderr)
        raise SystemExit(1)

    init_db()

    try:
        results = import_profile(args.path, dry_run=args.dry_run)
    except json.JSONDecodeError as exc:
        print(f"error: {args.path} is not valid JSON: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except ValidationError as exc:
        print(f"error: {args.path} does not match the profile schema:\n{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    prefix = "[dry-run] " if args.dry_run else ""
    for section, (created, updated) in results.items():
        print(f"  {prefix}{section:<15} {created} created, {updated} updated")


if __name__ == "__main__":
    main()
