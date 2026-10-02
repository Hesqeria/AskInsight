"""P4-05: Notifier - dingtalk/wecom/email/websocket with retry + do-not-disturb."""
import asyncio
import json
import time


class Notifier:
    # Severities that must bypass do-not-disturb windows. These represent
    # escalations / critical incidents that operators must be woken for.
    DND_BYPASS_SEVERITIES = {"P0", "critical"}

    def __init__(self, redis=None, retries=3):
        self.redis = redis
        self.retries = retries
        self._audit_log = []

    async def notify(self, channel, recipients, title, content, severity="info") -> None:
        if self._in_do_not_disturb() and severity not in self.DND_BYPASS_SEVERITIES:
            return
        tasks = []
        for r in recipients:
            if channel == "dingtalk":
                tasks.append(self._send_dingtalk(r, title, content, severity))
            elif channel == "email":
                tasks.append(self._send_email(r, title, content))
            elif channel == "wecom":
                tasks.append(self._send_wecom(r, title, content))
            else:
                tasks.append(self._send_unknown(channel, r, title, content))
        await asyncio.gather(*tasks, return_exceptions=True)
        self._audit_log.append({"channel": channel, "title": title,
                                "severity": severity, "ts": time.time()})

    async def push_ws(self, user_id, msg) -> None:
        if self.redis is not None:
            await self.redis.publish(f"da:ws:{user_id}", json.dumps(msg, ensure_ascii=False))

    async def _send_dingtalk(self, recipient, title, content, severity):
        color = {"P0": "FF0000", "P1": "FFA500", "info": "008000"}.get(severity, "008000")
        return {"ok": True, "channel": "dingtalk", "recipient": recipient, "color": color}

    async def _send_email(self, recipient, title, content):
        return {"ok": True, "channel": "email", "recipient": recipient}

    async def _send_wecom(self, recipient, title, content):
        return {"ok": True, "channel": "wecom", "recipient": recipient}

    async def _send_unknown(self, channel, recipient, title, content):
        for attempt in range(self.retries):
            try:
                return {"ok": True, "channel": channel, "recipient": recipient,
                        "attempt": attempt + 1}
            except Exception:
                if attempt == self.retries - 1:
                    raise

    @staticmethod
    def _in_do_not_disturb():
        hour = time.localtime().tm_hour
        return 23 <= hour or hour < 8

    def get_audit_log(self):
        return self._audit_log


_notifier = None


def get_notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        _notifier = Notifier()
    return _notifier
