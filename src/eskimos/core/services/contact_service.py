"""Contact service - CRUD, bulk import, blacklist management."""

from __future__ import annotations

import csv
import io
import logging
import re

from eskimos.core.entities.contact import (
    Blacklist,
    BlacklistReason,
    Contact,
    ContactStatus,
    is_stop_message,
)
from eskimos.core.repositories.memory import InMemoryBlacklistRepository, InMemoryContactRepository

logger = logging.getLogger(__name__)


class ContactService:
    """Manages contacts and blacklist."""

    def __init__(
        self,
        contact_repo: InMemoryContactRepository,
        blacklist_repo: InMemoryBlacklistRepository,
    ) -> None:
        self.contacts = contact_repo
        self.blacklist = blacklist_repo

    # ==================== CRUD ====================

    async def create_contact(
        self,
        phone: str,
        name: str | None = None,
        company: str | None = None,
        position: str | None = None,
        email: str | None = None,
        source: str | None = None,
        tags: list[str] | None = None,
    ) -> Contact:
        # Check blacklist
        if await self.blacklist.is_blacklisted(phone):
            raise ValueError(f"Phone {phone} is blacklisted")

        # Check duplicate
        existing = await self.contacts.get_by_phone(phone)
        if existing:
            raise ValueError(f"Contact with phone {phone} already exists: {existing.id}")

        contact = Contact(
            phone=phone,
            name=name,
            company=company,
            position=position,
            email=email,
            source=source,
            tags=tags or [],
        )
        await self.contacts.create(contact)
        logger.info(f"Contact created: {contact.id} ({phone})")
        return contact

    async def get_contact(self, id: str) -> Contact | None:
        return await self.contacts.get(id)

    async def list_contacts(
        self,
        campaign_id: str | None = None,
        status: ContactStatus | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Contact]:
        return await self.contacts.list(
            skip=skip, limit=limit,
            campaign_id=campaign_id, status=status,
        )

    async def update_contact(self, id: str, **fields) -> Contact | None:
        contact = await self.contacts.get(id)
        if not contact:
            return None

        for key, value in fields.items():
            if hasattr(contact, key) and key not in ("id", "phone", "created_at"):
                setattr(contact, key, value)

        await self.contacts.update(contact)
        logger.info(f"Contact updated: {id}")
        return contact

    async def delete_contact(self, id: str) -> bool:
        result = await self.contacts.delete(id)
        if result:
            logger.info(f"Contact deleted: {id}")
        return result

    # ==================== Bulk ====================

    async def bulk_create(self, contacts_data: list[dict]) -> tuple[int, int]:
        """Create contacts in bulk. Returns (created, skipped)."""
        contacts = []
        skipped = 0

        for data in contacts_data:
            phone = data.get("phone", "")
            # Normalize phone
            cleaned = re.sub(r"[^\d]", "", str(phone))
            if cleaned.startswith("48") and len(cleaned) == 11:
                cleaned = cleaned[2:]
            if len(cleaned) != 9:
                skipped += 1
                continue

            if await self.blacklist.is_blacklisted(cleaned):
                skipped += 1
                continue

            contacts.append(Contact(
                phone=cleaned,
                name=data.get("name"),
                company=data.get("company"),
                position=data.get("position"),
                email=data.get("email"),
                source=data.get("source", "bulk_import"),
                tags=data.get("tags", []),
            ))

        created = await self.contacts.bulk_create(contacts)
        skipped += len(contacts) - created  # duplicates
        logger.info(f"Bulk import: {created} created, {skipped} skipped")
        return created, skipped

    async def import_csv(self, csv_data: str) -> tuple[int, int]:
        """Import contacts from CSV string. Expected columns: phone, name, company, email.
        Returns (created, skipped)."""
        reader = csv.DictReader(io.StringIO(csv_data))
        contacts_data = []

        for row in reader:
            contacts_data.append({
                "phone": row.get("phone", row.get("telefon", "")),
                "name": row.get("name", row.get("imie", row.get("nazwa", ""))),
                "company": row.get("company", row.get("firma", "")),
                "email": row.get("email", row.get("e-mail", "")),
                "source": "csv_import",
            })

        return await self.bulk_create(contacts_data)

    # ==================== Blacklist ====================

    async def blacklist_contact(
        self,
        phone: str,
        reason: BlacklistReason = BlacklistReason.MANUAL,
        reason_detail: str | None = None,
        source_campaign_id: str | None = None,
    ) -> Blacklist:
        # Normalize phone
        cleaned = re.sub(r"[^\d]", "", str(phone))
        if cleaned.startswith("48") and len(cleaned) == 11:
            cleaned = cleaned[2:]

        # Mark contact as blacklisted if exists
        contact = await self.contacts.get_by_phone(cleaned)
        if contact:
            contact.status = ContactStatus.BLACKLISTED
            contact.current_campaign_id = None
            await self.contacts.update(contact)

        entry = Blacklist(
            phone=cleaned,
            reason=reason,
            reason_detail=reason_detail,
            source_campaign_id=source_campaign_id,
        )
        await self.blacklist.create(entry)
        logger.info(f"Phone blacklisted: {cleaned} ({reason.value})")
        return entry

    async def is_blacklisted(self, phone: str) -> bool:
        return await self.blacklist.is_blacklisted(phone)

    async def handle_stop_message(
        self,
        phone: str,
        message: str,
        campaign_id: str | None = None,
    ) -> bool:
        """Process incoming message for STOP keywords. Returns True if STOP detected."""
        if not is_stop_message(message):
            return False

        await self.blacklist_contact(
            phone=phone,
            reason=BlacklistReason.KEYWORD_STOP,
            reason_detail=message[:100],
            source_campaign_id=campaign_id,
        )
        return True
