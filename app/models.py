from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Experience(Base):
    __tablename__ = "experiences"

    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    bullets: Mapped[list[str]] = mapped_column(JSON, default=list)
    type: Mapped[str] = mapped_column(String(20), default="work", server_default="work")


class Education(Base):
    __tablename__ = "education"

    id: Mapped[int] = mapped_column(primary_key=True)
    institution: Mapped[str] = mapped_column(String(200))
    degree: Mapped[str] = mapped_column(String(200))
    field: Mapped[str | None] = mapped_column(String(200), nullable=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    gpa: Mapped[float | None] = mapped_column(Float, nullable=True)


class Certification(Base):
    __tablename__ = "certifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    issuer: Mapped[str] = mapped_column(String(200))
    date_earned: Mapped[date] = mapped_column(Date)
    credential_url: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tech_stack: Mapped[list[str]] = mapped_column(JSON, default=list)
    bullets: Mapped[list[str]] = mapped_column(JSON, default=list)
    readme: Mapped[str | None] = mapped_column(Text, nullable=True)
    featured: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    source: Mapped[str] = mapped_column(String(20), default="manual", server_default="manual")
    github_repo_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, unique=True
    )


class JobApplication(Base):
    __tablename__ = "job_applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str] = mapped_column(String(200))
    role_title: Mapped[str] = mapped_column(String(200))
    jd_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    brief: Mapped["ResearchBrief | None"] = relationship(
        back_populates="job_application",
        cascade="all, delete-orphan",
        uselist=False,
    )
    resumes: Mapped[list["GeneratedResume"]] = relationship(
        back_populates="job_application",
        cascade="all, delete-orphan",
        order_by="GeneratedResume.created_at",
    )


class ResearchBrief(Base):
    __tablename__ = "research_briefs"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Unique FK: one brief per application.
    job_application_id: Mapped[int] = mapped_column(
        ForeignKey("job_applications.id", ondelete="CASCADE"), unique=True
    )
    company_values: Mapped[str | None] = mapped_column(Text, nullable=True)
    tech_stack_signals: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Plain-language summary of what the role actually involves day to day.
    role_reality: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Full raw findings (search queries, results, citations) kept for reference.
    raw_research: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    job_application: Mapped["JobApplication"] = relationship(back_populates="brief")


class GeneratedResume(Base):
    __tablename__ = "generated_resumes"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Not unique: an application can be regenerated, keeping earlier attempts.
    job_application_id: Mapped[int] = mapped_column(
        ForeignKey("job_applications.id", ondelete="CASCADE")
    )
    content_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    job_application: Mapped["JobApplication"] = relationship(back_populates="resumes")


class Profile(Base):
    """The candidate's contact details. Exactly one row, always id=1."""

    __tablename__ = "profile"
    __table_args__ = (CheckConstraint("id = 1", name="profile_is_singleton"),)

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    github_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    portfolio_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
