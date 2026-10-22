"""Production entrypoint: apply migrations, then serve on $PORT (Render/Heroku style). `python -m app.start`"""
from __future__ import annotations

import os
import subprocess
import sys


def main() -> None:
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
