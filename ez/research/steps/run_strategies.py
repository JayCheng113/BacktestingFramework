"""RunStrategiesStep — implemented in commit 3."""
from ..pipeline import ResearchStep


class RunStrategiesStep(ResearchStep):
    """Stub — implemented in commit 3."""
    name = "run_strategies"
    writes = ("returns", "metrics")

    def run(self, context):
        raise NotImplementedError("RunStrategiesStep implemented in commit 3")
