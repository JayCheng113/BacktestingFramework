# Codex Round-5 Review Request — V2.19.0 round-4 + V2.20.0 round-4 fixes

**Date:** 2026-04-12 (continuation of `2026-04-12-v2.19.0-round3-and-v2.20.0-p1a-codex-review.md`)
**Commit to review:** `6d4e9f7` (single commit, but builds on round-3 fixes in `e36041e`)
**Working directory:** `/Users/zcheng256/BacktestingFramework`
**Test baseline:** 2414 → 2424 passed (+10 round-4 regression tests, zero regression)

---

## Context: review chain so far

You (codex) reviewed `2b7491e^..1522c9d` and found 2 P1 + 6 P2 + 3 P3 in round 3. I fixed all in `e36041e`. Then a Claude reviewer subagent audited my round-3 fixes and found:

- **P1-A**: codex's P1-1 fix was incomplete. The AST `Import` / `ImportFrom` check catches direct imports of `ez.agent.sandbox`, but **attribute traversal** from a legal `import ez` (i.e. `ez.agent.sandbox._reload_lock.acquire()`) bypasses the check entirely. Compounding: round 3 also switched `_reload_lock` from `Lock` to `RLock` as defense-in-depth. Reviewer's key insight — RLock makes the attack STRICTLY MORE DANGEROUS than the pre-fix Lock, because RLock allows silent count poisoning (1→2→1 = lock permanently held after `with` exit) while Lock would deadlock the current save loudly.
- **P2-A**: round-3's `isinstance(symbols, str)` check missed `bytes` / `bytearray`.
- **P2-B**: round-3 fixed `market` / `period` to use `is not None` sentinel but left `start_date` / `end_date` using `or` (asymmetric).
- **P3-A**: ReportStep Warnings section did not escape `\n` in exception messages.
- **P3-B**: failure path's `StepRecord.written_keys` was always `()` (the success path computed it from a diff but the except path did not).
- **P3-C**: docstring example referenced non-existent `BuyHoldSingle("SPY")`.

I addressed all 6 in commit `6d4e9f7`. **You have not yet seen round 4**. This document is the request for codex round-5 review.

---

## Round-4 fixes summary (commit `6d4e9f7`)

```bash
git show 6d4e9f7
git diff e36041e..6d4e9f7
git diff e36041e..6d4e9f7 --stat
```

```
ez/agent/sandbox.py                                   +47 / -11
ez/research/pipeline.py                                +18 / -6
ez/research/steps/data_load.py                         +20 / -10
ez/research/steps/report.py                             +6 / -3
tests/test_agent/test_sandbox.py                        +6 / -2
tests/test_research/test_codex_round2_regressions.py  +233 / -7
6 files changed, 330 insertions, 39 deletions
```

### P1-A: AST attribute-chain reconstruction + RLock → Lock revert

**File:** `ez/agent/sandbox.py`

#### Part 1 — Lock revert

```python
# Reverted from RLock to Lock in codex round-4 (P1-A finding).
#
# History:
#   V2.19.0 round-3 (S4): wrapped _run_guards in _reload_lock for thread-safety
#   V2.19.0 round-3 P1-1 (codex r3): fixed user import bypass with _FORBIDDEN_FULL_MODULES
#   V2.19.0 round-3 P1-1 follow-up: switched to RLock as defense-in-depth
#   V2.19.0 round-4 P1-A (this commit): RLock change was MORE harmful than helpful.
#       Lock would immediately deadlock the current save (loud, easy to detect).
#       RLock allows user code in same thread to silently bump count 1→2; on
#       `with` exit count goes 2→1 and the lock remains permanently held.
#       Subsequent saves from other threads then deadlock with no traceback.
#       Reverting to Lock makes the attack symptom IMMEDIATELY VISIBLE again.
_reload_lock = threading.Lock()
```

#### Part 2 — Attribute chain reconstruction

New helper inside `check_syntax`:

```python
def _reconstruct_attribute_chain(node: ast.AST) -> str | None:
    """Walk up an Attribute chain to reconstruct the dotted name.

    Returns the dotted string (e.g. "ez.agent.sandbox._reload_lock")
    if the chain is rooted at a Name. Returns None for chains starting
    from a Call result (like `get_ez().agent.sandbox`) — those are too
    dynamic to analyze statically and would require runtime defense.
    """
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None
```

