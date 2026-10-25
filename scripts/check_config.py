"""Verify your configuration WITHOUT printing any secret value.

    python -m scripts.check_config

Reports, per variable, only PRESENT / MISSING (and for the JWT secret, whether it passes the safety rules).
Safe to paste the output into an issue or chat: it contains names and statuses, never values.
Exit code 1 if the API would refuse to start with the current settings.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

from app.core.config import Settings, serving_security_problems

# Variables that may hold credentials. Only their presence is ever reported.
SECRET_VARS = ["JWT_SECRET", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY",
               "OPENROUTER_API_KEY", "QDRANT_API_KEY", "NEO4J_PASSWORD", "POSTGRES_PASSWORD", "DATABASE_URL"]


def _present(name: str, file_vals: dict[str, str | None]) -> bool:
    v = os.environ.get(name, file_vals.get(name))
    return bool(v and v.strip())


def build_report(env_file: Path = Path(".env"), settings: Settings | None = None) -> tuple[list[str], int]:
    """Return (report lines, exit code). Pure of side effects except reading the env file; never emits a value."""
    file_vals = dotenv_values(env_file) if env_file.is_file() else {}
    s = settings or Settings(_env_file=str(env_file) if env_file.is_file() else None)
    lines = [f".env file: {'found' if env_file.is_file() else 'NOT found (copy .env.example to .env)'}",
             f"ENVIRONMENT: {s.environment}", f"SYSTEM: {s.system}", ""]
    for name in SECRET_VARS:
        lines.append(f"  {name:<20} {'PRESENT' if _present(name, file_vals) else 'missing'}")
    errors, warnings = serving_security_problems(s)
    lines.append("")
    lines += [f"WARNING: {w}" for w in warnings]
    lines += [f"ERROR:   {e}" for e in errors]
    lines.append("RESULT: API would refuse to start." if errors else "RESULT: configuration passes the startup safety checks.")
    return lines, 1 if errors else 0


def main() -> None:
    lines, code = build_report()
    print("\n".join(lines))
    sys.exit(code)


if __name__ == "__main__":
    main()
