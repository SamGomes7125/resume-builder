# Resume Builder

A local-first tool for keeping structured resume data (experience, education,
skills, projects) in SQLite and generating tailored resumes from it.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head     # create/update the database
```

Configuration is read from `.env` at the project root (loaded automatically by
`app/__init__.py`; real environment variables take precedence):

| Variable | Required | Purpose |
| --- | --- | --- |
| `GITHUB_USERNAME` | no | Default user for the GitHub importer |
| `GITHUB_TOKEN` | no | Raises the GitHub rate limit from 60 to 5000 req/hour |
| `DATABASE_URL` | no | Overrides the default `data/resume.db` |
| `ANTHROPIC_API_KEY` | for `app.research` / `app.generate` | Claude API access |

## Scripts

```bash
.venv/bin/python -m app.seed                    # migrate to head, show row counts
.venv/bin/python -m app.seed --reset            # drop everything and re-migrate (destroys data)
.venv/bin/python -m app.github_import <user>    # import public repos as projects
.venv/bin/python -m app.import_profile          # import profile.json (see profile.example.json)
.venv/bin/python -m app.mark_featured <name>... # feature these projects, unfeature the rest
.venv/bin/python -m app.research --company "Acme Corp" --jd jd.txt   # research a company + JD
.venv/bin/python -m app.generate --job-id 1                         # tailor bullets for that role
.venv/bin/python -m app.generate --list                             # list job applications
.venv/bin/python -m app.render --job-id 1 --output resume.docx      # render a one-page .docx
                                                                    # (contact details come from the
                                                                    #  "contact" object in profile.json)
.venv/bin/uvicorn app.main:app --reload         # run the API
```

## Migrations

Alembic owns the schema. Models live in `app/models.py`; never change table
structure by hand — write a migration so a fresh checkout ends up identical.

```bash
# 1. edit app/models.py, then generate a migration from the diff
.venv/bin/alembic revision --autogenerate -m "add some field"

# 2. read the generated file in alembic/versions/ before applying it
#    (autogenerate misses some changes, e.g. column renames become drop+add)

# 3. apply it
.venv/bin/alembic upgrade head
```

Other useful commands:

```bash
.venv/bin/alembic current              # which revision the database is on
.venv/bin/alembic history              # list revisions
.venv/bin/alembic check                # fail if models have drifted from the schema
.venv/bin/alembic downgrade -1         # undo the last migration
.venv/bin/alembic stamp head           # mark as up to date without running anything
```

`alembic/env.py` sets `render_as_batch=True`, so SQLite column alters and drops
work: Alembic rewrites the table rather than issuing an unsupported `ALTER`.

Scripts call `init_db()`, which runs `alembic upgrade head` itself, so the
database is migrated automatically before any script touches it.
