"""Core business logic layer.

This module contains domain logic for:
- Consent URL handling
"""
from .consent import build_consent_url, parse_webhook_payload

__all__ = [
    "build_consent_url",
    "parse_webhook_payload",
]
