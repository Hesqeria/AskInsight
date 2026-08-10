"""P6-03: Alert correlation - group related alerts into incidents."""
import uuid


class Incident:
    def __init__(self, incident_id, title, severity, member_alerts, start_time,
                 end_time=None, status="active", correlation_score=1.0, labels=None):
        self.incident_id = incident_id
        self.title = title
        self.severity = severity
        self.member_alerts = member_alerts
        self.start_time = start_time
        self.end_time = end_time
        self.status = status
        self.correlation_score = correlation_score
        self.labels = labels or {}

    def to_dict(self):
        return {"incident_id": self.incident_id, "title": self.title,
                "severity": self.severity, "member_alerts": self.member_alerts,
                "start_time": self.start_time, "end_time": self.end_time,
                "status": self.status, "correlation_score": self.correlation_score,
                "labels": self.labels}


class AlertCorrelator:
    CORRELATION_WINDOW_MINUTES = 10
    CORRELATION_KEYS = ["instance", "host", "dag_id", "table", "topic"]
    SCORE_THRESHOLD = 0.6

    def __init__(self):
        self._incidents = []

    async def correlate(self, alert) -> Incident:
        now = self._ts(alert.first_seen if hasattr(alert, "first_seen") else getattr(alert, "received_at", ""))
        candidates = [inc for inc in self._incidents
                      if inc.status == "active" and
                      abs(self._ts(inc.start_time) - now) < self.CORRELATION_WINDOW_MINUTES * 60]
        best, score = None, 0
        for inc in candidates:
            s = self._score(alert, inc)
            if s > score:
                best, score = inc, s
        if best and score >= self.SCORE_THRESHOLD:
            best.member_alerts.append(alert.alert_uid)
            best.correlation_score = max(best.correlation_score, score)
            return best
        new_inc = Incident(
            incident_id=uuid.uuid4().hex,
            title=alert.title,
            severity=alert.severity,
            member_alerts=[alert.alert_uid],
            start_time=alert.first_seen if hasattr(alert, "first_seen") else alert.received_at,
            correlation_score=1.0,
            labels=alert.labels,
        )
        self._incidents.append(new_inc)
        return new_inc

    def _score(self, alert, incident) -> float:
        shared = 0
        present = 0
        for k in self.CORRELATION_KEYS:
            if k not in incident.labels:
                continue
            present += 1
            if alert.labels.get(k) == incident.labels.get(k):
                shared += 1
        return shared / max(1, present)

    @staticmethod
    def _ts(s):
        if isinstance(s, (int, float)):
            return float(s)
        if isinstance(s, str) and s:
            try:
                from datetime import datetime
                return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
            except Exception:
                return 0.0
        return 0.0

    def get_incidents(self):
        return [i.to_dict() for i in self._incidents]


_correlator = None


def get_correlator() -> AlertCorrelator:
    global _correlator
    if _correlator is None:
        _correlator = AlertCorrelator()
    return _correlator
