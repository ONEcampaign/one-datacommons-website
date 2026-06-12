"""Local launcher for the resolvekit demo — mixer-independent.

Serves the resolve_demo static page + the real resolve_api router on its own
bare FastAPI app, skipping the dc_search search lifespan (which constructs a
DataCommonsClient that makes a live mixer call). The resolve endpoints are
decoupled from the search pipeline, so this is a faithful local test.

Run from the dc_search_server dir on the 3.12 venv:

    .venv/bin/python run_resolve_demo.py

Then open http://127.0.0.1:7800/api/resolve-demo/
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).parent / "src"
sys.path.insert(0, str(SRC))

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from dc_search import resolve_api  # noqa: E402

app = FastAPI(title="resolvekit demo (local)")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

if resolve_api.router is not None:
    app.include_router(resolve_api.router)
else:
    print("WARNING: resolve_api.router is None — resolvekit import failed")

app.mount(
    "/api/resolve-demo",
    StaticFiles(directory=str(SRC / "dc_search" / "resolve_demo"), html=True),
    name="resolve_demo",
)

if __name__ == "__main__":
    import uvicorn

    print("\n  →  http://127.0.0.1:7800/api/resolve-demo/\n")
    uvicorn.run(app, host="127.0.0.1", port=7800, log_level="warning")
