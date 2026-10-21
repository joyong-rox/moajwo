"""Deployment smoke checks using a fresh temporary database and synthetic CSV.

Run after npm ci && npm run build in frontend:
    python deploy/render/verify.py
Requires the backend requirements plus httpx. Never uses existing app data.
"""

import os
import secrets
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class PreviewChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="moajwo-preview-check-")
        cls.password = secrets.token_urlsafe(32)
        os.environ.update({
            "MOAJWO_PREVIEW": "1",
            "MOAJWO_DATA_DIR": cls.tmp.name,
            "DATABASE_URL": f"sqlite:///{cls.tmp.name}/moajwo.db",
            "MOAJWO_STATIC_DIR": str(ROOT / "frontend/dist"),
            "MOAJWO_COOKIE_SECURE": "true",
            "MOAJWO_PREVIEW_OWNER_USERNAME": "preview-owner",
            "MOAJWO_PREVIEW_OWNER_PASSWORD": cls.password,
            "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
        })
        sys.path.insert(0, str(ROOT / "backend"))
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "backend", check=True)
        import bootstrap
        bootstrap.main()
        from preview_app import app
        from starlette.testclient import TestClient
        cls.app = app
        cls.client_type = TestClient

    @classmethod
    def tearDownClass(cls):
        from app.db import engine
        engine.dispose()
        cls.tmp.cleanup()

    def client(self):
        return self.client_type(self.app, base_url="https://review.test")

    def login(self, client):
        response = client.post("/api/auth/login", json={"username": "preview-owner", "password": self.password})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def test_health(self):
        with self.client() as client:
            response = client.get("/api/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "ok", "database": "ok"})

    def test_spa_deep_links(self):
        expected = (ROOT / "frontend/dist/index.html").read_bytes()
        with self.client() as client:
            for path in ("/", "/mapping/common", "/moajwoAdmin/users"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, expected)
                self.assertEqual(response.headers["cache-control"], "no-cache")

    def test_assets_and_missing_paths(self):
        asset = next((ROOT / "frontend/dist/assets").glob("*.js"))
        with self.client() as client:
            self.assertEqual(client.get(f"/assets/{asset.name}").content, asset.read_bytes())
            self.assertEqual(client.get("/assets/missing.js").status_code, 404)
            self.assertEqual(client.get("/missing.json").status_code, 404)
            self.assertEqual(client.get("/%2e%2e/requirements.txt").status_code, 404)

    def test_api_is_not_spa(self):
        with self.client() as client:
            response = client.get("/api/does-not-exist")
            self.assertEqual(response.status_code, 404)
            self.assertIn("application/json", response.headers["content-type"])
            self.assertEqual(client.get("/api/files").status_code, 401)

    def test_public_setup_is_closed(self):
        with self.client() as client:
            self.assertFalse(client.get("/api/auth/status").json()["setup_required"])
            response = client.post("/api/auth/setup", json={
                "username": "other-owner", "display_name": "Other", "password": secrets.token_urlsafe(32),
            })
            self.assertEqual(response.status_code, 409)

    def test_login_upload_and_logout(self):
        with self.client() as client:
            cookie = self.login(client).headers["set-cookie"].lower()
            for attribute in ("secure", "httponly", "samesite=strict"):
                self.assertIn(attribute, cookie)
            response = client.post("/api/files", files={
                "files": ("sample.csv", (ROOT / "deploy/render/sample.csv").read_bytes(), "text/csv"),
            })
            self.assertEqual(response.status_code, 200, response.text)
            uploaded = response.json()[0]
            self.assertIsNone(uploaded["error"])
            self.assertEqual(uploaded["data_rows"], 2)
            self.assertEqual(client.get("/api/files").status_code, 200)
            self.assertTrue(list((Path(self.tmp.name) / "uploads").glob("*.csv")))
            client.post("/api/auth/logout")
            self.assertEqual(client.get("/api/files").status_code, 401)

    def test_restart_preserves_credentials(self):
        import bootstrap
        with patch.dict(os.environ, {"MOAJWO_PREVIEW_OWNER_PASSWORD": secrets.token_urlsafe(32)}):
            bootstrap.main()
        with self.client() as client:
            self.login(client)

    def test_incomplete_configuration_stops_startup(self):
        import bootstrap
        for change in ({"MOAJWO_PREVIEW": "0"}, {"MOAJWO_PREVIEW_OWNER_PASSWORD": ""},
                       {"MOAJWO_PREVIEW_OWNER_USERNAME": "different-owner"}):
            with patch.dict(os.environ, change), self.assertRaises(RuntimeError):
                bootstrap.main()


if __name__ == "__main__":
    unittest.main(verbosity=2)