New branch in the AST walk loop:

```python
# Block dangerous dunder attribute access (e.g. __subclasses__, __bases__)
elif isinstance(node, ast.Attribute):
    attr = node.attr
    if attr.startswith("__") and attr.endswith("__") and attr not in _SAFE_DUNDERS:
        errors.append(f"Forbidden dunder access: .{attr} (line {node.lineno})")
    # Codex round-4 P1-A: block attribute chains that traverse into
    # a forbidden full module. `import ez` followed by
    # `ez.agent.sandbox._reload_lock` would otherwise bypass the
    # ImportFrom check entirely.
    chain = _reconstruct_attribute_chain(node)
    if chain and _is_forbidden(chain):
        errors.append(
            f"Forbidden attribute chain: {chain} (line {node.lineno}). "
            f"Cannot reach forbidden modules via attribute traversal."
        )
```

The `_is_forbidden` helper is unchanged from round 3 — it checks both root-segment `_FORBIDDEN_MODULES` and full-path `_FORBIDDEN_FULL_MODULES`.

**Verified blocked patterns**:
- `import ez\nx = ez.agent.sandbox._reload_lock` ✓ blocked (chain `ez.agent.sandbox._reload_lock`)
- `import ez.agent\nsb = ez.agent.sandbox` ✓ blocked (chain `ez.agent.sandbox`)
- `import ez\nez.agent.sandbox._reload_lock.acquire()` ✓ blocked (chain `ez.agent.sandbox._reload_lock.acquire`)
- `import ez\nlock = ez.testing.guards.suite.GuardSuite` ✓ blocked

**Verified allowed patterns**:
- `import pandas as pd\ndf = pd.DataFrame()` ✓
- `import ez\n# just import, no chain access` ✓
- `from ez.factor.base import Factor` ✓ (legitimate ez submodule)
- `class X:\n    name = "x"` ✓

### P2-A: bytes/bytearray symbols rejected

**File:** `ez/research/steps/data_load.py`

```python
if isinstance(symbols, (str, bytes, bytearray)):
    raise TypeError(
        f"DataLoadStep symbols must be a list of str, not a single "
        f"{type(symbols).__name__}. Did you mean [{symbols!r}]?"
    )
```

Applied in both the constructor and `_resolve()`. Same pitfall, same defense.

### P2-B: start_date/end_date sentinel consistency

**File:** `ez/research/steps/data_load.py::_resolve`

```python
start = self.start_date if self.start_date is not None else context.config.get("start_date")
end = self.end_date if self.end_date is not None else context.config.get("end_date")
```

(Previously used `self.start_date or context.config.get(...)`.) Now ALL four parameters (symbols/start/end/market/period) use the same `is not None` pattern. Docstring updated.

### P3-A: ReportStep Warnings section reason newlines escaped

**File:** `ez/research/steps/report.py::default_template`

```python
if skipped_data:
    lines.append("**Data load skipped**:")
    for sym, reason in skipped_data:
        lines.append(f"- `{_md_escape(sym)}`: {_md_escape(reason)}")
    lines.append("")
```

The `_md_escape` helper (added in round 3) replaces `|` with `\|` and `\n` / `\r` with space. Now applied to both the symbol/label and the reason.

### P3-B: failure StepRecord.written_keys captures partial mutation

**File:** `ez/research/pipeline.py::run`

```python
prev_ctx = ctx
t0 = time.perf_counter()
started = datetime.now()
pre_keys: set[str] = set()  # lifted out of try so the except can use it
try:
    pre_keys = set(prev_ctx.artifacts.keys())
    returned = step.run(prev_ctx)
    ...
    written = tuple(sorted(set(ctx.artifacts.keys()) - pre_keys))
    ctx.history.append(StepRecord(..., written_keys=written))
except Exception as e:
    # Codex round-4 P3-B: include partial_written keys so a debugger
    # can see what the step DID write before failing. Otherwise the
    # failure record's written_keys is always () even when the step
    # mutated artifacts mid-execution.
    try:
        partial_written = tuple(sorted(set(prev_ctx.artifacts.keys()) - pre_keys))
    except Exception:
        partial_written = ()
    prev_ctx.history.append(StepRecord(
        ..., status="failed", written_keys=partial_written, ...
    ))
```

