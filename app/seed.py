"""Database setup and inspection.

Applies migrations and reports what is in the database. It inserts no fixture
content — real data comes from `app.github_import` and manual entry.

Usage:
    python -m app.seed          # migrate to head, then show row counts
    python -m app.seed --reset  # drop everything and re-migrate (destroys data)
"""

import argparse

from sqlalchemy import func, select

from app.database import SessionLocal, init_db, reset_db
from app.models import Certification, Education, Experience, Project, Skill

TABLES = (
    ("experiences", Experience),
    ("education", Education),
    ("certifications", Certification),
    ("skills", Skill),
    ("projects", Project),
)


def report() -> None:
    with SessionLocal() as session:
        for label, model in TABLES:
            total = session.scalar(select(func.count()).select_from(model))
            print(f"  {label:<12} {total} row(s)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply migrations and show row counts.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop all tables and re-apply migrations. Destroys existing data.",
    )
    parser.add_argument("--yes", action="store_true", help="Skip the --reset confirmation prompt.")
    args = parser.parse_args()

    if args.reset:
        # The database now holds real imported data, so make --reset deliberate.
        if not args.yes:
            confirm = input("This deletes all data in the database. Type 'reset' to continue: ")
            if confirm.strip() != "reset":
                raise SystemExit("Aborted.")
        reset_db()
        print("Dropped all tables and re-applied migrations.")
    else:
        init_db()
        print("Database is at the latest migration.")

    report()


if __name__ == "__main__":
    main()
