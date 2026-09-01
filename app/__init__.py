"""Resume builder.

Importing anything under `app.` loads the project's .env first, so scripts and
the FastAPI app all see the same configuration without wiring it up themselves.
Real environment variables take precedence over .env values.
"""

from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env", override=False)
