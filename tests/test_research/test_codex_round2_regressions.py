"""Codex round-3 + round-4 regression tests for V2.19.0 + V2.20.0.

Each test maps to a finding from the codex external review (round-3
fixes are tagged with codex_review_1, round-4 with codex_review_2):

Round-3 (codex external review of V2.19.0 r3 + V2.20.0 P1-A MVP):
  - P1-1: _run_guards deadlock surface (forbidden imports, RLock→Lock)
  - P1-2: ResearchPipeline.run robust to bad step return
  - P2-1: None-return / explicit StepError still records history
  - P2-2: StepError carries the partial PipelineContext
  - P2-3: DataLoadStep market/period sentinel uses None, not "cn_stock"
  - P2-4: DataLoadStep symbols rejects single string
  - P2-5: stale skipped artifacts are cleared on rerun
  - P2-6: ResearchPipeline.run(reset=True) clears prior context state
  - P3-1: ReportStep escapes pipe and newline in markdown table cells

Round-4 (Claude reviewer audit of round-3 fixes):
  - P1-A: attribute chain attack (ez.agent.sandbox._reload_lock via
          legal `import ez`) — adds AST chain reconstruction + reverts
          RLock to Lock so attacks fail loud, not silent
  - P2-A: DataLoadStep symbols also rejects bytes/bytearray
  - P2-B: DataLoadStep start_date/end_date use is-not-None (consistent)
  - P3-A: ReportStep Warnings section escapes reason newlines
  - P3-B: failed StepRecord.written_keys captures partial mutation

If any of these fails in the future, codex's findings have regressed.
"""
from __future__ import annotations
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ez.research.context import PipelineContext, StepRecord
from ez.research.pipeline import ResearchPipeline, ResearchStep, StepError
from ez.research.steps.data_load import DataLoadStep
from ez.research.steps.report import ReportStep, default_template, _md_escape


# ============================================================
# P1-1: sandbox deadlock surface — forbidden import + RLock
# ============================================================

class TestP11ForbiddenSandboxImports:
    """Codex round-2 P1-1: user code must not be able to import
    ez.agent.sandbox or its internal functions.

    V2.21: _reload_lock moved to closure, but sandbox module is still
    forbidden to prevent access to _get_reload_lock and other internals."""

    def test_direct_import_blocked(self):
        from ez.agent.sandbox import check_syntax
        for code in [
            "import ez.agent.sandbox",
            "from ez.agent.sandbox import _reload_lock",
            "from ez.agent import sandbox",
            "from ez.agent.sandbox import check_syntax",
        ]:
            errs = check_syntax(code)
            assert errs, f"P1-1 regression: {code!r} should be blocked"
            assert any("Forbidden import" in e for e in errs)

    def test_guards_internals_blocked(self):
        from ez.agent.sandbox import check_syntax
        for code in [
            "from ez.testing.guards import suite",
            "from ez.testing import guards",
            "import ez.testing.guards.suite",
        ]:
            errs = check_syntax(code)
            assert errs, f"P1-1 regression: {code!r} should be blocked"

    def test_routes_code_blocked(self):
        from ez.agent.sandbox import check_syntax
        errs = check_syntax("from ez.api.routes.code import save_and_validate")
        assert errs, "P1-1 regression: routes/code import should be blocked"

    def test_legitimate_imports_still_allowed(self):
        from ez.agent.sandbox import check_syntax
        for code in [
            "from ez.factor.base import Factor",
            "from ez.strategy.base import Strategy",
            "import pandas",
            "from ez.portfolio.cross_factor import CrossSectionalFactor",
        ]:
            errs = check_syntax(code)
            assert not errs, f"P1-1 regression: {code!r} should be allowed, got {errs}"

    def test_reload_lock_basic_acquire_release(self):
        """Lock can be acquired + released without exception.

        V2.21: lock moved to closure, accessed via _get_reload_lock().
        """
        from ez.agent.sandbox import _get_reload_lock
        lock = _get_reload_lock()
        with lock:
            pass  # acquire + release without nesting


