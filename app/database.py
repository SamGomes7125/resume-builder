import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'resume.db'}")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def _alembic_config():
    from alembic.config import Config

    config = Config(PROJECT_ROOT / "alembic.ini")
    # Resolve the script location against the project, not the caller's cwd.
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    return config


def init_db() -> None:
    """Bring the database up to the latest migration.

    Alembic owns the schema, not metadata.create_all() — otherwise a fresh
    checkout would build tables with no version stamp, and the next
    `alembic upgrade head` would fail on tables that already exist.
    """
    from alembic import command

    command.upgrade(_alembic_config(), "head")


def reset_db() -> None:
    """Drop every table, including Alembic's, then migrate back up."""
    from sqlalchemy import text

    from app import models  # noqa: F401  (registers tables on Base.metadata)

    Base.metadata.drop_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    init_db()


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
