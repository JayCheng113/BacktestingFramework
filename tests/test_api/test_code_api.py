"""Tests for the code editor API endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ez.api.app import app

client = TestClient(app)


class TestTemplateEndpoint:
    def test_strategy_template(self):
        resp = client.post("/api/code/template", json={"kind": "strategy", "class_name": "TestStrat"})
        assert resp.status_code == 200
        data = resp.json()
        assert "code" in data
        assert "TestStrat" in data["code"]

    def test_factor_template(self):
        resp = client.post("/api/code/template", json={"kind": "factor", "class_name": "TestFactor"})
        assert resp.status_code == 200
        data = resp.json()
        assert "TestFactor" in data["code"]

    def test_invalid_kind(self):
        resp = client.post("/api/code/template", json={"kind": "invalid"})
        assert resp.status_code == 422


class TestValidateEndpoint:
    def test_valid_code(self):
        resp = client.post("/api/code/validate", json={"code": "x = 1"})
        assert resp.status_code == 200
        assert resp.json()["valid"] is True

    def test_syntax_error(self):
        resp = client.post("/api/code/validate", json={"code": "def foo(:"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert len(data["errors"]) > 0

    def test_forbidden_import(self):
        resp = client.post("/api/code/validate", json={"code": "import os"})
        data = resp.json()
        assert data["valid"] is False


class TestFilesEndpoint:
    def test_list_files(self):
        resp = client.get("/api/code/files")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_read_nonexistent(self):
        resp = client.get("/api/code/files/nonexistent.py")
        assert resp.status_code == 404


class TestChatStatusEndpoint:
    def test_status(self):
        resp = client.get("/api/chat/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "available" in data


class TestHealthVersion:
    def test_version_updated(self):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["version"] == "0.2.11.1"


class TestPromote:
    """Tests for POST /api/code/promote endpoint."""

    def test_promote_nonexistent_file(self):
        resp = client.post("/api/code/promote", json={"filename": "research_nope.py"})
        assert resp.status_code == 404

    def test_promote_non_research_file(self):
        resp = client.post("/api/code/promote", json={"filename": "my_strategy.py"})
        assert resp.status_code == 400
        assert "research_" in resp.json()["detail"]

    def test_promote_invalid_extension(self):
        resp = client.post("/api/code/promote", json={"filename": "research_bad.txt"})
        assert resp.status_code == 400

    def test_promote_path_traversal(self):
        resp = client.post("/api/code/promote", json={"filename": "research_../../etc/passwd.py"})
        assert resp.status_code in (400, 404)  # Blocked by either validation or file-not-found


class TestPromoteRegex:
    """V2.8.1: Promote regex only matches Research + uppercase (class name pattern)."""

    def test_class_rename_pattern(self):
        import re
        code = 'class ResearchMomentumRsi(Strategy):'
        result = re.sub(r'class Research([A-Z]\w*)\(', r'class \1(', code)
        assert result == 'class MomentumRsi(Strategy):'

    def test_return_rename_pattern(self):
        import re
        code = 'return "ResearchMomentumRsi: a momentum strategy"'
        result = re.sub(r'return "Research([A-Z])', r'return "\1', code)
        assert result == 'return "MomentumRsi: a momentum strategy"'

    def test_no_rename_lowercase(self):
        """Research followed by lowercase should NOT be renamed."""
        import re
        code = 'return "Research is important"'
        result = re.sub(r'return "Research([A-Z])', r'return "\1', code)
        assert result == code  # unchanged

    def test_class_rename_requires_uppercase(self):
        import re
        code = 'class Researcher(object):'  # not a Research-prefixed strategy
        result = re.sub(r'class Research([A-Z]\w*)\(', r'class \1(', code)
        assert result == code  # unchanged — 'e' is lowercase