The inner `try/except` around `partial_written` is paranoid: if `prev_ctx.artifacts` itself was somehow corrupted, we still record the failure (with empty `written_keys`) instead of double-faulting.

### P3-C: docstring example uses real class

**File:** `ez/research/pipeline.py::ResearchPipeline.__doc__`

Changed from `BuyHoldSingle("SPY")` (does not exist) to `MACrossStrategy(short_period=5, long_period=20)` (real class in `ez.strategy.builtin.ma_cross`).

---

## Tests added (10 new round-4 regression tests)

`tests/test_research/test_codex_round2_regressions.py` now has 33 tests (28 round-3 + 10 round-4 + 1 renamed). All passing.

### TestP1AAttributeChainAttack (4 tests)

```python
def test_attribute_chain_via_root_import_blocked(self):
    """4 patterns: import ez + ez.agent.sandbox._reload_lock,
    import ez.agent + chain, attribute access via `with .acquire()`."""

def test_attribute_chain_to_guards_blocked(self):
    """ez.testing.guards.suite.GuardSuite via legal `import ez`."""

def test_legitimate_attribute_chains_still_allowed(self):
    """ez.factor.base.Factor + pandas + numpy still work."""

def test_reload_lock_is_NOT_rlock_after_round4(self):
    """Acquire lock, then `acquire(blocking=False)` from same thread →
    must return False (Lock semantics). RLock would silently bump count."""
```

### TestP2ABytesSymbols (3 tests)

`bytes` symbols rejected at constructor + `bytearray` rejected at constructor + `bytes` from config rejected.

### TestP2BDateSentinelConsistency (1 test)

Explicit `start_date=date(2020,1,1)` not overridden by `config["start_date"]="1900-01-01"`.

### test_p3a_warnings_section_escapes_newline_in_reason

Verifies `RuntimeError: line1\nline2\nline3` escapes to one bullet line, label `"X|Y"` escapes to `X\|Y`, reason `"bar|baz"` escapes to `bar\|baz`. Counts the bullet items in the Warnings section to verify they're not split.

### test_p3b_failure_history_records_partial_written_keys

A step writes `partial_a` and `partial_b` then crashes. Verifies the failure record's `written_keys` contains both keys (not `()`).

### Renamed: test_reload_lock_basic_acquire_release

Was `test_reload_lock_is_reentrant` (round 3, expected RLock with 3-deep nested `with`). After Lock revert, the original test would deadlock itself. Renamed and rewritten to use single-level `with _reload_lock: pass`.

### Existing test updated: test_forbidden_import_os

Was `assert len(errors) == 1`. After P1-A, `import os; os.getcwd()` produces TWO errors (the import line and the attribute chain). Updated to `assert len(errors) >= 1` and check for both `Forbidden import: os` and `os.getcwd`.

---

## Specific things I want codex to scrutinize

### Q1: AST attribute chain reconstruction edge cases

The `_reconstruct_attribute_chain` walks `ast.Attribute` upwards via `.value` until it hits an `ast.Name`. What I'm uncertain about:

**Q1a — Walrus operator inside attribute chain**:

```python
if (x := ez).agent.sandbox._reload_lock:
    pass
```

Here `(x := ez)` is an `ast.NamedExpr`. `_reconstruct_attribute_chain` walks `x._reload_lock → ez._reload_lock → ?`. Actually wait — the AST is `Attribute(value=Attribute(value=Attribute(value=NamedExpr(...), attr='agent'), attr='sandbox'), attr='_reload_lock')`. The walker hits `NamedExpr` (not `Name`), returns `None`. So this attack ISN'T caught. Is this a real vector? `NamedExpr` was added in 3.8 — pretty exotic but valid.

**Q1b — Subscript inside attribute chain**:

```python
sb = sys.modules["ez.agent.sandbox"]
sb._reload_lock.acquire()
```

`sys.modules` is blocked at the import-of-`sys` level (`sys` is in `_FORBIDDEN_MODULES`). But what about `globals()['ez'].agent.sandbox`? `globals` is an `_FORBIDDEN_BUILTINS` call (already checked at AST.Call). What about `vars(some_module)["ez"]`? Same — `vars` is in builtins block. **Are there other attribute-resolution patterns I'm missing?**

