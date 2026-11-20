from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERN = re.compile(
    r"(?P<company>[^/@#\s]+)/(?P<device>[^/@#\s]+)"
    r"@(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)#(?P<path>.+)"
)


@dataclass(frozen=True)
class EvidenceRef:
    """Pointer to one observation: `<company>/<device>@<collected_at>#<path>`."""

    company_id: str
    device_id: str
    collected_at: str
    path: str

    def format(self) -> str:
        return f"{self.company_id}/{self.device_id}@{self.collected_at}#{self.path}"

    def __str__(self) -> str:
        return self.format()

    @classmethod
    def parse(cls, text: str) -> EvidenceRef:
        match = _PATTERN.fullmatch(text)
        if match is None:
            raise ValueError("malformed evidence reference")
        return cls(match["company"], match["device"], match["ts"], match["path"])