# ============================================================
# P1-2: pipeline robust to bad step return
# ============================================================

class _DictReturningStep(ResearchStep):
    name = "dict_return"
    def run(self, context):
        return {}  # WRONG: should return PipelineContext


class _ListReturningStep(ResearchStep):
    name = "list_return"
    def run(self, context):
        return [1, 2, 3]


class _NoneReturningStep(ResearchStep):
    name = "none_return"
    def run(self, context):
        return None


def test_p12_dict_return_raises_step_error_with_history():
    """Codex P1-2: bad return type → StepError with history populated,
    not a raw AttributeError on a malformed object."""
    pipeline = ResearchPipeline([_DictReturningStep()])
    with pytest.raises(StepError) as exc_info:
        pipeline.run()
    assert exc_info.value.step_name == "dict_return"
    assert exc_info.value.context is not None
    assert len(exc_info.value.context.history) == 1
    assert exc_info.value.context.history[0].status == "failed"
    assert "dict" in exc_info.value.context.history[0].error.lower()


def test_p12_list_return_raises_step_error_with_history():
    pipeline = ResearchPipeline([_ListReturningStep()])
    with pytest.raises(StepError) as exc_info:
        pipeline.run()
    assert exc_info.value.context is not None
    assert len(exc_info.value.context.history) == 1


# ============================================================
# P2-1: None-return / StepError both record history
# ============================================================

def test_p21_none_return_records_history():
    """Codex P2-1: previously, None-return raised StepError directly
    without going through the history-append branch, so history was empty."""
    pipeline = ResearchPipeline([_NoneReturningStep()])
    with pytest.raises(StepError) as exc_info:
        pipeline.run()
    assert exc_info.value.context is not None
    assert len(exc_info.value.context.history) == 1
    assert exc_info.value.context.history[0].status == "failed"
    assert "None" in exc_info.value.context.history[0].error


class _ExplicitStepErrorStep(ResearchStep):
    name = "explicit_step_error"
    def run(self, context):
        raise StepError("nested", RuntimeError("inner"))


def test_p21_explicit_step_error_records_history():
    """If a step manually raises StepError, history must STILL be
    populated (previously the `except StepError: raise` branch skipped
    the history append)."""
    pipeline = ResearchPipeline([_ExplicitStepErrorStep()])
    with pytest.raises(StepError) as exc_info:
        pipeline.run()
    assert exc_info.value.context is not None
    assert len(exc_info.value.context.history) == 1
    assert exc_info.value.context.history[0].status == "failed"


# ============================================================
# P2-2: StepError carries the partial context
# ============================================================

class _RaisingStep(ResearchStep):
    name = "raising"
    def run(self, context):
        raise ValueError("intentional")


def test_p22_step_error_carries_context_for_pipelines_without_explicit_ctx():
    """Codex P2-2: previously, callers that did `pipeline.run()` (no
    explicit context arg) couldn't inspect partial state on failure
    because they had no reference to the context. StepError.context
    now holds the failed-state context."""
    pipeline = ResearchPipeline([_RaisingStep()])
    with pytest.raises(StepError) as exc_info:
        pipeline.run()  # NO explicit ctx arg
    assert exc_info.value.context is not None
    assert isinstance(exc_info.value.context, PipelineContext)
    assert len(exc_info.value.context.history) == 1


# ============================================================
# P2-3: DataLoadStep market sentinel
# ============================================================

