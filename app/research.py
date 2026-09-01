"""Research a company and job description with Claude + web search.

Calls the Anthropic API with the web search server tool, asks it to research the
company and read past the JD's stated requirements to what the role actually
involves, and saves the result as a JobApplication + ResearchBrief row.

Usage:
    python -m app.research --company "Acme Corp" --jd path/to/jd.txt
    python -m app.research --company "Acme Corp" --jd jd.txt --role "Data Engineer"
    python -m app.research --company "Acme Corp" --jd jd.txt --dry-run

Requires ANTHROPIC_API_KEY (read from .env or the environment).
"""

import argparse
import json
import os
import sys
from pathlib import Path

import anthropic
from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.models import JobApplication, ResearchBrief

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 16000
MAX_CONTINUATIONS = 5

# A brief whose sections are all shorter than this is a stub, not research.
MIN_FIELD_CHARS = 50
BRIEF_FIELDS = ("company_values", "tech_stack_signals", "role_reality")

# Per 1M tokens, for MODEL. Web search requests are billed separately per search.
INPUT_COST_PER_MTOK = 3.00
OUTPUT_COST_PER_MTOK = 15.00

SYSTEM_PROMPT = """\
You are a research assistant helping a candidate prepare for a job application.

Research the company using web search, then analyse the job description. Aim for
what is actually true and useful, not marketing copy:

- company_values: the company's mission and stated values, plus what recent news
  and engineering writing suggest they actually prioritise. Note where the
  public story and the evidence diverge.
- tech_stack_signals: the technologies the company uses, drawn from the JD, job
  postings, engineering blogs, open source, and conference talks. Say which
  signals are firm and which are inferred.
- role_reality: plain language, what this person will actually do day to day.
  Read past the requirements list to the underlying job: what problems land on
  this desk, who they work with, what the first six months look like, and which
  listed "requirements" are boilerplate rather than real. Be concrete and honest,
  including about unglamorous parts.

Base claims on sources you found. Where you are inferring rather than citing,
say so. If searches return little about the company, say that plainly instead of
padding — a short honest brief is more useful than a confident invented one.\
"""

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "role_title": {
            "type": "string",
            "description": "The role title, taken from the job description.",
        },
        "company_values": {"type": "string"},
        "tech_stack_signals": {"type": "string"},
        "role_reality": {"type": "string"},
    },
    "required": ["role_title", "company_values", "tech_stack_signals", "role_reality"],
    "additionalProperties": False,
}


def build_prompt(company_name: str, jd_text: str, role_title: str | None) -> str:
    role_line = f"\nRole title: {role_title}" if role_title else ""
    return (
        f"Company: {company_name}{role_line}\n\n"
        f"Job description:\n<job_description>\n{jd_text}\n</job_description>\n\n"
        f"Research {company_name} and analyse this job description."
    )


def _sum_usage(target: dict, usage) -> None:
    """Accumulate usage across continuation requests."""
    for field in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                  "cache_creation_input_tokens"):
        target[field] = target.get(field, 0) + (getattr(usage, field, None) or 0)

    server_tool_use = getattr(usage, "server_tool_use", None)
    searches = getattr(server_tool_use, "web_search_requests", None) or 0
    target["web_search_requests"] = target.get("web_search_requests", 0) + searches


def run_research(
    company_name: str, jd_text: str, role_title: str | None = None
) -> tuple[dict, str, dict]:
    """Returns (findings, raw_research_json, usage totals)."""
    client = anthropic.Anthropic()

    messages = [{"role": "user", "content": build_prompt(company_name, jd_text, role_title)}]
    usage_totals: dict[str, int] = {}
    raw_blocks: list = []

    for _ in range(MAX_CONTINUATIONS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=messages,
            thinking={"type": "adaptive"},
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 8,
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": RESULT_SCHEMA}},
        )

        _sum_usage(usage_totals, response.usage)
        raw_blocks.extend(json.loads(response.to_json())["content"])

        # The server-side tool loop caps out at 10 iterations; resend the turn
        # unchanged and the server picks up where it stopped.
        if response.stop_reason == "pause_turn":
            messages = [
                messages[0],
                {"role": "assistant", "content": response.content},
            ]
            continue

        if response.stop_reason == "refusal":
            raise RuntimeError(f"Model declined to respond: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Response hit max_tokens before finishing; raise MAX_TOKENS.")

        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise RuntimeError("No text block in response.")

        return json.loads(text), json.dumps(raw_blocks, indent=2), usage_totals

    raise RuntimeError(f"Still paused after {MAX_CONTINUATIONS} continuations.")


