from app.worker import AgentWorker


class RecordingRepository:
    def __init__(self, fixed_delay_ms: float = 10) -> None:
        self.appended: list[tuple[int, dict]] = []
        self.fixed_delay_ms = fixed_delay_ms
        self.coordination_duration_ms = 0.0

    def append_event(self, run_id, *, graph_sequence, event_type, payload):
        self.coordination_duration_ms += self.fixed_delay_ms
        self.appended.append((graph_sequence, payload))
        return {"event_id": len(self.appended)}


def test_cumulative_graph_snapshots_append_each_event_once() -> None:
    worker = AgentWorker.__new__(AgentWorker)
    worker.repository = RecordingRepository()
    persisted: set[str] = set()

    for event_count in range(1, 12):
        events = [
            {"node": f"node-{index}", "status": "success", "summary": "done"}
            for index in range(1, event_count + 1)
        ]
        worker._persist_graph_events(
            "run-test",
            {"events": events, "status_bar": None},
            persisted,
        )

    assert len(worker.repository.appended) == 11
    assert [item[0] for item in worker.repository.appended] == list(range(1, 12))
    assert len(persisted) == 11
    previous_attempts = sum(range(1, 12))
    previous_coordination_ms = previous_attempts * worker.repository.fixed_delay_ms
    assert worker.repository.coordination_duration_ms <= previous_coordination_ms * 0.5
