from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class PolicyDecision:
    allowed: bool
    reason: Optional[str] = None


def validate_submit_order(
    environment: str, approval_token: Optional[str] = None
) -> PolicyDecision:
    # R&D guardrails: only allow demo unless explicit approval provided
    if environment != "demo":
        return PolicyDecision(
            allowed=False, reason="non-demo environment blocked in R&D"
        )
    # Optional: require approval token later
    return PolicyDecision(allowed=True)
