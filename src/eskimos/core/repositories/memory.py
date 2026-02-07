"""In-memory repository implementations with JSON file persistence."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from eskimos.core.entities.campaign import Campaign, CampaignStatus
from eskimos.core.entities.contact import Blacklist, Contact, ContactStatus

logger = logging.getLogger(__name__)

# Default persistence directory
DATA_DIR = Path.cwd() / "data"


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


class InMemoryCampaignRepository:
    """In-memory campaign storage with JSON file persistence."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir or DATA_DIR
        self._campaigns: dict[str, Campaign] = {}
        self._load()

    def _file_path(self) -> Path:
        return self._data_dir / "campaigns.json"

    def _load(self) -> None:
        path = self._file_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for item in data:
                    campaign = Campaign.model_validate(item)
                    self._campaigns[campaign.id] = campaign
                logger.info(f"Loaded {len(self._campaigns)} campaigns from {path}")
            except Exception as e:
                logger.warning(f"Failed to load campaigns: {e}")

    def _save(self) -> None:
        _ensure_data_dir()
        path = self._file_path()
        data = [c.model_dump(mode="json") for c in self._campaigns.values()]
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    async def get(self, id: str) -> Campaign | None:
        return self._campaigns.get(id)

    async def list(self, skip: int = 0, limit: int = 100,
                   status: CampaignStatus | None = None) -> list[Campaign]:
        items = list(self._campaigns.values())
        if status:
            items = [c for c in items if c.status == status]
        items.sort(key=lambda c: c.created_at, reverse=True)
        return items[skip:skip + limit]

    async def create(self, campaign: Campaign) -> Campaign:
        self._campaigns[campaign.id] = campaign
        self._save()
        return campaign

    async def update(self, campaign: Campaign) -> Campaign:
        campaign.updated_at = datetime.utcnow()
        self._campaigns[campaign.id] = campaign
        self._save()
        return campaign

    async def delete(self, id: str) -> bool:
        if id in self._campaigns:
            del self._campaigns[id]
            self._save()
            return True
        return False

    async def count(self, status: CampaignStatus | None = None) -> int:
        if status:
            return sum(1 for c in self._campaigns.values() if c.status == status)
        return len(self._campaigns)

    async def get_by_status(self, status: CampaignStatus) -> list[Campaign]:
        return [c for c in self._campaigns.values() if c.status == status]


class InMemoryContactRepository:
    """In-memory contact storage with JSON file persistence."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir or DATA_DIR
        self._contacts: dict[str, Contact] = {}
        self._load()

    def _file_path(self) -> Path:
        return self._data_dir / "contacts.json"

    def _load(self) -> None:
        path = self._file_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for item in data:
                    contact = Contact.model_validate(item)
                    self._contacts[contact.id] = contact
                logger.info(f"Loaded {len(self._contacts)} contacts from {path}")
            except Exception as e:
                logger.warning(f"Failed to load contacts: {e}")

    def _save(self) -> None:
        _ensure_data_dir()
        path = self._file_path()
        data = [c.model_dump(mode="json") for c in self._contacts.values()]
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    async def get(self, id: str) -> Contact | None:
        return self._contacts.get(id)

    async def list(self, skip: int = 0, limit: int = 100,
                   campaign_id: str | None = None,
                   status: ContactStatus | None = None) -> list[Contact]:
        items = list(self._contacts.values())
        if campaign_id:
            items = [c for c in items if c.current_campaign_id == campaign_id]
        if status:
            items = [c for c in items if c.status == status]
        items.sort(key=lambda c: c.created_at, reverse=True)
        return items[skip:skip + limit]

    async def create(self, contact: Contact) -> Contact:
        self._contacts[contact.id] = contact
        self._save()
        return contact

    async def update(self, contact: Contact) -> Contact:
        contact.updated_at = datetime.utcnow()
        self._contacts[contact.id] = contact
        self._save()
        return contact

    async def delete(self, id: str) -> bool:
        if id in self._contacts:
            del self._contacts[id]
            self._save()
            return True
        return False

    async def count(self, campaign_id: str | None = None,
                    status: ContactStatus | None = None) -> int:
        items = list(self._contacts.values())
        if campaign_id:
            items = [c for c in items if c.current_campaign_id == campaign_id]
        if status:
            items = [c for c in items if c.status == status]
        return len(items)

    async def get_by_phone(self, phone: str) -> Contact | None:
        for c in self._contacts.values():
            if c.phone == phone:
                return c
        return None

    async def get_active_for_campaign(self, campaign_id: str) -> list[Contact]:
        return [
            c for c in self._contacts.values()
            if c.current_campaign_id == campaign_id
            and c.status == ContactStatus.ACTIVE
        ]

    async def bulk_create(self, contacts: list[Contact]) -> int:
        created = 0
        for contact in contacts:
            existing = await self.get_by_phone(contact.phone)
            if not existing:
                self._contacts[contact.id] = contact
                created += 1
        self._save()
        return created


class InMemoryBlacklistRepository:
    """In-memory blacklist storage with JSON file persistence."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir or DATA_DIR
        self._entries: dict[str, Blacklist] = {}
        self._load()

    def _file_path(self) -> Path:
        return self._data_dir / "blacklist.json"

    def _load(self) -> None:
        path = self._file_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for item in data:
                    entry = Blacklist.model_validate(item)
                    self._entries[entry.id] = entry
                logger.info(f"Loaded {len(self._entries)} blacklist entries from {path}")
            except Exception as e:
                logger.warning(f"Failed to load blacklist: {e}")

    def _save(self) -> None:
        _ensure_data_dir()
        path = self._file_path()
        data = [e.model_dump(mode="json") for e in self._entries.values()]
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    async def get(self, id: str) -> Blacklist | None:
        return self._entries.get(id)

    async def get_by_phone(self, phone: str) -> Blacklist | None:
        for e in self._entries.values():
            if e.phone == phone:
                return e
        return None

    async def create(self, entry: Blacklist) -> Blacklist:
        self._entries[entry.id] = entry
        self._save()
        return entry

    async def delete(self, id: str) -> bool:
        if id in self._entries:
            del self._entries[id]
            self._save()
            return True
        return False

    async def is_blacklisted(self, phone: str) -> bool:
        return any(e.phone == phone for e in self._entries.values())

    async def list_all(self) -> list[Blacklist]:
        return list(self._entries.values())
