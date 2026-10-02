"""P6 Alert root cause tests."""
import asyncio


def _make_raw(source="prometheus", title="HighCPU", severity="warning", labels=None):
    from app.alerts.ingestor import RawAlert
    return RawAlert(alert_id="fp-123", source=source, title=title,
                    severity=severity, labels=labels or {"host": "h1", "instance": "be1"},
                    starts_at="2024-01-01T00:00:00Z")


def test_ingestor_prometheus_webhook():
    from app.alerts.ingestor import AlertIngestor
    ing = AlertIngestor()
    count = asyncio.run(ing.ingest_prometheus(
        {"alerts": [{"fingerprint": "f1", "labels": {"alertname": "CPUHigh", "severity": "warning"},
                     "annotations": {}, "status": "firing", "startsAt": "2024-01-01T00:00:00Z"}]}))
    assert count == 1
    assert len(ing.get_ingested()) == 1
    assert ing.get_ingested()[0]["source"] == "prometheus"


def test_ingestor_airflow_event():
    from app.alerts.ingestor import AlertIngestor
    ing = AlertIngestor()
    uid = asyncio.run(ing.ingest_airflow_event({"dag_id": "dag_etl_daily",
                                                "task_id": "load", "dag_run_id": "run-1"}))
    assert uid
    assert ing.get_ingested()[0]["source"] == "airflow"


def test_deduper_same_fingerprint_merges():
    from app.alerts.deduper import AlertDeduper
    d = AlertDeduper()
    a1 = _make_raw()
    a2 = _make_raw()
    r1 = asyncio.run(d.process(a1))
    r2 = asyncio.run(d.process(a2))
    assert r1.alert_uid == r2.alert_uid
    assert r2.occurrence_count == 2
    assert "fp-123" in r2.member_alert_ids


def test_deduper_different_fingerprint():
    from app.alerts.deduper import AlertDeduper
    d = AlertDeduper()
    r1 = asyncio.run(d.process(_make_raw(title="CPU")))
    r2 = asyncio.run(d.process(_make_raw(title="DiskFull")))
    assert r1.alert_uid != r2.alert_uid


def test_deduper_escalation():
    from app.alerts.deduper import AlertDeduper
    d = AlertDeduper()
    result = None
    for i in range(6):
        result = asyncio.run(d.process(_make_raw(severity="info")))
    assert result.severity == "warning"


def test_deduper_window_expiry():
    from app.alerts.deduper import AlertDeduper
    d = AlertDeduper()
    r1 = asyncio.run(d.process(_make_raw()))
    d._store[r1.fingerprint] = (r1, 0)  # simulate expiry
    r2 = asyncio.run(d.process(_make_raw()))
    assert r1.alert_uid != r2.alert_uid


def test_correlator_same_host():
    from app.alerts.correlator import AlertCorrelator
    from app.alerts.deduper import DedupedAlert
    c = AlertCorrelator()
    a1 = DedupedAlert("u1", ["fp1"], "prometheus", "CPU", "warning", "fp1",
                      labels={"host": "h1"}, first_seen="2024-01-01T00:00:00Z")
    a2 = DedupedAlert("u2", ["fp2"], "prometheus", "Disk", "warning", "fp2",
                      labels={"host": "h1"}, first_seen="2024-01-01T00:00:05Z")
    inc1 = asyncio.run(c.correlate(a1))
    inc2 = asyncio.run(c.correlate(a2))
    assert inc1.incident_id == inc2.incident_id
    assert len(inc2.member_alerts) == 2


def test_correlator_cross_source():
    from app.alerts.correlator import AlertCorrelator
    from app.alerts.deduper import DedupedAlert
    c = AlertCorrelator()
    a1 = DedupedAlert("u1", ["fp1"], "prometheus", "CPU", "warning", "fp1",
                      labels={"dag_id": "dag_etl"}, first_seen="2024-01-01T00:00:00Z")
    a2 = DedupedAlert("u2", ["fp2"], "airflow", "DagFailed", "warning", "fp2",
                      labels={"dag_id": "dag_etl"}, first_seen="2024-01-01T00:00:01Z")
    inc1 = asyncio.run(c.correlate(a1))
    inc2 = asyncio.run(c.correlate(a2))
    assert inc1.incident_id == inc2.incident_id


