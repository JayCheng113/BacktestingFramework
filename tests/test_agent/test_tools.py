"""Tests for the tool registration framework."""
from __future__ import annotations

import json

import pytest

from ez.agent.tools import _TOOLS, execute_tool, get_all_tool_schemas


class TestToolRegistry:
    def test_tools_registered(self):
        schemas = get_all_tool_schemas()
        assert len(schemas) >= 14
        names = {s["function"]["name"] for s in schemas}
        # Original 9 tools (V2.7)
        assert "list_strategies" in names
        assert "list_factors" in names
        assert "read_source" in names
        assert "create_strategy" in names
        assert "update_strategy" in names
        assert "run_backtest" in names
        assert "run_experiment" in names
        assert "list_experiments" in names
        assert "explain_metrics" in names
        # V2.9+ portfolio tools
        assert "list_portfolio_strategies" in names
        assert "create_portfolio_strategy" in names
        assert "create_cross_factor" in names
        assert "run_portfolio_backtest" in names
        # V2.16.2 ML Alpha tool
        assert "create_ml_alpha" in names

    def test_schema_format(self):
        for schema in get_all_tool_schemas():
            assert schema["type"] == "function"
            fn = schema["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn
            assert fn["parameters"]["type"] == "object"

    def test_execute_unknown_tool(self):
        result = execute_tool("nonexistent_tool", {})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "Unknown tool" in parsed["error"]


class TestListStrategies:
    def test_returns_list(self):
        result = execute_tool("list_strategies", {})
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        # Should have at least the built-in strategies
        assert len(parsed) >= 3
        names = {s["name"] for s in parsed}
        assert "MACrossStrategy" in names

    def test_has_parameters(self):
        result = execute_tool("list_strategies", {})
        parsed = json.loads(result)
        for s in parsed:
            assert "parameters" in s
            assert "name" in s


class TestListFactors:
    def test_returns_list(self):
        result = execute_tool("list_factors", {})
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) >= 9
        # Tool returns uppercase names
        assert "MA" in parsed or "ma" in parsed


class TestReadSource:
    def test_read_builtin(self):
        result = execute_tool("read_source", {"path": "ez/strategy/builtin/ma_cross.py"})
        assert "MACrossStrategy" in result
        assert "class" in result

    def test_access_denied(self):
        result = execute_tool("read_source", {"path": "ez/config.py"})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "Access denied" in parsed["error"]

    def test_file_not_found(self):
        result = execute_tool("read_source", {"path": "strategies/nonexistent.py"})
        parsed = json.loads(result)
        assert "error" in parsed

    def test_path_traversal_blocked(self):
        result = execute_tool("read_source", {"path": "strategies/../ez/config.py"})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "denied" in parsed["error"].lower()

    def test_dotenv_traversal_blocked(self):
        result = execute_tool("read_source", {"path": "strategies/../../.env"})
        parsed = json.loads(result)
        assert "error" in parsed