class TestP23DataLoadSentinel:
    @pytest.fixture
    def fake_chain(self, monkeypatch):
        captured = {}
        def fake_fetch(self, sym, market, period, start, end):
            captured.setdefault("calls", []).append({
                "sym": sym, "market": market, "period": period,
            })
            idx = pd.date_range("2024-01-01", periods=10, freq="B")
            return pd.DataFrame({
                "open": np.zeros(10), "high": np.zeros(10), "low": np.zeros(10),
                "close": np.arange(10, dtype=float), "adj_close": np.arange(10, dtype=float),
                "volume": np.zeros(10),
            }, index=idx)
        monkeypatch.setattr(DataLoadStep, "_fetch_one", fake_fetch)
        return captured

    def test_explicit_market_not_overridden_by_config(self, fake_chain):
        """User explicitly passes market='cn_stock' → must NOT be
        overridden by context.config['market']='us_stock'."""
        step = DataLoadStep(
            symbols=["X"], start_date="2024-01-01", end_date="2024-12-31",
            market="cn_stock",
        )
        step.run(PipelineContext(config={"market": "us_stock"}))
        assert fake_chain["calls"][0]["market"] == "cn_stock"

    def test_explicit_period_not_overridden_by_config(self, fake_chain):
        step = DataLoadStep(
            symbols=["X"], start_date="2024-01-01", end_date="2024-12-31",
            period="daily",
        )
        step.run(PipelineContext(config={"period": "hourly"}))
        assert fake_chain["calls"][0]["period"] == "daily"

    def test_none_default_falls_back_to_config(self, fake_chain):
        step = DataLoadStep(
            symbols=["X"], start_date="2024-01-01", end_date="2024-12-31",
        )
        step.run(PipelineContext(config={"market": "us_stock", "period": "hourly"}))
        assert fake_chain["calls"][0]["market"] == "us_stock"
        assert fake_chain["calls"][0]["period"] == "hourly"


# ============================================================
# P2-4: DataLoadStep string symbols rejected
# ============================================================

class TestP24DataLoadStringSymbols:
    def test_constructor_rejects_string_symbols(self):
        with pytest.raises(TypeError, match="must be a list of str"):
            DataLoadStep(symbols="AAA", start_date="2024-01-01", end_date="2024-12-31")

    def test_config_rejects_string_symbols(self):
        step = DataLoadStep(start_date="2024-01-01", end_date="2024-12-31")
        ctx = PipelineContext(config={"symbols": "XYZ"})
        with pytest.raises(TypeError, match="must be a list of str"):
            step.run(ctx)

    def test_list_symbols_still_work(self):
        # Just construct — should not raise
        step = DataLoadStep(symbols=["AAA"], start_date="2024-01-01", end_date="2024-12-31")
        assert step.symbols == ["AAA"]


# ============================================================
# P2-5: stale skipped artifacts cleared on rerun
# ============================================================

class TestP25StaleSkipped:
    """Each step must clear its OWN skipped artifact at the start of
    run() so a clean rerun doesn't render the previous run's warnings.
    """
    @pytest.fixture
    def chain_first_call_skips(self, monkeypatch):
        """Fetch returns empty on first call (skip), valid on subsequent calls."""
        state = {"calls": 0}
        def fetch(self, sym, market, period, start, end):
            state["calls"] += 1
            if state["calls"] == 1:
                return pd.DataFrame()  # empty → skipped
            idx = pd.date_range("2024-01-01", periods=10, freq="B")
            return pd.DataFrame({
                "open": np.zeros(10), "high": np.zeros(10), "low": np.zeros(10),
                "close": np.arange(10, dtype=float), "adj_close": np.arange(10, dtype=float),
                "volume": np.zeros(10),
            }, index=idx)
        monkeypatch.setattr(DataLoadStep, "_fetch_one", fetch)
        return state

    def test_data_load_clears_stale_skipped_on_rerun(self, chain_first_call_skips):
        ctx = PipelineContext()
        # First run: 1 symbol, fetch returns empty → skipped
        step1 = DataLoadStep(symbols=["X"], start_date="2024-01-01", end_date="2024-12-31")
        # First run with one symbol → all-fail → raises
        try:
            step1.run(ctx)
        except RuntimeError:
            pass
        # Manually inject the skipped artifact (simulating a partial-failure run)
        ctx.artifacts["data_load_skipped"] = [("OLD", "stale skip from prior run")]
        ctx.artifacts.pop("universe_data", None)  # discard old result so we exercise rerun path

        # Second run: 1 different symbol, fetch returns valid (because counter > 1)
        step2 = DataLoadStep(symbols=["Y"], start_date="2024-01-01", end_date="2024-12-31")
        out = step2.run(ctx)

        # Stale skipped must be gone
        assert "data_load_skipped" not in out.artifacts, (
            f"P2-5 regression: stale skipped not cleared. "
            f"Got: {out.artifacts.get('data_load_skipped')}"
        )
        assert "Y" in out.artifacts["universe_data"]


