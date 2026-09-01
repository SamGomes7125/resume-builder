"""Generate tailored resume content for a saved job application.

Pulls the candidate's profile and the research brief for a JobApplication, asks
Claude to pick the most relevant entries and reframe their bullets for this
specific role, and saves the result as a GeneratedResume row.

Usage:
    python -m app.generate --job-id 1
    python -m app.generate --job-id 1 --dry-run
    python -m app.generate --list

Requires ANTHROPIC_API_KEY (read from .env or the environment).
"""

import argparse
import json
import os
import sys

import anthropic
from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.models import (
    Certification,
    Education,
    Experience,
    GeneratedResume,
    JobApplication,
    Project,
)
from app.research import MODEL, print_cost

MAX_TOKENS = 16000
MIN_SELECTED = 3
MAX_SELECTED = 5
# READMEs are the only substantial material behind imported projects, whose
# bullets are usually empty. Truncated to keep the prompt affordable.
README_CHARS = 1500

SYSTEM_PROMPT = f"""\
You are helping a candidate tailor their resume for one specific role.

You are given the candidate's profile and a research brief about the role. Do two
things:

1. SELECT the {MIN_SELECTED}-{MAX_SELECTED} entries (experiences and projects
   combined, not each) that best evidence what this role actually needs. Judge
   against the brief's role_reality and tech_stack_signals, not against the job
   description's keyword list. An entry that demonstrates the underlying work
   beats one that merely shares vocabulary. Say briefly why each was chosen.

2. REWRITE each selected entry's bullets to lead with the angle this role cares
   about. This is reframing, not keyword stuffing: the same work, described from
   the perspective that matters here. Emphasise the parts that map to the role's
   day-to-day reality and let irrelevant detail fall away.

Hard constraints:
- Invent nothing. Every claim must be supported by the profile material you were
  given. No invented metrics, scale, dates, team sizes, or technologies. If the
  source says nothing about impact, write a bullet about what was built, not a
  fabricated result.
- Do not restate a technology the entry does not list.
- Prefer concrete specifics already present over vague achievement language.
- 2-4 bullets per entry, one line each, starting with a strong verb.

If the profile is thin, select fewer entries and say so in `notes` rather than
padding with weak ones.\
"""

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "selected_experiences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "Experience.id from the profile."},
                    "company": {"type": "string"},
                    "title": {"type": "string"},
                    "relevance_reason": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "company", "title", "relevance_reason", "bullets"],
                "additionalProperties": False,
            },
        },
        "selected_projects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "Project.id from the profile."},
                    "name": {"type": "string"},
                    "relevance_reason": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "name", "relevance_reason", "bullets"],
                "additionalProperties": False,
            },
        },
        "notes": {
            "type": "string",
            "description": "Anything the candidate should know: gaps, weak evidence, caveats.",
        },
    },
    "required": ["selected_experiences", "selected_projects", "notes"],
    "additionalProperties": False,
}


def load_profile(db) -> dict:
    """The candidate's material: all experience/education/certs, featured projects."""
    experiences = db.scalars(select(Experience).order_by(Experience.start_date.desc())).all()
    education = db.scalars(select(Education).order_by(Education.start_date.desc())).all()
    certifications = db.scalars(
        select(Certification).order_by(Certification.date_earned.desc())
    ).all()
    projects = db.scalars(
        select(Project).where(Project.featured.is_(True)).order_by(Project.name)
    ).all()

    return {
        "experiences": [
            {
                "id": e.id,
                "company": e.company,
                "title": e.title,
                "type": e.type,
                "location": e.location,
                "start_date": e.start_date.isoformat() if e.start_date else None,
                "end_date": e.end_date.isoformat() if e.end_date else None,
                "bullets": e.bullets or [],
            }
            for e in experiences
        ],
        "education": [
            {
                "institution": ed.institution,
                "degree": ed.degree,
                "field": ed.field,
                "start_date": ed.start_date.isoformat() if ed.start_date else None,
                "end_date": ed.end_date.isoformat() if ed.end_date else None,
                "gpa": ed.gpa,
            }
            for ed in education
        ],
        "certifications": [
            {
                "name": c.name,
                "issuer": c.issuer,
                "date_earned": c.date_earned.isoformat() if c.date_earned else None,
            }
            for c in certifications
        ],
        "featured_projects": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "tech_stack": p.tech_stack or [],
                "bullets": p.bullets or [],
                "github_url": p.github_url,
                "readme_excerpt": (p.readme or "")[:README_CHARS] or None,
            }
            for p in projects
        ],
    }


def build_prompt(application: JobApplication, profile: dict) -> str:
    brief = application.brief
    return (
        f"Target role: {application.role_title} at {application.company_name}\n\n"
        "<research_brief>\n"
        f"<role_reality>\n{brief.role_reality}\n</role_reality>\n\n"
        f"<tech_stack_signals>\n{brief.tech_stack_signals}\n</tech_stack_signals>\n\n"
        f"<company_values>\n{brief.company_values}\n</company_values>\n"
        "</research_brief>\n\n"
        f"<job_description>\n{application.jd_text}\n</job_description>\n\n"
        f"<candidate_profile>\n{json.dumps(profile, indent=2)}\n</candidate_profile>\n\n"
        "Select and rewrite as instructed."
    )


