from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExperienceBase(BaseModel):
    company: str
    title: str
    start_date: date
    end_date: date | None = None
    location: str | None = None
    bullets: list[str] = Field(default_factory=list)
    type: Literal["work", "volunteer", "extracurricular"] = "work"


class ExperienceCreate(ExperienceBase):
    pass


class Experience(ExperienceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class EducationBase(BaseModel):
    institution: str
    degree: str
    field: str | None = None
    start_date: date
    end_date: date | None = None
    gpa: float | None = None


class EducationCreate(EducationBase):
    pass


class Education(EducationBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class ContactBase(BaseModel):
    """Maps to the models.Profile singleton table."""

    name: str
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None


class ContactCreate(ContactBase):
    pass


class Contact(ContactBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class CertificationBase(BaseModel):
    name: str
    issuer: str
    date_earned: date
    credential_url: str | None = None


class CertificationCreate(CertificationBase):
    pass


class Certification(CertificationBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class SkillBase(BaseModel):
    name: str
    category: str | None = None


class SkillCreate(SkillBase):
    pass


class Skill(SkillBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class ProjectBase(BaseModel):
    name: str
    description: str | None = None
    github_url: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)
    readme: str | None = None
    featured: bool = False
    source: Literal["manual", "github"] = "manual"
    github_repo_id: str | None = None


class ProjectCreate(ProjectBase):
    pass


class Project(ProjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class ProfileDocument(BaseModel):
    """Full import/export shape. Mirrors profile.example.json.

    Named ProfileDocument, not Profile, so it is never confused with
    models.Profile — the single-row contact table it embeds as `contact`.
    """

    contact: ContactCreate | None = None
    experiences: list[ExperienceCreate] = Field(default_factory=list)
    education: list[EducationCreate] = Field(default_factory=list)
    certifications: list[CertificationCreate] = Field(default_factory=list)
    skills: list[SkillCreate] = Field(default_factory=list)
    projects: list[ProjectCreate] = Field(default_factory=list)
