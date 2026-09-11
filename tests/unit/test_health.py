from production_optimizer.api import ProbeStatus, ReadinessProbe


def test_readiness_fails_closed_when_a_dependency_raises() -> None:
    def unavailable() -> bool:
        raise RuntimeError("not available")

    report = ReadinessProbe(
        {"artifact_store": lambda: True, "checkpoint": unavailable}, version="0.1.0"
    ).inspect()
    assert report.status == ProbeStatus.DOWN
    assert report.checks == {
        "artifact_store": ProbeStatus.UP,
        "checkpoint": ProbeStatus.DOWN,
    }
