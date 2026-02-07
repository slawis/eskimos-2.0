"""Dependency injection for FastAPI - singleton services."""

from __future__ import annotations

from eskimos.core.repositories.memory import (
    InMemoryBlacklistRepository,
    InMemoryCampaignRepository,
    InMemoryContactRepository,
)
from eskimos.core.services.campaign_service import CampaignService
from eskimos.core.services.contact_service import ContactService

# Singleton instances (initialized on first access)
_campaign_repo: InMemoryCampaignRepository | None = None
_contact_repo: InMemoryContactRepository | None = None
_blacklist_repo: InMemoryBlacklistRepository | None = None
_campaign_service: CampaignService | None = None
_contact_service: ContactService | None = None


def get_campaign_repo() -> InMemoryCampaignRepository:
    global _campaign_repo
    if _campaign_repo is None:
        _campaign_repo = InMemoryCampaignRepository()
    return _campaign_repo


def get_contact_repo() -> InMemoryContactRepository:
    global _contact_repo
    if _contact_repo is None:
        _contact_repo = InMemoryContactRepository()
    return _contact_repo


def get_blacklist_repo() -> InMemoryBlacklistRepository:
    global _blacklist_repo
    if _blacklist_repo is None:
        _blacklist_repo = InMemoryBlacklistRepository()
    return _blacklist_repo


def get_campaign_service() -> CampaignService:
    global _campaign_service
    if _campaign_service is None:
        _campaign_service = CampaignService(
            campaign_repo=get_campaign_repo(),
            contact_repo=get_contact_repo(),
        )
    return _campaign_service


def get_contact_service() -> ContactService:
    global _contact_service
    if _contact_service is None:
        _contact_service = ContactService(
            contact_repo=get_contact_repo(),
            blacklist_repo=get_blacklist_repo(),
        )
    return _contact_service
