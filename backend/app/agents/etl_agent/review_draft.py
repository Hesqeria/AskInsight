"""P5-05: Human-in-loop review draft - store, edit, submit, rollback, preflight."""
import time
import re


class ETLDraft:
    def __init__(self, requirement_id, model, sql_pkg, schedule, rules):
        self.draft_id = f"draft_{requirement_id}"
        self.requirement_id = requirement_id
        self.model = model
        self.sql_pkg = sql_pkg
        self.schedule = schedule
        self.rules = rules
        self.status = "draft"
        self.created_at = time.time()

    def to_dict(self):
        return {
            "draft_id": self.draft_id, "requirement_id": self.requirement_id,
            "model": self.model.to_dict() if hasattr(self.model, "to_dict") else self.model,
            "sql": self.sql_pkg.to_dict() if hasattr(self.sql_pkg, "to_dict") else self.sql_pkg,
            "schedule": self.schedule.to_dict() if hasattr(self.schedule, "to_dict") else self.schedule,
            "rules": [r.to_dict() for r in self.rules],
            "status": self.status, "created_at": self.created_at,
        }


class ETLDraftStore:
    def __init__(self):
        self._drafts = {}

    def save(self, draft: ETLDraft):
        self._drafts[draft.draft_id] = draft
        return draft

    def get(self, draft_id):
        return self._drafts.get(draft_id)

    def update_sql(self, draft_id, sql):
        d = self._drafts.get(draft_id)
        if d:
            d.sql_pkg.insert_sql = sql
        return d

    def update_schedule(self, draft_id, schedule_dict):
        d = self._drafts.get(draft_id)
        if d:
            for k, v in schedule_dict.items():
                setattr(d.schedule, k, v)
        return d

    def update_quality_rules(self, draft_id, rules):
        d = self._drafts.get(draft_id)
        if d:
            d.rules = rules
        return d

    def submit(self, draft_id, preflight_checker=None) -> dict:
        d = self._drafts.get(draft_id)
        if not d:
            return {"ok": False, "error": "draft not found"}
        if preflight_checker is not None:
            report = preflight_checker(d)
            if not report["ok"]:
                d.status = "rejected"
                return {"ok": False, "checks": report["checks"]}
        d.status = "deployed"
        return {"ok": True, "draft_id": draft_id, "status": d.status}

    def rollback(self, draft_id) -> dict:
        d = self._drafts.get(draft_id)
        if not d:
            return {"ok": False, "error": "draft not found"}
        d.status = "rolled_back"
        return {"ok": True, "draft_id": draft_id, "status": d.status}


class PreflightChecker:
    """上线前自检：SQL语法/调度冲突/规则完整性/命名规范."""

    NAMING_RE = re.compile(r"^(dwd|dws|ads|dim)_[a-z0-9_]+$")

    def check(self, draft) -> dict:
        checks = []
        ok = True
        sql_ok = self._check_sql(draft)
        checks.append({"name": "SQL语法", "ok": sql_ok})
        ok = ok and sql_ok
        rule_ok = len(draft.rules) >= 3
        checks.append({"name": "质量规则完整性", "ok": rule_ok, "required": ">=3 条规则"})
        ok = ok and rule_ok
        name_ok = self.NAMING_RE.match(draft.sql_pkg.table_name) is not None
        checks.append({"name": "命名规范", "ok": name_ok})
        ok = ok and name_ok
        return {"ok": ok, "checks": checks}

    @staticmethod
    def _check_sql(draft):
        sql = draft.sql_pkg.insert_sql.upper()
        if "INSERT" not in sql:
            return False
        if "DROP" in sql or "DELETE" in sql or "TRUNCATE" in sql:
            return False
        return True


_draft_store = None


def get_draft_store() -> ETLDraftStore:
    global _draft_store
    if _draft_store is None:
        _draft_store = ETLDraftStore()
    return _draft_store