# ============================================================
# P2-6: pipeline.run(reset=True) clears prior state
# ============================================================

class _SetMarkerStep(ResearchStep):
    name = "set_marker"
    def run(self, context):
        context.artifacts["marker"] = "from_step"
        return context


def test_p26_run_reset_clears_prior_artifacts_and_history():
    pipeline = ResearchPipeline([_SetMarkerStep()])
    ctx = PipelineContext(
        config={"keep_me": True},
        artifacts={"old": "value"},
    )
    ctx.history.append(StepRecord(
        step_name="prior",
        started_at=__import__("datetime").datetime.now(),
        finished_at=__import__("datetime").datetime.now(),
        duration_ms=0.0,
        status="success",
    ))
    out = pipeline.run(ctx, reset=True)
    # Old artifact gone
    assert "old" not in out.artifacts
    # New artifact present
    assert out.artifacts["marker"] == "from_step"
    # Config preserved
    assert out.config["keep_me"] is True
    # History only contains the current run's record
    assert len(out.history) == 1
    assert out.history[0].step_name == "set_marker"


def test_p26_run_default_no_reset_preserves_prior_state():
    pipeline = ResearchPipeline([_SetMarkerStep()])
    ctx = PipelineContext(artifacts={"old": "value"})
    out = pipeline.run(ctx)  # default reset=False
    assert out.artifacts["old"] == "value"
    assert out.artifacts["marker"] == "from_step"


# ============================================================
# P3-1: report markdown table escape
# ============================================================

class TestP31ReportEscape:
    def test_md_escape_pipe_and_newline(self):
        assert _md_escape("A|B") == "A\\|B"
        assert _md_escape("line1\nline2") == "line1 line2"
        assert _md_escape("a|b\nc|d") == "a\\|b c\\|d"
        assert _md_escape("plain") == "plain"

    def test_default_template_escapes_label_with_pipe(self):
        ctx = PipelineContext(artifacts={
            "metrics": {
                "A|B": {"sharpe_ratio": 1.5},
                "C": {"sharpe_ratio": 2.0},
            }
        })
        md = default_template(ctx)
        # The literal "A|B" should appear escaped, NOT as an unescaped pipe
        assert "A\\|B" in md
        # Make sure each DATA row in the metrics table has consistent
        # cell count (i.e., the escape kept the pipe out of the parser).
        # Only inspect rows that start with "| " (data rows + header) —
        # the "|---|---|" separator row is also valid markdown but uses
        # a different prefix.
        data_rows = [l for l in md.split("\n") if l.startswith("| ")]
        # 1 header + 2 data rows = 3 rows
        assert len(data_rows) >= 3
        cell_counts = [l.count("|") - l.count("\\|") for l in data_rows]
        assert len(set(cell_counts)) == 1, (
            f"P3-1 regression: row cell counts inconsistent: {cell_counts} "
            f"for rows {data_rows}"
        )

    def test_default_template_escapes_returns_column_with_pipe(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="B")
        rets = pd.DataFrame({
            "A|B": [0.01] * 5,
            "C": [0.02] * 5,
        }, index=idx)
        ctx = PipelineContext(artifacts={"returns": rets})
        md = default_template(ctx)
        assert "A\\|B" in md

    def test_default_template_escapes_audit_step_name_with_pipe(self):
        from datetime import datetime as dt
        ctx = PipelineContext()
        ctx.history.append(StepRecord(
            step_name="weird|name",
            started_at=dt.now(),
            finished_at=dt.now(),
            duration_ms=0.5,
            status="success",
            written_keys=("a|b",),
        ))
        md = default_template(ctx)
        assert "weird\\|name" in md
        assert "a\\|b" in md