def test_correlator_different_host():
    from app.alerts.correlator import AlertCorrelator
    from app.alerts.deduper import DedupedAlert
    c = AlertCorrelator()
    a1 = DedupedAlert("u1", ["fp1"], "prometheus", "CPU", "warning", "fp1",
                      labels={"host": "h1"}, first_seen="2024-01-01T00:00:00Z")
    a2 = DedupedAlert("u2", ["fp2"], "prometheus", "Disk", "warning", "fp2",
                      labels={"host": "h2"}, first_seen="2024-01-01T00:00:01Z")
    inc1 = asyncio.run(c.correlate(a1))
    inc2 = asyncio.run(c.correlate(a2))
    assert inc1.incident_id != inc2.incident_id


def test_root_cause_report():
    from app.agents.alert_root_cause_agent.analyzer import RootCauseAnalyzer
    from app.alerts.correlator import Incident
    inc = Incident("inc-1", "GMV drop", "critical", ["u1"], "2024-01-01T00:00:00Z")
    report = asyncio.run(RootCauseAnalyzer().analyze(inc))
    assert report.incident_id == "inc-1"
    assert report.root_cause
    assert len(report.evidence) >= 1
    assert any(e.source == "metric" for e in report.evidence)
    assert report.confidence >= 0.5


def test_root_cause_needs_review():
    from app.agents.alert_root_cause_agent.analyzer import RootCauseReport
    r = RootCauseReport("inc-2", "unknown", 0.4)
    assert r.needs_review is True
    r2 = RootCauseReport("inc-2", "known", 0.8)
    assert r2.needs_review is False


def test_runbook_low_risk_auto_executes():
    from app.agents.alert_root_cause_agent.runbook_executor import RunbookExecutor, Runbook, RunbookStep
    from app.alerts.correlator import Incident
    rb = Runbook("rb1", "restart dag", risk_level="low_risk",
                 steps=[RunbookStep(1, "airflow_trigger", {"dag_id": "dag_etl_daily"}),
                        RunbookStep(2, "wait", {})])
    inc = Incident("inc-3", "t", "warning", ["u"], "t0")

    async def verifier(_inc):
        # Simulate the live alerting system confirming the alert cleared.
        return True

    res = asyncio.run(RunbookExecutor(incident_verifier=verifier).execute(rb, inc))
    assert res.status == "success"
    assert inc.status == "resolved"


def test_runbook_partial_when_still_active():
    """When no external verifier is wired and the incident is still
    active, the runbook must NOT auto-mark it resolved (regression test
    for the previously inverted `_verify_incident_resolved` check)."""
    from app.agents.alert_root_cause_agent.runbook_executor import RunbookExecutor, Runbook, RunbookStep
    from app.alerts.correlator import Incident
    rb = Runbook("rb1b", "restart dag", risk_level="low_risk",
                 steps=[RunbookStep(1, "airflow_trigger", {"dag_id": "dag_etl_daily"}),
                        RunbookStep(2, "wait", {})])
    inc = Incident("inc-3b", "t", "warning", ["u"], "t0")
    res = asyncio.run(RunbookExecutor().execute(rb, inc))
    assert res.status == "partial"
    assert inc.status == "active"


def test_runbook_high_risk_needs_approval():
    from app.agents.alert_root_cause_agent.runbook_executor import RunbookExecutor, Runbook
    from app.alerts.correlator import Incident
    rb = Runbook("rb2", "drop table", risk_level="high_risk", steps=[])
    inc = Incident("inc-4", "t", "critical", ["u"], "t0")
    res = asyncio.run(RunbookExecutor().execute(rb, inc))
    assert res.status == "pending_approval"
    assert res.ticket_id


def test_runbook_failed_step_rollback():
    from app.agents.alert_root_cause_agent.runbook_executor import RunbookExecutor, Runbook, RunbookStep
    from app.alerts.correlator import Incident

    class Boom:
        async def trigger_dag(self, *a, **k):
            raise RuntimeError("boom")

    rb = Runbook("rb3", "multi", risk_level="low_risk",
                 steps=[RunbookStep(1, "airflow_trigger", {"dag_id": "x"}),
                        RunbookStep(2, "notify", {},
                                    rollback_action={"action": "notify",
                                                     "parameters": {"title": "rollback"}})])
    inc = Incident("inc-5", "t", "warning", ["u"], "t0")
    res = asyncio.run(RunbookExecutor(scheduler=Boom()).execute(rb, inc))
    assert res.status == "failed"


