# AI-Assisted Resume Builder

A personal pipeline that turns your GitHub projects and career history into a **tailored, research-backed resume** for a specific job — automatically.

Most resume advice says "tailor every application." Almost nobody actually does it, because it's slow: researching a company properly, figuring out what a role *really* involves beyond the buzzwords, and reframing your experience to match takes real time per application. This project automates that pipeline while keeping a human in the loop for the judgment calls that matter.

## How it works

```
GitHub repos ─┐
               ├─► Profile data (SQLite) ─► Job/company research ─► Tailored generation ─► Rendered resume (.docx)
Manual entry ──┘         ▲                         ▲                        ▲
                    experience, education,     JD text + company      selects & reframes
                    certifications              name → web search      relevant experience
                                                 → structured brief     into resume bullets
```

1. **Import your GitHub projects** — pulls your public repos, languages, and README content automatically
2. **Add your profile** — experience, education, and certifications, stored locally
3. **Research the role** — given a job description and company name, an LLM with web search builds a structured brief: company values vs. operational reality, confirmed tech stack, and what the role *actually* involves day to day (not just the listed requirements)
4. **Generate tailored content** — the model selects your most relevant experience/projects for this specific role and reframes the bullets accordingly, with a transparent "why" for every selection and honest notes on any gaps in your profile
5. **Render** — outputs a clean, single-page, ATS-friendly `.docx`

Every step's reasoning is visible — nothing is a black box. The generation step is also deliberately calibrated to flag weaknesses rather than oversell you; a resume tool that only tells you what you want to hear isn't actually useful.

## Setup

Requires Python 3.11+.

```bash
git clone https://github.com/SamGomes7125/resume-builder.git
cd resume-builder
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
GITHUB_USERNAME=your-github-username
GITHUB_TOKEN=your-github-personal-access-token   # optional, raises API rate limit from 60/hr to 5000/hr
ANTHROPIC_API_KEY=your-anthropic-api-key
```

- GitHub token: [Settings → Developer settings → Personal access tokens](https://github.com/settings/tokens) (no scopes needed for public repos)
- Anthropic API key: [console.anthropic.com](https://console.anthropic.com)

Initialize the database:

```bash
.venv/bin/python -m app.seed
```

## Usage

**1. Import your GitHub projects**
```bash
.venv/bin/python -m app.github_import
```

**2. Add your profile**

Copy `profile.example.json` to `profile.json` and fill in your real experience, education, certifications, and contact details. Then:
```bash
.venv/bin/python -m app.import_profile
```

**3. (Optional) Curate which projects get featured**
```bash
.venv/bin/python -m app.mark_featured "project-name-1" "project-name-2" ...
```

**4. Research a job**

Save the job description to a text file, then:
```bash
.venv/bin/python -m app.research --company "Acme Corp" --jd path/to/jd.txt
```

**5. Generate tailored content**
```bash
.venv/bin/python -m app.generate --job-id 1
```

**6. Render the resume**
```bash
.venv/bin/python -m app.render --job-id 1 --output resume.docx
```

## Cost

Each research + generate cycle costs roughly **$0.15–0.30 USD** in Anthropic API usage (Claude Sonnet, with web search for the research step). Everything else — GitHub import, rendering — is free.

## Project structure

```
app/
  database.py       SQLAlchemy engine/session setup
  models.py         Experience, Education, Certification, Skill, Project, Profile, JobApplication, ResearchBrief, GeneratedResume
  schemas.py         Pydantic validation schemas
  github_import.py  Pulls public repos into the Project table
  import_profile.py  Loads profile.json into the database
  mark_featured.py   Curates which projects surface in generation
  research.py        Company/JD research via Claude + web search
  generate.py         Selects and reframes experience into tailored resume content
  render.py           Renders a GeneratedResume into a .docx file
alembic/              Database migrations
profile.example.json  Template for your own profile.json (gitignored)
```

## A note on how this was built

This was built iteratively, phase by phase, using [Claude Code](https://claude.com/claude-code) as an AI-assisted development workflow — each phase (data model, GitHub ingestion, research, generation, rendering) was scoped, built, and verified before moving to the next, with schema migrations (Alembic) added early to avoid data loss as the model evolved.

## Forking this for yourself

This repo contains no personal data — `profile.json`, `.env`, and the local database are all gitignored. Clone it, add your own `.env` and `profile.json`, and it's yours. Contributions and forks welcome.
