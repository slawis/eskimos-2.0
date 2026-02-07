"""Repository interfaces and implementations."""

from eskimos.core.repositories.memory import (
    InMemoryBlacklistRepository,
    InMemoryCampaignRepository,
    InMemoryContactRepository,
)

__all__ = [
    "InMemoryCampaignRepository",
    "InMemoryContactRepository",
    "InMemoryBlacklistRepository",
]