**Q1c — Decorator usage**:

```python
@ez.agent.sandbox._some_decorator
def f(): pass
```

The decorator itself is an attribute chain. AST node is `FunctionDef(decorator_list=[Attribute(...)])`. The walker enters via `for node in ast.walk(tree)` which visits ALL nodes. `ast.walk` yields the Attribute node, so the chain check fires. ✓ Caught.

**Q1d — Type annotations**:

```python
def f(x: ez.agent.sandbox._SomeType) -> None: ...
```

The annotation is an attribute chain in `arg.annotation` or `FunctionDef.returns`. `ast.walk` visits these. The chain check fires. ✓ Caught.

But what about:
```python
def f() -> "ez.agent.sandbox._SomeType":  # string annotation
    pass
```

String annotations are `ast.Constant(value="...")`, NOT `ast.Attribute`. The chain check wouldn't see them. They're "forward references" — never executed at module-load time, only resolved by `typing.get_type_hints` if called. **Is this a real attack vector?** I think no, because the string isn't accessed at module-load.

**Q1e — Star unpacking / argument-passing**:

```python
import ez
some_func(*ez.agent.sandbox._args)
```

The `*ez.agent.sandbox._args` is `Starred(value=Attribute(...))`. `ast.walk` visits the inner Attribute node. Caught. ✓

### Q2: Lock revert vs the existing reload_lock acquirers

I reverted `_reload_lock` from RLock to Lock, but multiple sandbox functions acquire it:
- `_reload_user_strategy` (lines ~609)
- `_reload_factor_code` (lines ~1172)
- `_reload_portfolio_code` (lines ~1226)
- `_run_guards` (lines ~154 — added in round 3)

The hooks (1, 2, 3) call `_run_guards` THEN call `_reload_*_code`. With Lock, this is sequential acquire/release/acquire — works.

**Q2a**: Is there any path where Hook 1's `_reload_user_strategy` is called RECURSIVELY (e.g., `_reload_user_strategy` somehow triggers another save which calls `_reload_user_strategy` again)? With Lock, that would deadlock. Please trace.

**Q2b**: What about `_reload_factor_code` calling itself for the rollback? In Hook 2's rollback path (sandbox.py:842), the guard-block path catches an exception and calls `_reload_factor_code(safe_name, target_dir)` to restore the backup. Is there ANY case where the original `_reload_factor_code` is still on the stack with the lock held? Tracing the call chain... I think no, because the original `_reload_factor_code` runs INSIDE the `try` block before guards run, and the guards run AFTER it returns. But please verify.

### Q3: P3-B partial_written semantics

```python
try:
    pre_keys = set(prev_ctx.artifacts.keys())
    returned = step.run(prev_ctx)
    ...
except Exception as e:
    try:
        partial_written = tuple(sorted(set(prev_ctx.artifacts.keys()) - pre_keys))
    except Exception:
        partial_written = ()
```

**Q3a**: If `prev_ctx.artifacts` is mutated during `step.run` AND the mutation includes deletes (`pop("foo")`), `set(prev_ctx.artifacts.keys()) - pre_keys` only captures ADDITIONS, not deletions. The `written_keys` field is named "written" so this might be acceptable (write != delete). But should there be a `deleted_keys` field too? Probably YAGNI for V2.20.0.

**Q3b**: The inner `try/except Exception: partial_written = ()` is paranoid. When could `set(prev_ctx.artifacts.keys())` actually fail? Only if `artifacts` was reassigned to a non-dict OR was somehow mutated to be unhashable. **Is the inner try worth it, or is it deflecting from a real bug we should let propagate?**

### Q4: Lock revert's compatibility with existing reentrance assumptions

Are there any tests or code paths that explicitly relied on `_reload_lock` being RLock? I checked the test suite and there's only one — the round-3 `test_reload_lock_is_reentrant`, which I renamed and rewrote in round 4. **But is there any production code that does `with _reload_lock:` recursively?** Please grep.

Specifically, look at:
```bash
grep -rn "_reload_lock" ez/
```

If any function `f` does `with _reload_lock:` and is called by another function `g` that ALSO does `with _reload_lock:` from inside the lock, Lock would deadlock. RLock would not.

### Q5: P2-B asymmetry follow-up — is the docstring still accurate?

`DataLoadStep._resolve` docstring now claims:

