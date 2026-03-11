"""Entrypoint script for production deployment.

Runs Alembic migrations and starts uvicorn.
All migrations are idempotent — they check if tables/columns/indexes
already exist before creating, so they work safely on both fresh and
pre-existing databases.
"""

import os
import subprocess
import sys


if __name__ == "__main__":
    print("[entrypoint] Running alembic upgrade head")
    subprocess.run(["alembic", "upgrade", "head"], check=True)

    print("[entrypoint] Starting uvicorn")
    os.execvp(
        "uvicorn",
        ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3000"],
    )