# ============================================================
# ROUND 4 — additional findings from Claude reviewer audit of round-3
# ============================================================

# ------------------------------------------------------------
# P1-A: attribute chain attack on sandbox
# ------------------------------------------------------------

class TestP1AAttributeChainAttack:
    """Codex round-4 P1-A: `import ez` is legal but
    ``ez.agent.sandbox.<anything>`` is an attribute traversal that
    reaches into a forbidden module. The round-3 ImportFrom check did
    not catch this — only the new AST attribute-chain reconstruction
    in check_syntax does.

    V2.21: _reload_lock moved to closure, but the forbidden-module check
    still blocks any attribute access on ez.agent.sandbox.
    """

    def test_attribute_chain_via_root_import_blocked(self):
        from ez.agent.sandbox import check_syntax
        for code in [
            "import ez\nx = ez.agent.sandbox._get_reload_lock",
            "import ez\nez.agent.sandbox._get_reload_lock()",
            "import ez.agent\nx = ez.agent.sandbox._get_reload_lock",
            "import ez.agent\nsb = ez.agent.sandbox",  # attribute chain to module itself
        ]:
            errs = check_syntax(code)
            assert errs, f"P1-A regression: {code!r} should be blocked"
            assert any("attribute chain" in e.lower() or "forbidden" in e.lower() for e in errs)

    def test_attribute_chain_to_guards_blocked(self):
        from ez.agent.sandbox import check_syntax
        for code in [
            "import ez\nx = ez.testing.guards.suite.GuardSuite",
            "import ez.testing\ny = ez.testing.guards.suite",
        ]:
            errs = check_syntax(code)
            assert errs, f"P1-A regression: {code!r} should be blocked"

    def test_legitimate_attribute_chains_still_allowed(self):
        from ez.agent.sandbox import check_syntax
        # ez.factor.base.Factor is a legitimate attribute chain
        # (ez.factor.base is NOT in _FORBIDDEN_FULL_MODULES).
        for code in [
            "import ez\nfrom ez.factor.base import Factor",  # mixed import + attribute
            "import pandas as pd\ndf = pd.DataFrame()",
            "import numpy as np\nx = np.zeros(10)",
        ]:
            errs = check_syntax(code)
            assert not errs, f"P1-A regression: {code!r} blocked but should be allowed: {errs}"

    def test_reload_lock_is_NOT_rlock_after_round4(self):
        """Round-4 reverted RLock back to Lock so user-code lock attacks
        manifest as immediate hangs (loud) instead of silent persistent
        holds (count poisoning under RLock).

        V2.21: lock is now closure-captured via _get_reload_lock().
        """
        from ez.agent.sandbox import _get_reload_lock
        lock = _get_reload_lock()
        assert lock.acquire(blocking=False)
        try:
            second = lock.acquire(blocking=False)
            if second:
                lock.release()
            assert not second, (
                "P1-A regression: reload lock is an RLock, not a Lock. "
                "RLock allows silent persistent holds via reentrance."
            )
        finally:
            lock.release()

    def test_reload_lock_not_module_attr(self):
        """V2.21: _reload_lock should NOT exist as a module attribute."""
        import ez.agent.sandbox as sandbox_mod
        assert not hasattr(sandbox_mod, "_reload_lock"), (
            "V2.21 regression: _reload_lock should be closure-captured, "
            "not a module-level attribute."
        )


# ------------------------------------------------------------
# P2-A: bytes/bytearray symbols rejected
# ------------------------------------------------------------

