"""F2+F4: Batch runner — execute multiple RunSpecs with pre-filter and ranking.

Pipeline: specs → pre-filter → full run (backtest+WFO+gate) → rank → persist.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ez.agent.experiment_store import ExperimentStore
from ez.agent.gates import GateConfig, ResearchGate
from ez.agent.prefilter import PrefilterConfig, PrefilterResult, prefilter
from ez.agent.report import ExperimentReport
from ez.agent.run_spec import RunSpec
from ez.agent.runner import Runner


@dataclass
class BatchConfig:
    """Configuration for a batch search run."""

    gate_config: GateConfig = field(default_factory=GateConfig)
    prefilter_config: PrefilterConfig = field(default_factory=PrefilterConfig)
    skip_prefilter: bool = False


@dataclass
class CandidateResult:
    """Result for a single candidate in the batch."""

    spec: RunSpec
    report: ExperimentReport | None = None
    prefilter: PrefilterResult | None = None
    skipped_duplicate: bool = False

    @property
    def sharpe(self) -> float:
        if self.report and self.report.sharpe_ratio is not None:
            return self.report.sharpe_ratio
        return float("-inf")

    @property
    def gate_passed(self) -> bool:
        return self.report is not None and self.report.gate_passed


@dataclass
class BatchResult:
    """Aggregated result from a batch run."""

    candidates: list[CandidateResult]
    total_specs: int = 0
    prefiltered: int = 0
    executed: int = 0
    duplicates: int = 0

    @property
    def ranked(self) -> list[CandidateResult]:
        """Candidates sorted by Sharpe descending (gate-passed first)."""
        executed = [c for c in self.candidates if c.report is not None]
        return sorted(executed, key=lambda c: (c.gate_passed, c.sharpe, c.spec.spec_id), reverse=True)

    @property
    def passed(self) -> list[CandidateResult]:
        """Only gate-passed candidates, ranked by Sharpe."""
        return [c for c in self.ranked if c.gate_passed]


def run_batch(
    specs: list[RunSpec],
    data: pd.DataFrame,
    config: BatchConfig | None = None,
    store: ExperimentStore | None = None,
) -> BatchResult:
    """Execute batch search pipeline.

    1. Pre-filter (quick backtest-only) to eliminate weak candidates
    2. Full run (backtest + WFO + gate) on survivors
    3. Persist to store (if provided)
    4. Return ranked results
    """
    if config is None:
        config = BatchConfig()

    runner = Runner()
    gate = ResearchGate(config.gate_config)
    result = BatchResult(candidates=[], total_specs=len(specs))

    # Step 1: Pre-filter
    if config.skip_prefilter:
        survivors = specs
    else:
        pf_results = prefilter(specs, data, config.prefilter_config)
        survivors = []
        for pf in pf_results:
            if pf.passed:
                survivors.append(pf.spec)
            else:
                result.candidates.append(CandidateResult(spec=pf.spec, prefilter=pf))
        result.prefiltered = len(specs) - len(survivors)

    # Step 2: Full run on survivors
    for spec in survivors:
        # Duplicate check
        if store is not None:
            existing = store.get_completed_run_id(spec.spec_id)
            if existing:
                result.candidates.append(CandidateResult(spec=spec, skipped_duplicate=True))
                result.duplicates += 1
                continue

        run_result = runner.run(spec, data)
        verdict = gate.evaluate(run_result)
        report = ExperimentReport.from_result(run_result, verdict)

        # Persist
        if store is not None:
            store.save_spec(spec.to_dict())
            inserted = store.save_completed_run(report.to_dict())
            if not inserted:
                # Lost race — another process completed this spec
                result.candidates.append(CandidateResult(spec=spec, skipped_duplicate=True))
                result.duplicates += 1
                continue

        result.candidates.append(CandidateResult(spec=spec, report=report))
        result.executed += 1

    return result
