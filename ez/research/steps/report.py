"""ReportStep — implemented in commit 4."""
from ..pipeline import ResearchStep


class ReportStep(ResearchStep):
    """Stub — implemented in commit 4."""
    name = "report"
    writes = ("report",)

    def run(self, context):
        raise NotImplementedError("ReportStep implemented in commit 4")
