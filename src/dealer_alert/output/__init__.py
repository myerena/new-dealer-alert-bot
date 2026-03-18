"""Digest and output layer — CSV, JSON, and scoring."""

from .digest import DigestGenerator
from .scoring import score_lead

__all__ = ["DigestGenerator", "score_lead"]
