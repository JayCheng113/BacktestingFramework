"""自主研究 agent：假设生成 → 代码编写 → 回测 → 分析 → 迭代优化。"""
from ez.agent.research.hypothesis import ResearchGoal, generate_hypotheses
from ez.agent.research.code_gen import generate_strategy_code
from ez.agent.research.analyzer import AnalysisResult, analyze_results
from ez.agent.research.loop_controller import LoopConfig, LoopController, LoopState
from ez.agent.research.report import ResearchReport, build_report
from ez.agent.research.runner import run_research_task
from ez.agent.research.store import ResearchStore
