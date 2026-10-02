"""P6-02: Alert dedup & noise reduction."""
import hashlib
import uuid
import time


class DedupedAlert:
    def __init__(self, alert_uid, member_alert_ids, source, title, severity,
                 fingerprint, labels=None, first_seen="", last_seen="",
                 occurrence_count=1):
        self.alert_uid = alert_uid
        self.member_alert_ids = member_alert_ids
        self.source = source
        self.title = title
        self.severity = severity
        self.fingerprint = fingerprint
        self.labels = labels or {}
        self.first_seen = first_seen
        self.last_seen = last_seen
        self.occurrence_count = occurrence_count

    def to_dict(self):
        return {"alert_uid": self.alert_uid, "member_alert_ids": self.member_alert_ids,
                "source": self.source, "title": self.title, "severity": self.severity,
                "fingerprint": self.fingerprint, "labels": self.labels,
                "first_seen": self.first_seen, "last_seen": self.last_seen,
                "occurrence_count": self.occurrence_count}


class AlertDeduper:
    FINGERPRINT_KEYS = ["source", "title", "severity"]
    DEDUP_WINDOW_MINUTES = 5
    ESCALATE_THRESHOLD = 5
    # Run a sweep of expired entries at most every N writes so the
    # `_store` dict doesn't grow without bound in long-running
    # processes. Sweeping on every write is wasteful when the dict is
    # small; this batches the work.
    _SWUP_INTERVAL = 64

    def __init__(self, redis=None):
        self.redis = redis
        self._store = {}
        self._writes_since_sweep = 0

    async def process(self, alert) -> DedupedAlert:
        fp = self._fingerprint(alert)
        existing = self._get(fp)
        if existing:
            existing.member_alert_ids.append(alert.alert_id)
            existing.occurrence_count += 1
            existing.last_seen = getattr(alert, "received_at", "") or existing.last_seen
            existing.severity = self._escalate(existing.occurrence_count, existing.severity)
            self._set(fp, existing)
            return existing
        deduped = DedupedAlert(
            alert_uid=uuid.uuid4().hex, member_alert_ids=[alert.alert_id],
            source=alert.source, title=alert.title, severity=alert.severity,
            fingerprint=fp, labels=alert.labels,
            first_seen=getattr(alert, "received_at", ""),
            last_seen=getattr(alert, "received_at", ""), occurrence_count=1,
        )
        self._set(fp, deduped)
        return deduped

    def _fingerprint(self, alert) -> str:
        parts = [str(getattr(alert, k, "")) for k in self.FINGERPRINT_KEYS]
        for lk in ["instance", "job", "dag_id", "table"]:
            labels = getattr(alert, "labels", {}) or {}
            if lk in labels:
                parts.append(f"{lk}={labels[lk]}")
        return hashlib.md5("|".join(parts).encode()).hexdigest()

    @staticmethod
    def _escalate(count, severity):
        if count >= 5 and severity == "info":
            return "warning"
        if count >= 10 and severity == "warning":
            return "critical"
        return severity

    def _get(self, fp):
        if self.redis is not None:
            try:
                data = self.redis.get(f"da:alert_fp:{fp}")
                return data
            except Exception:
                pass
        entry = self._store.get(fp)
        if entry and time.time() - entry[1] < self.DEDUP_WINDOW_MINUTES * 60:
            return entry[0]
        return None

    def _set(self, fp, value):
        if self.redis is not None:
            try:
                self.redis.setex(f"da:alert_fp:{fp}", self.DEDUP_WINDOW_MINUTES * 60, value)
                return
            except Exception:
                pass
        self._store[fp] = (value, time.time())
        # Lazy GC: periodically drop expired entries so the dict can't
        # grow unbounded when Redis isn't configured.
        self._writes_since_sweep += 1
        if self._writes_since_sweep >= self._SWUP_INTERVAL:
            self._writes_since_sweep = 0
            self._sweep_expired()

    def _sweep_expired(self):
        now = time.time()
        cutoff = self.DEDUP_WINDOW_MINUTES * 60
        expired = [fp for fp, (_v, ts) in self._store.items()
                   if now - ts >= cutoff]
        for fp in expired:
            self._store.pop(fp, None)


_deduper = None


def get_deduper() -> AlertDeduper:
    global _deduper
    if _deduper is None:
        _deduper = AlertDeduper()
    return _deduper