class TestP2ABytesSymbols:
    def test_bytes_symbols_rejected(self):
        with pytest.raises(TypeError, match="must be a list of str"):
            DataLoadStep(symbols=b"AAA", start_date="2024-01-01", end_date="2024-12-31")

    def test_bytearray_symbols_rejected(self):
        with pytest.raises(TypeError, match="must be a list of str"):
            DataLoadStep(
                symbols=bytearray(b"AAA"),
                start_date="2024-01-01",
                end_date="2024-12-31",
            )

    def test_bytes_in_config_also_rejected(self):
        step = DataLoadStep(start_date="2024-01-01", end_date="2024-12-31")
        ctx = PipelineContext(config={"symbols": b"XYZ"})
        with pytest.raises(TypeError, match="must be a list of str"):
            step.run(ctx)


# ------------------------------------------------------------
# P2-B: start_date/end_date sentinel consistency
# ------------------------------------------------------------

class TestP2BDateSentinelConsistency:
    @pytest.fixture
    def captured_dates(self, monkeypatch):
        captured = {}
        def fake_fetch(self, sym, market, period, start, end):
            captured.setdefault("calls", []).append({"start": start, "end": end})
            idx = pd.date_range("2024-01-01", periods=10, freq="B")
            return pd.DataFrame({
                "open": np.zeros(10), "high": np.zeros(10), "low": np.zeros(10),
                "close": np.arange(10, dtype=float),
                "adj_close": np.arange(10, dtype=float),
                "volume": np.zeros(10),
            }, index=idx)
        monkeypatch.setattr(DataLoadStep, "_fetch_one", fake_fetch)
        return captured

    def test_explicit_start_date_not_overridden_by_falsy_check(self, captured_dates):
        """Ensure start_date uses `is not None`, not `or`. An explicit
        date object is truthy so the old `or` worked, but the
        consistency fix removes the asymmetry."""
        from datetime import date as _date
        step = DataLoadStep(
            symbols=["X"],
            start_date=_date(2020, 1, 1),
            end_date=_date(2020, 12, 31),
        )
        # Inject a different date in config to verify it doesn't win
        step.run(PipelineContext(config={
            "start_date": "1900-01-01",
            "end_date": "1999-12-31",
        }))
        from datetime import date
        assert captured_dates["calls"][0]["start"] == date(2020, 1, 1)
        assert captured_dates["calls"][0]["end"] == date(2020, 12, 31)


# ------------------------------------------------------------
# P3-A: Warnings section reason escape
# ------------------------------------------------------------

def test_p3a_warnings_section_escapes_newline_in_reason():
    """A reason like 'KeyError\\n  full traceback' would otherwise
    split the bullet item visually."""
    ctx = PipelineContext(artifacts={
        "data_load_skipped": [
            ("BAD", "RuntimeError: line1\nline2\nline3"),
        ],
        "run_strategies_skipped": [
            ("X|Y", "ValueError: bar|baz"),
        ],
    })
    md = default_template(ctx)
    # Newlines should be escaped to spaces
    assert "line1 line2 line3" in md
    # Pipe in label should be escaped
    assert "X\\|Y" in md
    # Pipe in reason should be escaped
    assert "bar\\|baz" in md
    # Bullet item should remain on a single line — count bullets in the
    # Warnings section
    warnings_section = md.split("## Warnings")[-1]
    bullet_lines = [l for l in warnings_section.split("\n") if l.startswith("- `")]
    assert len(bullet_lines) == 2, f"Expected 2 bullets, got {len(bullet_lines)}: {bullet_lines}"


# ------------------------------------------------------------
# P3-B: failure path written_keys captures partial mutation
# ------------------------------------------------------------

class _PartialMutationStep(ResearchStep):
    """Writes one artifact then crashes. Verifies that history records
    the partial mutation in written_keys."""
    name = "partial_mutation"
    def run(self, context):
        context.artifacts["partial_a"] = "first"
        context.artifacts["partial_b"] = "second"
        raise RuntimeError("crash after partial mutation")


def test_p3b_failure_history_records_partial_written_keys():
    pipeline = ResearchPipeline([_PartialMutationStep()])
    with pytest.raises(StepError) as exc_info:
        pipeline.run()
    history = exc_info.value.context.history
    assert len(history) == 1
    rec = history[0]
    assert rec.status == "failed"
    # Codex round-4 P3-B: written_keys should NOT be empty even on failure
    assert "partial_a" in rec.written_keys
    assert "partial_b" in rec.written_keys
    # Partial state is also visible in artifacts
    assert exc_info.value.context.artifacts.get("partial_a") == "first"
    assert exc_info.value.context.artifacts.get("partial_b") == "second"


