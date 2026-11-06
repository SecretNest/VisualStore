"""Messages crossing the server ↔ compute-child boundary. Picklable, no database handles."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ComputeRequest:
    job_id: str  # UUID string; children never see bytes ids
    profile: str
    type: str
    params: dict


@dataclass(frozen=True)
class ComputeMessage:
    job_id: str
    profile: str
    kind: str  # "started" | "progress" | "result" | "error"
    payload: dict
