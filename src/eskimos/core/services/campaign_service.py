"""Campaign service - CRUD and lifecycle management."""

from __future__ import annotations

import logging
from datetime import datetime

from eskimos.core.entities.campaign import Campaign, CampaignSchedule, CampaignStatus, CampaignStep
from eskimos.core.entities.contact import Contact, ContactStatus
from eskimos.core.repositories.memory import InMemoryCampaignRepository, InMemoryContactRepository

logger = logging.getLogger(__name__)


class CampaignService:
    """Manages campaign lifecycle: create, start, pause, cancel, stats."""

    def __init__(
        self,
        campaign_repo: InMemoryCampaignRepository,
        contact_repo: InMemoryContactRepository,
    ) -> None:
        self.campaigns = campaign_repo
        self.contacts = contact_repo

    # ==================== CRUD ====================

    async def create_campaign(
        self,
        name: str,
        user_id: str,
        schedule: CampaignSchedule,
        steps: list[CampaignStep] | None = None,
        description: str | None = None,
        enable_ai_replies: bool = False,
        ai_reply_prompt: str | None = None,
    ) -> Campaign:
        campaign = Campaign(
            name=name,
            user_id=user_id,
            schedule=schedule,
            steps=steps or [],
            description=description,
            enable_ai_replies=enable_ai_replies,
            ai_reply_prompt=ai_reply_prompt,
        )
        await self.campaigns.create(campaign)
        logger.info(f"Campaign created: {campaign.id} '{name}'")
        return campaign

    async def get_campaign(self, id: str) -> Campaign | None:
        return await self.campaigns.get(id)

    async def list_campaigns(
        self,
        status: CampaignStatus | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Campaign]:
        return await self.campaigns.list(skip=skip, limit=limit, status=status)

    async def update_campaign(self, id: str, **fields) -> Campaign | None:
        campaign = await self.campaigns.get(id)
        if not campaign:
            return None

        if campaign.status not in (CampaignStatus.DRAFT, CampaignStatus.PAUSED):
            raise ValueError(f"Cannot edit campaign in status: {campaign.status}")

        for key, value in fields.items():
            if hasattr(campaign, key):
                setattr(campaign, key, value)

        await self.campaigns.update(campaign)
        logger.info(f"Campaign updated: {id}")
        return campaign

    async def delete_campaign(self, id: str) -> bool:
        campaign = await self.campaigns.get(id)
        if not campaign:
            return False

        if campaign.status in (CampaignStatus.RUNNING, CampaignStatus.SCHEDULED):
            raise ValueError(f"Cannot delete active campaign. Cancel it first.")

        # Unassign contacts
        contacts = await self.contacts.get_active_for_campaign(id)
        for contact in contacts:
            contact.current_campaign_id = None
            contact.current_step = 0
            await self.contacts.update(contact)

        result = await self.campaigns.delete(id)
        logger.info(f"Campaign deleted: {id}")
        return result

    # ==================== Lifecycle ====================

    async def start_campaign(self, id: str) -> Campaign:
        campaign = await self.campaigns.get(id)
        if not campaign:
            raise ValueError(f"Campaign not found: {id}")

        if campaign.status not in (CampaignStatus.DRAFT, CampaignStatus.PAUSED):
            raise ValueError(f"Cannot start campaign in status: {campaign.status}")

        if not campaign.steps:
            raise ValueError("Campaign has no steps defined")

        contacts = await self.contacts.get_active_for_campaign(id)
        if not contacts:
            raise ValueError("Campaign has no contacts assigned")

        campaign.status = CampaignStatus.RUNNING
        campaign.started_at = campaign.started_at or datetime.utcnow()
        campaign.total_contacts = len(contacts)
        await self.campaigns.update(campaign)
        logger.info(f"Campaign started: {id} ({len(contacts)} contacts)")
        return campaign

    async def pause_campaign(self, id: str) -> Campaign:
        campaign = await self.campaigns.get(id)
        if not campaign:
            raise ValueError(f"Campaign not found: {id}")

        if campaign.status != CampaignStatus.RUNNING:
            raise ValueError(f"Can only pause running campaigns, current: {campaign.status}")

        campaign.status = CampaignStatus.PAUSED
        await self.campaigns.update(campaign)
        logger.info(f"Campaign paused: {id}")
        return campaign

    async def cancel_campaign(self, id: str) -> Campaign:
        campaign = await self.campaigns.get(id)
        if not campaign:
            raise ValueError(f"Campaign not found: {id}")

        if campaign.status == CampaignStatus.COMPLETED:
            raise ValueError("Cannot cancel completed campaign")

        campaign.status = CampaignStatus.CANCELLED
        campaign.completed_at = datetime.utcnow()
        await self.campaigns.update(campaign)
        logger.info(f"Campaign cancelled: {id}")
        return campaign

    # ==================== Contacts ====================

    async def add_contacts_to_campaign(
        self,
        campaign_id: str,
        contacts: list[Contact],
    ) -> int:
        campaign = await self.campaigns.get(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign not found: {campaign_id}")

        added = 0
        for contact in contacts:
            if contact.status != ContactStatus.ACTIVE:
                continue
            contact.current_campaign_id = campaign_id
            contact.current_step = 0
            await self.contacts.update(contact)
            added += 1

        campaign.total_contacts = len(
            await self.contacts.get_active_for_campaign(campaign_id)
        )
        await self.campaigns.update(campaign)
        logger.info(f"Added {added} contacts to campaign {campaign_id}")
        return added

    # ==================== Stats ====================

    async def get_campaign_stats(self, id: str) -> dict | None:
        campaign = await self.campaigns.get(id)
        if not campaign:
            return None

        contacts = await self.contacts.get_active_for_campaign(id)
        total_steps = len(campaign.steps)

        completed_contacts = sum(
            1 for c in contacts
            if c.current_step >= total_steps
        )

        return {
            "id": campaign.id,
            "name": campaign.name,
            "status": campaign.status.value,
            "total_contacts": campaign.total_contacts,
            "active_contacts": len(contacts),
            "completed_contacts": completed_contacts,
            "total_steps": total_steps,
            "sent_count": campaign.sent_count,
            "delivered_count": campaign.delivered_count,
            "reply_count": campaign.reply_count,
            "unsubscribe_count": campaign.unsubscribe_count,
            "delivery_rate": campaign.delivery_rate,
            "reply_rate": campaign.reply_rate,
            "started_at": campaign.started_at,
            "completed_at": campaign.completed_at,
        }