def check_brief(findings: dict, usage: dict) -> list[str]:
    """Reasons the brief is too thin to save. Empty list means it is fine."""
    problems = []

    lengths = {field: len((findings.get(field) or "").strip()) for field in BRIEF_FIELDS}
    if any(length < MIN_FIELD_CHARS for length in lengths.values()):
        detail = ", ".join(f"{field}={length} chars" for field, length in lengths.items())
        problems.append(
            f"at least one section is under {MIN_FIELD_CHARS} characters ({detail}) — "
            "the model returned a stub instead of research"
        )

    if not usage.get("web_search_requests"):
        problems.append(
            "no web searches were performed — the brief is unsourced, "
            "written from the model's own priors"
        )

    return problems


def save(company_name: str, jd_text: str, findings: dict, raw_research: str) -> int:
    with SessionLocal() as db:
        application = JobApplication(
            company_name=company_name,
            role_title=findings["role_title"],
            jd_text=jd_text,
        )
        application.brief = ResearchBrief(
            company_values=findings["company_values"],
            tech_stack_signals=findings["tech_stack_signals"],
            role_reality=findings["role_reality"],
            raw_research=raw_research,
        )
        db.add(application)
        db.commit()
        return application.id


def print_cost(usage: dict) -> None:
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    input_cost = input_tokens / 1_000_000 * INPUT_COST_PER_MTOK
    output_cost = output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK

    print(f"\nUsage ({MODEL}):")
    print(f"  input tokens        {input_tokens:>8,}  ${input_cost:.4f}")
    print(f"  output tokens       {output_tokens:>8,}  ${output_cost:.4f}")
    if cache_read:
        print(f"  cached input        {cache_read:>8,}  (billed at ~0.1x)")
    print(f"  {'total':<19} {input_tokens + output_tokens:>8,}  ${input_cost + output_cost:.4f}")
    if usage.get("web_search_requests"):
        print(
            f"  web searches        {usage['web_search_requests']:>8,}  "
            "(billed separately, not included above)"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Research a company and job description.")
    parser.add_argument("--company", required=True, help="Company name.")
    parser.add_argument("--jd", required=True, type=Path, help="Path to a job description file.")
    parser.add_argument("--role", help="Role title (otherwise taken from the job description).")
    parser.add_argument("--dry-run", action="store_true", help="Print findings without saving.")
    args = parser.parse_args()

    if not args.jd.exists():
        print(f"error: no such file: {args.jd}", file=sys.stderr)
        raise SystemExit(1)

    jd_text = args.jd.read_text().strip()
    if not jd_text:
        print(f"error: {args.jd} is empty", file=sys.stderr)
        raise SystemExit(1)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("error: ANTHROPIC_API_KEY is not set (add it to .env)", file=sys.stderr)
        raise SystemExit(1)

    init_db()

    print(f"Researching {args.company}...")
    try:
        findings, raw_research, usage = run_research(args.company, jd_text, args.role)
    except anthropic.APIStatusError as exc:
        print(f"error: Anthropic API returned {exc.status_code}: {exc.message}", file=sys.stderr)
        raise SystemExit(1) from exc
    except anthropic.APIConnectionError as exc:
        print(f"error: could not reach the Anthropic API: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    for field in ("role_title", *BRIEF_FIELDS):
        print(f"\n## {field}\n{findings[field]}")

    problems = check_brief(findings, usage)
    if problems:
        # Show the cost anyway — the call was billed whether or not it was useful.
        print_cost(usage)
        print("\nerror: refusing to save this brief:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "Check that --company matches the job description, then try again.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if args.dry_run:
        print("\n[dry-run] not saved.")
    else:
        application_id = save(args.company, jd_text, findings, raw_research)
        print(f"\nSaved JobApplication id={application_id} with research brief.")

    print_cost(usage)


def latest_briefs(limit: int = 10):
    """Convenience helper for inspecting saved research."""
    with SessionLocal() as db:
        return db.scalars(
            select(JobApplication).order_by(JobApplication.created_at.desc()).limit(limit)
        ).all()


if __name__ == "__main__":
    main()
