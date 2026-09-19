"""Stateless multi-user web version of the resume builder.

Every request is self-contained: a visitor's GitHub username, their own GitHub
token (optional) and Anthropic API key, their profile (experience, education,
certifications, contact info), and a job description. Nothing is written to
disk or a database — the whole pipeline (GitHub import -> research -> generate
-> render) runs in memory for the duration of one request, and the result is
streamed back as a .docx download. Temp files are deleted immediately after.

Run locally:
    uvicorn web.api:app --reload --port 8000

Deploy (Render): root directory `web`, build `pip install -r requirements.txt`,
start `uvicorn api:app --host 0.0.0.0 --port $PORT`.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import anthropic
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.generate import README_CHARS, check_result, generate
from app.github_import import GitHubError, _session, fetch_languages, fetch_readme, fetch_repos
from app.render import USABLE_HEIGHT_PT, ResumeDoc, build as build_resume_doc
from app.research import check_brief, cost_summary, run_research

# Cap on how many of a visitor's repos we pull into the prompt. Keeps the
# Claude request affordable regardless of how large someone's GitHub is.
MAX_REPOS = 25

app = FastAPI(title="Resume Builder — web")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class ContactIn(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    links: list[str] = Field(default_factory=list)


class ExperienceIn(BaseModel):
    company: str
    title: str
    type: str = "work"
    location: str | None = None
    start_date: str  # "YYYY-MM-DD"
    end_date: str | None = None
    bullets: list[str] = Field(default_factory=list)


class EducationIn(BaseModel):
    institution: str
    degree: str
    field: str | None = None
    start_date: str
    end_date: str | None = None
    gpa: float | None = None


class CertificationIn(BaseModel):
    name: str
    issuer: str
    date_earned: str


class GenerateRequest(BaseModel):
    github_username: str
    github_token: str | None = None
    anthropic_api_key: str
    company_name: str
    jd_text: str
    role_title: str | None = None
    contact: ContactIn
    experiences: list[ExperienceIn] = Field(default_factory=list)
    education: list[EducationIn] = Field(default_factory=list)
    certifications: list[CertificationIn] = Field(default_factory=list)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"Invalid date (expected YYYY-MM-DD): {value!r}")


@dataclass
class _Brief:
    role_reality: str
    tech_stack_signals: str
    company_values: str


@dataclass
class _Application:
    company_name: str
    role_title: str
    jd_text: str
    brief: _Brief


# ---------------------------------------------------------------------------
# Pipeline steps, adapted to run in memory for one request
# ---------------------------------------------------------------------------

def _fetch_github_projects(username: str, token: str | None) -> list[dict]:
    session = _session(token)
    try:
        repos = fetch_repos(session, username)
    except GitHubError as exc:
        raise HTTPException(400, str(exc))
    except requests.RequestException as exc:
        raise HTTPException(502, f"Could not reach GitHub: {exc}")

    selected = [r for r in repos if not r.get("fork") and not r.get("archived")]
    selected = selected[:MAX_REPOS]

    projects = []
    for i, repo in enumerate(selected, start=1):
        languages = fetch_languages(session, repo)
        readme = fetch_readme(session, repo)
        projects.append(
            {
                "id": i,
                "name": repo["name"],
                "description": repo.get("description"),
                "tech_stack": languages,
                "bullets": [],
                "github_url": repo["html_url"],
                "readme_excerpt": (readme or "")[:README_CHARS] or None,
            }
        )
    return projects


def _build_profile(payload: GenerateRequest, projects: list[dict]) -> dict:
    experiences = []
    for i, e in enumerate(payload.experiences, start=1):
        experiences.append(
            {
                "id": i,
                "company": e.company,
                "title": e.title,
                "type": e.type,
                "location": e.location,
                "start_date": e.start_date,
                "end_date": e.end_date,
                "bullets": e.bullets,
            }
        )
    return {
        "experiences": experiences,
        "education": [
            {
                "institution": ed.institution,
                "degree": ed.degree,
                "field": ed.field,
                "start_date": ed.start_date,
                "end_date": ed.end_date,
                "gpa": ed.gpa,
            }
            for ed in payload.education
        ],
        "certifications": [
            {"name": c.name, "issuer": c.issuer, "date_earned": c.date_earned}
            for c in payload.certifications
        ],
        "featured_projects": projects,
    }


def _render_docx(result: dict, payload: GenerateRequest, profile: dict) -> Path:
    exp_lookup = {
        e["id"]: SimpleNamespace(
            company=e["company"],
            title=e["title"],
            start_date=_parse_date(e["start_date"]),
            end_date=_parse_date(e["end_date"]),
            location=e["location"],
        )
        for e in profile["experiences"]
    }
    proj_lookup = {
        p["id"]: SimpleNamespace(
            name=p["name"],
            tech_stack=p["tech_stack"],
            github_url=p["github_url"],
        )
        for p in profile["featured_projects"]
    }

    data = {
        "experiences": [
            (entry, exp_lookup.get(entry["id"])) for entry in result.get("selected_experiences", [])
        ],
        "projects": [
            (entry, proj_lookup.get(entry["id"])) for entry in result.get("selected_projects", [])
        ],
        "education": [
            SimpleNamespace(
                degree=ed.degree,
                field=ed.field,
                institution=ed.institution,
                start_date=_parse_date(ed.start_date),
                end_date=_parse_date(ed.end_date),
                gpa=ed.gpa,
            )
            for ed in payload.education
        ],
        "certifications": [
            SimpleNamespace(
                name=c.name, issuer=c.issuer, date_earned=_parse_date(c.date_earned)
            )
            for c in payload.certifications
        ],
    }

    contact = {
        "name": payload.contact.name,
        "email": payload.contact.email,
        "phone": payload.contact.phone,
        "location": payload.contact.location,
        "links": payload.contact.links,
    }

    doc: ResumeDoc = build_resume_doc(data, contact)

    fd, path_str = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    out_path = Path(path_str)
    doc.save(out_path)

    fill = doc.height_pt / USABLE_HEIGHT_PT
    return out_path, fill


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text())


@app.post("/api/generate")
def api_generate(payload: GenerateRequest):
    # Validate dates early, before spending any API cost.
    for e in payload.experiences:
        _parse_date(e.start_date)
        _parse_date(e.end_date)
    for ed in payload.education:
        _parse_date(ed.start_date)
        _parse_date(ed.end_date)
    for c in payload.certifications:
        _parse_date(c.date_earned)

    # Step 1: GitHub projects (free — GitHub's own rate limit, not ours).
    projects = _fetch_github_projects(payload.github_username, payload.github_token)
    profile = _build_profile(payload, projects)

    if not profile["experiences"] and not profile["featured_projects"]:
        raise HTTPException(
            400, "No experience entries and no GitHub projects found — nothing to build from."
        )

    # Step 2: research (costs the visitor's own Anthropic key).
    try:
        findings, _raw, research_usage = run_research(
            payload.company_name,
            payload.jd_text,
            payload.role_title,
            api_key=payload.anthropic_api_key,
        )
    except anthropic.APIStatusError as exc:
        if exc.status_code == 401:
            raise HTTPException(401, "That Anthropic API key was rejected. Check it and try again.")
        raise HTTPException(502, f"Anthropic API error: {exc.message}")
    except anthropic.APIConnectionError as exc:
        raise HTTPException(502, f"Could not reach the Anthropic API: {exc}")

    brief_problems = check_brief(findings, research_usage)
    if brief_problems:
        raise HTTPException(
            422,
            "Research came back too thin to use: " + "; ".join(brief_problems)
            + ". Check the company name matches the job description and try again.",
        )

    application = _Application(
        company_name=payload.company_name,
        role_title=findings["role_title"],
        jd_text=payload.jd_text,
        brief=_Brief(
            role_reality=findings["role_reality"],
            tech_stack_signals=findings["tech_stack_signals"],
            company_values=findings["company_values"],
        ),
    )

    # Step 3: generate tailored content.
    try:
        result, gen_usage = generate(application, profile, api_key=payload.anthropic_api_key)
    except anthropic.APIStatusError as exc:
        if exc.status_code == 401:
            raise HTTPException(401, "That Anthropic API key was rejected. Check it and try again.")
        raise HTTPException(502, f"Anthropic API error: {exc.message}")
    except anthropic.APIConnectionError as exc:
        raise HTTPException(502, f"Could not reach the Anthropic API: {exc}")

    result_problems = check_result(result, profile)
    if result_problems:
        raise HTTPException(422, "Could not build a resume: " + "; ".join(result_problems))

    # Step 4: render to .docx.
    out_path, fill = _render_docx(result, payload, profile)

    research_cost = cost_summary(research_usage)
    gen_cost = {
        "input_tokens": gen_usage.get("input_tokens", 0),
        "output_tokens": gen_usage.get("output_tokens", 0),
        "cost_usd": round(
            gen_usage.get("input_tokens", 0) / 1_000_000 * 3.00
            + gen_usage.get("output_tokens", 0) / 1_000_000 * 15.00,
            4,
        ),
    }
    total_cost = round(research_cost["cost_usd"] + gen_cost["cost_usd"], 4)

    def cleanup() -> None:
        out_path.unlink(missing_ok=True)

    return FileResponse(
        out_path,
        filename=f"resume_{payload.company_name.replace(' ', '_')}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        background=BackgroundTask(cleanup),
        headers={
            "X-Total-Cost-Usd": str(total_cost),
            "X-Page-Fill-Percent": str(round(fill * 100)),
            "X-Notes": (result.get("notes") or "")[:800].replace("\n", " "),
            "Access-Control-Expose-Headers": "X-Total-Cost-Usd, X-Page-Fill-Percent, X-Notes",
        },
    )


# Serve any other static assets placed next to index.html (e.g. a favicon).
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")