def test_analyzer_parses_llm_json():
    """RootCauseAnalyzer must actually parse the LLM's JSON response
    instead of discarding it (regression test for the dead-code _parse)."""
    from app.agents.alert_root_cause_agent.analyzer import RootCauseAnalyzer
    from app.alerts.correlator import Incident

    class FakeLLM:
        def complete(self, msgs, task_type=None, temperature=0):
            return {"content": (
                'Here is the analysis:\n'
                '```json\n'
                '{"root_cause": "Redis连接池耗尽", "confidence": 0.92,'
                ' "suggested_fixes": ['
                '{"description": "扩容连接池", "runbook_id": "rb_pool",'
                ' "risk_level": "low_risk", "estimated_impact": "立即恢复"},'
                '"重启服务"],'
                ' "timeline": [{"t": 1700000000, "event": "drop observed"}]}\n'
                '```')}

    inc = Incident("inc-6", "GMV drop", "critical", ["u"], "t0")
    report = asyncio.run(RootCauseAnalyzer(llm=FakeLLM()).analyze(inc))
    assert report.root_cause == "Redis连接池耗尽"
    assert report.confidence == 0.92
    assert report.needs_review is False  # 0.92 >= 0.6
    assert len(report.suggested_fixes) == 2
    assert report.suggested_fixes[0].runbook_id == "rb_pool"
    assert report.suggested_fixes[1].description == "重启服务"
    assert report.suggested_fixes[1].runbook_id is None
    assert report.timeline == [{"t": 1700000000, "event": "drop observed"}]


def test_analyzer_falls_back_on_garbage_llm_output():
    from app.agents.alert_root_cause_agent.analyzer import RootCauseAnalyzer
    from app.alerts.correlator import Incident

    class FakeLLM:
        def complete(self, msgs, task_type=None, temperature=0):
            return {"content": "sorry, I cannot help with that"}

    inc = Incident("inc-7", "GMV drop", "critical", ["u"], "t0")
    report = asyncio.run(RootCauseAnalyzer(llm=FakeLLM()).analyze(inc))
    # Falls back to rule-based; still produces a usable report.
    assert "data pipeline failure" in report.root_cause
    assert report.confidence == 0.7


def test_ingestor_bounded_buffer_evicts_oldest():
    """`_ingested` must stay bounded in long-running processes."""
    from app.alerts.ingestor import AlertIngestor, RawAlert
    ing = AlertIngestor(max_ingested=3)
    for i in range(5):
        asyncio.run(ing.ingest(RawAlert(alert_id=f"a{i}", source="prometheus",
                                        title="t", severity="warning",
                                        starts_at="t0")))
    buf = ing.get_ingested()
    assert len(buf) == 3
    # Oldest two were evicted.
    assert buf[0]["alert_id"] == "a2"
    assert buf[-1]["alert_id"] == "a4"


def test_deduper_evicts_expired_entries():
    """`_store` must not grow without bound when Redis is absent."""
    from app.alerts.deduper import AlertDeduper
    from app.alerts.ingestor import RawAlert
    d = AlertDeduper()
    d.DEDUP_WINDOW_MINUTES = 0  # expire immediately

    # Push exactly _SWUP_INTERVAL entries. The last write triggers a
    # sweep that drops every entry (all are now past their 0-minute TTL).
    for i in range(d._SWUP_INTERVAL):
        asyncio.run(d.process(RawAlert(alert_id=f"a{i}", source="src",
                                       title=f"t{i}", severity="warning",
                                       starts_at="t0")))
    assert d._store == {}

    # And a non-expiring deduper keeps its entries bounded only by the
    # window - confirming the sweep only drops actually-expired ones.
    d2 = AlertDeduper()
    for i in range(d2._SWUP_INTERVAL + 10):
        asyncio.run(d2.process(RawAlert(alert_id=f"b{i}", source="src",
                                        title=f"u{i}", severity="warning",
                                        starts_at="t0")))
    assert len(d2._store) == d2._SWUP_INTERVAL + 10  # nothing expired
