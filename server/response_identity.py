"""Session-local response identity, independent of transcript rendering order."""
from uuid import uuid4


class ResponseIdentity:
    def __init__(self, session_id=None):
        self.session_id = session_id or str(uuid4())
        self.current = None
        self.completed = None

    def begin(self):
        if self.current is None:
            self.current = str(uuid4())
        return self.current

    def finish(self):
        self.completed = self.begin()
        self.current = None
        return self.completed

    def stamp(self, payload, *, response_id=None):
        result = dict(payload)
        identity = response_id or self.current
        result["session_id"] = self.session_id
        if identity:
            result["response_id"] = identity
            # Usage is finalized once per provider response. Re-delivery keeps
            # this identity, rather than looking like another billable request,
            # while genuine token revisions on the same response increment :r{rev}.
            usage_obj = result.get("usage") if isinstance(result.get("usage"), dict) else {}
            rev = usage_obj.get("revision", 0)
            if result.get("type") == "usage":
                suffix = f"usage:r{rev}" if isinstance(rev, int) and rev > 0 else "usage"
            else:
                suffix = str(uuid4())
            result["event_id"] = f"{self.session_id}:{identity}:{suffix}"
        return result