> Constructor args win when explicitly set (i.e. not None) — falls back to context.config when the constructor arg is None.

But `symbols` still uses the same `is not None` check. **Verify all 5 parameters use the same pattern**.

### Q6: P3-A escape only handles ASCII pipes and newlines

`_md_escape` does:

```python
def _md_escape(s) -> str:
    text = str(s)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")
```

What about:
- `\t` (tab) — markdown table ignores tabs in cells, but they could break alignment in some renderers
- Unicode pipe lookalikes like `｜` (U+FF5C, fullwidth) — does any renderer treat them as separators?
- HTML entity injection: a metric value `"<script>alert(1)</script>"` would render as HTML in a markdown viewer that supports inline HTML

This is mostly theoretical for V2.20.0 (no untrusted markdown rendering today), but worth flagging as a known limitation.

### Q7: Coverage of the round-4 fixes vs Q1-Q5 above

Have I added regression tests for ALL the edge cases I'm uncertain about? Specifically:

- Q1c: decorator chain — NO test
- Q1d: type annotation chain — NO test
- Q1e: starred attribute in function call — NO test
- Q2a/Q2b: recursive lock acquire — NO test (would be a runtime hazard test, not unit)

Should some of these become tests now, or are they too speculative?

---

## What I'd like as verdict

**Verdict format**:
- "Codex round-4 fixes complete: YES / NO / WITH FIXES"
- Per Q1-Q7, your assessment of whether each is a real concern
- Any new P1/P2 issues introduced by the round-4 fixes
- Whether the framework is now ready as infrastructure for V2.20.x (NestedOOSStep etc.)

After this round-5 review, my plan is to start V2.20.1 — `NestedOOSStep` + `Optimizer` ABC, replacing `validation/phase_o_nested_oos.py` (411 lines) with a ~50-line ResearchPipeline declaration. If you find architectural issues that would make NestedOOSStep awkward to express in the current framework, please flag them — easier to fix the framework now than after NestedOOSStep is half-built.

---

## Test counts

| version | tests | delta |
|---|---|---|
| V2.18.1 baseline | 2265 | — |
| V2.19.0 base | 2333 | +68 |
| V2.19.0 round 1 (Claude r1) | 2337 | +4 |
| V2.19.0 round 2 (codex r2) | 2342 | +5 |
| V2.19.0 round 3 (Claude final-gate) | 2343 | +1 |
| V2.20.0 P1-A MVP | 2391 | +48 |
| codex round 3 fixes | 2414 | +23 |
| **codex round 4 fixes (this commit)** | **2424** | **+10** |

Zero existing test regression throughout.

---

## Files changed (full list)

```
ez/agent/sandbox.py                                    +47 / -11
  - _reload_lock: RLock → Lock revert (with detailed history comment)
  - check_syntax: new _reconstruct_attribute_chain helper + new
    ast.Attribute branch checking attribute chains against _is_forbidden

ez/research/pipeline.py                                +18 / -6
  - run(): pre_keys lifted out of try, partial_written computed in except
  - docstring: BuyHoldSingle → MACrossStrategy

ez/research/steps/data_load.py                         +20 / -10
  - __init__: isinstance(symbols, (str, bytes, bytearray))
  - _resolve: same bytes check on context.config['symbols']
  - _resolve: start_date/end_date use `is not None` (was `or`)
  - _resolve: docstring updated to declare unified pattern

ez/research/steps/report.py                            +6 / -3
  - default_template: Warnings section now wraps sym/label AND reason in _md_escape

tests/test_agent/test_sandbox.py                       +6 / -2
  - test_forbidden_import_os: assert >= 1 error (was == 1), check both
    "Forbidden import: os" AND "os.getcwd" (attribute chain)

tests/test_research/test_codex_round2_regressions.py  +233 / -7
  - Renamed test_reload_lock_is_reentrant → test_reload_lock_basic_acquire_release
    (no longer 3-deep nested with — Lock can't reentrance)
  - New TestP1AAttributeChainAttack class (4 tests)
  - New TestP2ABytesSymbols class (3 tests)
  - New TestP2BDateSentinelConsistency class (1 test)
  - New test_p3a_warnings_section_escapes_newline_in_reason
  - New test_p3b_failure_history_records_partial_written_keys
```
