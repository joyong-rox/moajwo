"""Serve the unchanged API and Vite build under one HTTPS origin."""

import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from app.main import app as api


class SPAFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            # Only extensionless client routes fall back to index.html.
            # Missing assets and path traversal retain their original error.
            parts = Path(path).parts
            if exc.status_code != 404 or ".." in parts or "." in Path(path).name or path.startswith("assets/"):
                raise
            response = await super().get_response("index.html", scope)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if response.media_type == "text/html":
            response.headers["Cache-Control"] = "no-cache"
        return response


app = Starlette(routes=[
    Mount("/api", app=api),
    Mount("/", app=SPAFiles(directory=os.environ.get("MOAJWO_STATIC_DIR", "/app/static"), html=True)),
])
