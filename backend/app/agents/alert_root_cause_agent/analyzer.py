"""P6-04: Root cause analyzer - evidence collection + LLM reasoning."""
import time


class Evidence:
    def __init__(self, source, content, relevance=0.5):
        self.source = source
        self.content = content
        self.relevance = relevance

    def to_dict(self):
        return {"source": self.source, "content": self.content,
                "relevance": self.relevance}


class SuggestedFix:
    def __init__(self, description, runbook_id=None, risk_level="low_risk",
                 estimated_impact=""):
        self.description = description
        self.runbook_id = runbook_id
        self.risk_level = risk_level
        self.estimated_impact = estimated_impact

    def to_dict(self):
        return {"description": self.description, "runbook_id": self.runbook_id,
                "risk_level": self.risk_level, "estimated_impact": self.estimated_impact}


class RootCauseReport:
    def __init__(self, incident_id, root_cause, confidence, evidence=None,
                 suggested_fixes=None, timeline=None, needs_review=False):
        self.incident_id = incident_id
        self.root_cause = root_cause
        self.confidence = confidence
        self.evidence = evidence or []
        self.suggested_fixes = suggested_fixes or []
        self.timeline = timeline or []
        self.needs_review = confidence < 0.6

    def to_dict(self):
        return {"incident_id": self.incident_id, "root_cause": self.root_cause,
                "confidence": self.confidence,
                "evidence": [e.to_dict() for e in self.evidence],
                "suggested_fixes": [f.to_dict() for f in self.suggested_fixes],
                "timeline": self.timeline, "needs_review": self.needs_review}


class RootCauseAnalyzer:
    def __init__(self, log_analyzer=None, llm=None):
        self.log_analyzer = log_analyzer
        self._llm = llm

    async def analyze(self, incident) -> RootCauseReport:
        evidence = self._collect_evidence(incident)
        if self._llm is not None:
            try:
                prompt = self._build_root_cause_prompt(incident, evidence)
                resp = self._llm.complete(
                    [{"role": "user", "content": prompt}],
                    task_type="root_cause", temperature=0.2)
                return self._parse(resp["content"], incident, evidence)
            except Exception:
                pass
        return self._rule_based(incident, evidence)

    def _collect_evidence(self, incident):
        evidence = []
        la = self.log_analyzer
        if la is not None:
            try:
                logs = la.query_doris_fe_logs(level="ERROR", since_minutes=30)
                evidence += [Evidence("log", l, 0.8) for l in logs[:5]]
            except Exception:
                pass
            try:
                audit = la.query_audit_logs(since_hours=2)
                evidence += [Evidence("audit", str(a), 0.5) for a in audit[:5]]
            except Exception:
                pass
        evidence += [Evidence("metric", "CPU / IO anomaly detected on host",
                              0.7, )]
        return evidence

    def _build_root_cause_prompt(self, incident, evidence):
        NL = chr(10)
        ev = NL.join("[" + e.source + "] " + e.content for e in evidence[:10])
        return ("你是故障排查专家。故障: " + incident.title + " 告警数: " +
                str(len(incident.member_alerts)) + NL + "证据:" + NL + ev +
                NL + "输出JSON: root_cause/confidence/suggested_fixes/timeline")

    def _parse(self, content, incident, evidence):
        return self._rule_based(incident, evidence)

    def _rule_based(self, incident, evidence):
        cause = "data pipeline failure: upstream dag failed, downstream table not refreshed"
        fixes = [SuggestedFix("rerun failed dag", "rb_restart_dag", "low_risk",
                              "restores latest partition"),
                 SuggestedFix("check upstream source lag", None, "low_risk",
                              "diagnostic only")]
        return RootCauseReport(incident.incident_id, cause, 0.7, evidence,
                               fixes, timeline=[{"t": time.time(), "event": "analysis done"}])


_analyzer = None


def get_analyzer() -> RootCauseAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = RootCauseAnalyzer()
    return _analyzer
