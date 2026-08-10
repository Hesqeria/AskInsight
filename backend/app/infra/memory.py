"""P3-04: Memory & context manager - session memory + user history."""
import json
import time
from typing import Optional


class MemoryManager:
    def __init__(self):
        self._sessions: dict[str, dict] = {}  # session_id -> {key: (value, expiry)}
        self._prefs: dict[str, dict] = {}     # user_id -> {preferences}

    def set(self, session_id: str, key: str, value, ttl: int = 3600):
        if session_id not in self._sessions:
            self._sessions[session_id] = {}
        self._sessions[session_id][key] = (json.dumps(value, ensure_ascii=False), time.time() + ttl)

    def get(self, session_id: str, key: str) -> Optional[str]:
        session = self._sessions.get(session_id, {})
        entry = session.get(key)
        if not entry:
            return None
        val, expiry = entry
        if time.time() > expiry:
            del session[key]
            return None
        return json.loads(val)

    def get_history(self, session_id: str, limit: int = 10) -> list:
        messages = self.get(session_id, "messages") or []
        if isinstance(messages, str):
            messages = json.loads(messages)
        return messages[-limit:]

    def add_message(self, session_id: str, role: str, content: str):
        messages = self.get(session_id, "messages") or []
        if isinstance(messages, str):
            messages = json.loads(messages)
        messages.append({"role": role, "content": content})
        self.set(session_id, "messages", messages)

    def set_preference(self, user_id: str, prefs: dict):
        if user_id not in self._prefs:
            self._prefs[user_id] = {}
        self._prefs[user_id].update(prefs)

    def get_preferences(self, user_id: str) -> dict:
        return self._prefs.get(user_id, {})

    def clear_session(self, session_id: str):
        self._sessions.pop(session_id, None)


_memory = None

def get_memory() -> MemoryManager:
    global _memory
    if _memory is None:
        _memory = MemoryManager()
    return _memory
