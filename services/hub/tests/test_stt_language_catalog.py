import ast
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from orion.contracts.settings import STT_LANGUAGE_CATALOG
from orion.api.access_boundary import public_path


def require_user():
    raise HTTPException(401, "Not authenticated")


class SttLanguageCatalogTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "src/orion/api/routes.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "stt_language_catalog")
        self.app = FastAPI()
        namespace = {"router": self.app, "Depends": Depends, "get_current_user": require_user}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)

    def test_catalog_requires_the_user_dependency(self):
        with TestClient(self.app) as client:
            self.assertEqual(client.get("/api/v1/audio/languages").status_code, 401)

    def test_mobile_catalog_comes_from_shared_json(self):
        self.app.dependency_overrides[require_user] = lambda: "alice"
        with TestClient(self.app) as client:
            response = client.get("/api/v1/audio/languages")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data["languages"]), set(STT_LANGUAGE_CATALOG["languages"]))
        self.assertEqual(len(data["languages"]), 100)
        self.assertEqual(data["aliases"], STT_LANGUAGE_CATALOG["aliases"])
        self.assertNotIn("prompt", data["languages"]["tr"])
        self.assertTrue(public_path("/api/v1/audio/languages"))
