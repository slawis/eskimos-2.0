"""AI adapters for Eskimos 2.0.

This module provides AI-powered features:
- SMS personalization
- Auto-reply generation
- Sentiment analysis
"""

from eskimos.adapters.ai.base import AIAdapter, PersonalizedSMS, AutoReplyResult
from eskimos.adapters.ai.claude import ClaudeAdapter

__all__ = [
    "AIAdapter",
    "PersonalizedSMS",
    "AutoReplyResult",
    "ClaudeAdapter",
]