# ============================================================
# ROUND 5 — codex external review of round-4 fixes
# ============================================================

# ------------------------------------------------------------
# P1: alias / rebinding / walrus / NamedExpr bypass of forbidden
#     attribute chain (the round-4 P1-A fix was incomplete)
# ------------------------------------------------------------

class TestRound5P1AliasBypass:
    """Codex round-5 P1: the round-4 attribute-chain check only handled
    chains rooted at a literal Name with NO binding analysis. User code
    can bind a forbidden module to a different local name and bypass:
      - `import ez as z; z.agent.sandbox._get_reload_lock`
      - `from ez import agent as a; a.sandbox._get_reload_lock`
      - `import ez; z = ez; z.agent.sandbox._get_reload_lock`
      - `(z := ez).agent.sandbox`

    Round-5 fix: build a name binding table during AST walk and resolve
    the chain root through it before checking against forbidden modules.
    """

    def test_import_as_alias_blocked(self):
        from ez.agent.sandbox import check_syntax
        code = "import ez as z\nx = z.agent.sandbox._get_reload_lock"
        errs = check_syntax(code)
        assert errs, "P1 alias bypass: `import ez as z; z.agent.sandbox` not blocked"
        assert any("ez.agent.sandbox" in e for e in errs), (
            f"Expected resolved chain to mention ez.agent.sandbox, got: {errs}"
        )

    def test_from_import_as_alias_blocked(self):
        from ez.agent.sandbox import check_syntax
        code = "from ez import agent as a\nx = a.sandbox._get_reload_lock"
        errs = check_syntax(code)
        assert errs, "P1 alias bypass: `from ez import agent as a; a.sandbox` not blocked"
        assert any("ez.agent.sandbox" in e for e in errs)

    def test_simple_assign_rebinding_blocked(self):
        from ez.agent.sandbox import check_syntax
        code = "import ez\nz = ez\nx = z.agent.sandbox._get_reload_lock"
        errs = check_syntax(code)
        assert errs, "P1 rebinding bypass: `z = ez; z.agent.sandbox` not blocked"
        assert any("ez.agent.sandbox" in e for e in errs)

    def test_walrus_namedexpr_bypass_blocked(self):
        """`(x := ez).agent.sandbox` — walrus operator binds AND yields."""
        from ez.agent.sandbox import check_syntax
        code = "import ez\n(x := ez)\nlock = x.agent.sandbox._get_reload_lock"
        errs = check_syntax(code)
        assert errs, "P1 walrus bypass: `(x := ez); x.agent.sandbox` not blocked"

    def test_attribute_assign_rebinding_blocked(self):
        """`a = ez.agent; a.sandbox._get_reload_lock` — chain rebind."""
        from ez.agent.sandbox import check_syntax
        code = "import ez\na = ez.agent\nlock = a.sandbox._get_reload_lock"
        errs = check_syntax(code)
        assert errs, "P1: `a = ez.agent; a.sandbox` not blocked"

    def test_aliased_imports_not_to_forbidden_still_allowed(self):
        """User can rename non-forbidden ez submodules without trouble."""
        from ez.agent.sandbox import check_syntax
        for code in [
            "from ez.factor.builtin.technical import RSI as MyRSI",
            "from ez.strategy.base import Strategy as Base",
            "import ez.factor.base as factors",
        ]:
            errs = check_syntax(code)
            assert not errs, f"Round-5 P1 false positive: {code!r} → {errs}"


# ------------------------------------------------------------
# P2-1: false positive on local rebinding of forbidden module names
# ------------------------------------------------------------

