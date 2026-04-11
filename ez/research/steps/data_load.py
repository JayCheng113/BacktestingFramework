"""DataLoadStep — implemented in commit 2."""
from ..pipeline import ResearchStep


class DataLoadStep(ResearchStep):
    """Stub — implemented in commit 2."""
    name = "data_load"
    writes = ("universe_data",)

    def run(self, context):
        raise NotImplementedError("DataLoadStep implemented in commit 2")
