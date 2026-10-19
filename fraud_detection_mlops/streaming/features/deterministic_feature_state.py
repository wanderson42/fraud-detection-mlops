"""Use the legacy causal transaction boundaries with a separate exact mean strategy."""

from fraud_detection_mlops.features.exact_binary_mean import ExactBinaryMean
from fraud_detection_mlops.streaming.replay.durable_triage_replay import DurableTriageReplay


class ExactMeanConnection:
    """Delegate SQLite transactions and replace only the reviewed mean expression."""

    def __init__(self, connection):
        self.connection = connection
        connection.create_aggregate("exact_binary_mean", 1, ExactBinaryMean)

    def execute(self, query, parameters=()):
        if "AVG(amount)" in query:
            if not query.startswith("SELECT COUNT(*), COALESCE(AVG(amount), 0.0)"):
                raise ValueError("Unreviewed mean query")
            query = query.replace("AVG(amount)", "exact_binary_mean(amount)")
        return self.connection.execute(query, parameters)

    def __getattr__(self, name):
        return getattr(self.connection, name)


class DeterministicTriageReplay(DurableTriageReplay):
    """Same atomic persistence; caller binds arithmetic and all code into input SHA-256."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.connection = ExactMeanConnection(self.connection)