def generate(application: JobApplication, profile: dict) -> tuple[dict, dict]:
    """Returns (result, usage)."""
    client = anthropic.Anthropic()

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_prompt(application, profile)}],
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": RESULT_SCHEMA}},
    )

    if response.stop_reason == "refusal":
        raise RuntimeError(f"Model declined to respond: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Response hit max_tokens before finishing; raise MAX_TOKENS.")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError("No text block in response.")

    usage = {
        "input_tokens": response.usage.input_tokens or 0,
        "output_tokens": response.usage.output_tokens or 0,
        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
    }
    return json.loads(text), usage


def check_result(result: dict, profile: dict) -> list[str]:
    """Reasons the generated resume is not worth saving."""
    problems = []

    experiences = result.get("selected_experiences") or []
    projects = result.get("selected_projects") or []
    total = len(experiences) + len(projects)

    if total == 0:
        problems.append("nothing was selected — no experiences and no projects")

    # Every returned id must correspond to a real profile row, or the bullets
    # are attached to something that does not exist.
    known_experience_ids = {e["id"] for e in profile["experiences"]}
    known_project_ids = {p["id"] for p in profile["featured_projects"]}
    for entry in experiences:
        if entry["id"] not in known_experience_ids:
            problems.append(f"experience id={entry['id']} is not in the profile")
    for entry in projects:
        if entry["id"] not in known_project_ids:
            problems.append(f"project id={entry['id']} is not in the profile")

    empty = [
        entry.get("name") or entry.get("company")
        for entry in (*experiences, *projects)
        if not entry.get("bullets")
    ]
    if empty:
        problems.append(f"no bullets generated for: {', '.join(map(str, empty))}")

    return problems


def save(job_application_id: int, result: dict) -> int:
    with SessionLocal() as db:
        resume = GeneratedResume(
            job_application_id=job_application_id,
            content_json=json.dumps(result, indent=2),
        )
        db.add(resume)
        db.commit()
        return resume.id


def render(result: dict) -> None:
    for entry in result.get("selected_experiences", []):
        print(f"\n## {entry['title']} — {entry['company']}  (experience id={entry['id']})")
        print(f"   why: {entry['relevance_reason']}")
        for bullet in entry["bullets"]:
            print(f"   • {bullet}")

    for entry in result.get("selected_projects", []):
        print(f"\n## {entry['name']}  (project id={entry['id']})")
        print(f"   why: {entry['relevance_reason']}")
        for bullet in entry["bullets"]:
            print(f"   • {bullet}")

    if result.get("notes"):
        print(f"\n## notes\n{result['notes']}")


def list_applications() -> None:
    with SessionLocal() as db:
        applications = db.scalars(select(JobApplication).order_by(JobApplication.id)).all()
        if not applications:
            print("No job applications yet — run app.research first.")
            return
        for application in applications:
            resumes = len(application.resumes)
            brief = "brief" if application.brief else "NO BRIEF"
            print(
                f"  {application.id}  {application.company_name} — {application.role_title}"
                f"  [{brief}, {resumes} generated]"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate tailored resume content.")
    parser.add_argument("--job-id", type=int, help="JobApplication id.")
    parser.add_argument("--dry-run", action="store_true", help="Print output without saving.")
    parser.add_argument("--list", action="store_true", help="List job applications.")
    args = parser.parse_args()

    init_db()

    if args.list:
        list_applications()
        return

    if args.job_id is None:
        parser.error("--job-id is required (or use --list)")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("error: ANTHROPIC_API_KEY is not set (add it to .env)", file=sys.stderr)
        raise SystemExit(1)

    with SessionLocal() as db:
        application = db.get(JobApplication, args.job_id)
        if application is None:
            print(f"error: no JobApplication with id={args.job_id}", file=sys.stderr)
            raise SystemExit(1)
        if application.brief is None:
            print(
                f"error: JobApplication id={args.job_id} has no research brief; "
                "run app.research for it first",
                file=sys.stderr,
            )
            raise SystemExit(1)

        profile = load_profile(db)
        if not profile["experiences"] and not profile["featured_projects"]:
            print(
                "error: profile has no experiences and no featured projects — "
                "nothing to build a resume from",
                file=sys.stderr,
            )
            raise SystemExit(1)

        print(
            f"Generating for {application.company_name} — {application.role_title}\n"
            f"  profile: {len(profile['experiences'])} experience, "
            f"{len(profile['featured_projects'])} featured project(s), "
            f"{len(profile['education'])} education, "
            f"{len(profile['certifications'])} certification(s)"
        )

        try:
            result, usage = generate(application, profile)
        except anthropic.APIStatusError as exc:
            print(f"error: Anthropic API returned {exc.status_code}: {exc.message}", file=sys.stderr)
            raise SystemExit(1) from exc
        except anthropic.APIConnectionError as exc:
            print(f"error: could not reach the Anthropic API: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

    render(result)

    problems = check_result(result, profile)
    if problems:
        print_cost(usage)
        print("\nerror: refusing to save this resume:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        raise SystemExit(1)

    selected = len(result["selected_experiences"]) + len(result["selected_projects"])
    if not MIN_SELECTED <= selected <= MAX_SELECTED:
        print(
            f"\nwarning: {selected} entries selected, expected "
            f"{MIN_SELECTED}-{MAX_SELECTED}",
            file=sys.stderr,
        )

    if args.dry_run:
        print("\n[dry-run] not saved.")
    else:
        resume_id = save(args.job_id, result)
        print(f"\nSaved GeneratedResume id={resume_id} for job application {args.job_id}.")

    print_cost(usage)


if __name__ == "__main__":
    main()