class TestRound5P21FalsePositiveOnLocalRebinding:
    """Codex round-5 P2-1: round-4's attribute chain check treated any
    chain whose ROOT segment matched _FORBIDDEN_MODULES as forbidden,
    even if the user had locally rebound that name. So `sys = MyClass();
    sys.mean()` was wrongly blocked. Round-5 fix uses the binding table
    — when the root is locally rebound, skip the check.
    """

    def test_sys_rebound_locally_allowed(self):
        from ez.agent.sandbox import check_syntax
        code = "sys = object()\nx = sys.mean"
        errs = check_syntax(code)
        assert not errs, f"P2-1: locally rebound sys wrongly blocked: {errs}"

    def test_os_rebound_locally_allowed(self):
        from ez.agent.sandbox import check_syntax
        code = "os = list()\nx = os.path"
        errs = check_syntax(code)
        assert not errs, f"P2-1: locally rebound os wrongly blocked: {errs}"

    def test_subprocess_rebound_locally_allowed(self):
        """Even .run() / .system() which are in _FORBIDDEN_ATTR_CALLS
        should be allowed when the receiver is a local rebinding."""
        from ez.agent.sandbox import check_syntax
        code = "subprocess = object()\nsubprocess.run()"
        errs = check_syntax(code)
        assert not errs, f"P2-1: subprocess.run() on local rebind wrongly blocked: {errs}"

    def test_real_forbidden_imports_still_blocked(self):
        """Make sure the false-positive fix didn't open the real attack."""
        from ez.agent.sandbox import check_syntax
        for code in [
            "import sys\nsys.exit()",
            "import os\nos.system('rm -rf /')",
            "import subprocess\nsubprocess.run(['ls'])",
        ]:
            errs = check_syntax(code)
            assert errs, f"Round-5 regressed real forbidden import: {code!r}"


# ------------------------------------------------------------
# P2-2: nested StepError context preservation
# ------------------------------------------------------------

class _InnerFailingStep(ResearchStep):
    name = "inner_failing"
    def run(self, context):
        context.artifacts["inner_marker"] = "set_before_fail"
        raise ValueError("inner failure")


class _CompositeStep(ResearchStep):
    """A composite step that runs an inner pipeline and propagates its
    StepError. Mimics what NestedOOSStep / WalkForwardStep will do."""
    name = "composite"
    def run(self, context):
        inner_pipeline = ResearchPipeline([_InnerFailingStep()])
        try:
            inner_pipeline.run(context)
        except StepError:
            # Re-raise as-is to test that the outer pipeline preserves
            # the inner StepError's context (not clobbers it).
            raise


def test_round5_p22_nested_step_error_preserves_inner_context():
    """Codex round-5 P2-2: when a composite step raises a StepError
    that already carries an inner context, the outer pipeline must
    NOT clobber it with its own prev_ctx."""
    pipeline = ResearchPipeline([_CompositeStep()])
    with pytest.raises(StepError) as exc_info:
        pipeline.run()
    err = exc_info.value
    # The inner StepError's step_name and original cause must propagate
    assert err.step_name == "inner_failing"
    assert isinstance(err.original, ValueError)
    assert "inner failure" in str(err.original)
    # The inner context (with the inner_marker) must be preserved,
    # NOT replaced by the outer step's pre-state context
    assert err.context is not None
    assert err.context.artifacts.get("inner_marker") == "set_before_fail"


# ------------------------------------------------------------
# P3-2: configuration section escapes backticks and newlines
# ------------------------------------------------------------

def test_round5_p32_config_value_escapes_backtick_and_newline():
    from ez.research.steps.report import default_template
    ctx = PipelineContext(config={
        "title": "P3-2 test",
        "weird_value": "a`b`c",
        "multiline": "line1\nline2",
    })
    md = default_template(ctx)
    # Backticks in value should be escaped so they don't terminate the code span
    assert "a\\`b\\`c" in md
    # Newlines collapsed to spaces
    assert "line1 line2" in md
    # Configuration section structure intact
    assert "## Configuration" in md
    assert "**weird_value**" in md
    assert "**multiline**" in md
