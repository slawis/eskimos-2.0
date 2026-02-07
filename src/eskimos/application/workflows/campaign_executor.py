"""Campaign execution engine - processes running campaigns step by step."""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from eskimos.core.entities.campaign import Campaign, CampaignStatus, CampaignStep, ConditionType
from eskimos.core.entities.contact import Contact, ContactStatus, InterestLevel
from eskimos.core.services.campaign_service import CampaignService
from eskimos.core.services.contact_service import ContactService

logger = logging.getLogger(__name__)

# Type for modem send function: (phone, message) -> (success, error)
SendFn = Callable[[str, str], Awaitable[tuple[bool, str | None]]]


class CampaignExecutor:
    """Processes running campaigns. Call tick() periodically from daemon loop."""

    def __init__(
        self,
        campaign_service: CampaignService,
        contact_service: ContactService,
        send_fn: SendFn,
    ) -> None:
        self.campaigns = campaign_service
        self.contacts = contact_service
        self.send_fn = send_fn
        self._hourly_counts: dict[str, int] = {}  # campaign_id -> count this hour
        self._daily_counts: dict[str, int] = {}  # campaign_id -> count today
        self._last_hour_reset: datetime = datetime.utcnow()
        self._last_day_reset: datetime = datetime.utcnow()

    async def tick(self) -> int:
        """One execution cycle. Returns number of SMS sent."""
        self._reset_counters_if_needed()

        running = await self.campaigns.list_campaigns(status=CampaignStatus.RUNNING)
        if not running:
            return 0

        total_sent = 0
        for campaign in running:
            sent = await self._process_campaign(campaign)
            total_sent += sent

        return total_sent

    async def _process_campaign(self, campaign: Campaign) -> int:
        """Process one campaign. Returns SMS sent count."""
        if not campaign.schedule.is_within_time_window():
            return 0

        contacts = await self.contacts.contacts.get_active_for_campaign(campaign.id)
        if not contacts:
            # All contacts done or removed - complete campaign
            await self._check_completion(campaign)
            return 0

        sent = 0
        # Shuffle to distribute sends across contacts
        shuffled = list(contacts)
        random.shuffle(shuffled)

        for contact in shuffled:
            if self._rate_limit_reached(campaign):
                break

            result = await self._process_contact(campaign, contact)
            if result:
                sent += 1

        if sent > 0:
            # Update campaign stats
            campaign.sent_count += sent
            await self.campaigns.campaigns.update(campaign)
            logger.info(f"Campaign {campaign.id}: sent {sent} SMS this tick")

        return sent

    async def _process_contact(self, campaign: Campaign, contact: Contact) -> bool:
        """Process one contact in a campaign. Returns True if SMS sent."""
        step = self._get_next_step(campaign, contact)
        if not step:
            return False

        if not self._evaluate_condition(step, contact):
            return False

        if not self._delay_elapsed(step, contact):
            return False

        # Personalize message
        message = self._personalize(step.message_template, contact)

        # Apply jitter delay
        jitter = random.randint(
            campaign.schedule.min_delay_seconds,
            campaign.schedule.max_delay_seconds,
        )
        if jitter > 0:
            import asyncio
            await asyncio.sleep(jitter)

        # Send SMS
        success, error = await self.send_fn(contact.phone, message)

        if success:
            # Update contact progress
            contact.current_step = step.step_number
            contact.last_contact_at = datetime.utcnow()
            contact.total_sms_received += 1
            await self.contacts.contacts.update(contact)

            # Update rate counters
            cid = campaign.id
            self._hourly_counts[cid] = self._hourly_counts.get(cid, 0) + 1
            self._daily_counts[cid] = self._daily_counts.get(cid, 0) + 1

            logger.debug(f"SMS sent: campaign={campaign.id}, contact={contact.phone}, step={step.step_number}")
            return True
        else:
            logger.warning(f"SMS failed: campaign={campaign.id}, contact={contact.phone}, error={error}")
            return False

    def _get_next_step(self, campaign: Campaign, contact: Contact) -> CampaignStep | None:
        """Get the next step for this contact, or None if done."""
        current = contact.current_step
        for step in campaign.steps:
            if step.step_number > current:
                return step
        return None

    def _evaluate_condition(self, step: CampaignStep, contact: Contact) -> bool:
        """Check if step condition is met for this contact."""
        match step.condition_type:
            case ConditionType.ALWAYS:
                return True
            case ConditionType.IF_NO_REPLY:
                return contact.total_replies == 0
            case ConditionType.IF_POSITIVE:
                return (contact.sentiment_score or 0) > 0.3
            case ConditionType.IF_NEGATIVE:
                return (contact.sentiment_score or 0) < -0.3
            case ConditionType.IF_QUESTION:
                return contact.interest_level not in (InterestLevel.NONE, InterestLevel.UNKNOWN)
            case _:
                return True

    def _delay_elapsed(self, step: CampaignStep, contact: Contact) -> bool:
        """Check if enough time has passed since last contact."""
        if not contact.last_contact_at:
            return True  # First message, no delay needed

        delay_seconds = step.total_delay_seconds
        if delay_seconds <= 0:
            return True

        elapsed = (datetime.utcnow() - contact.last_contact_at).total_seconds()
        return elapsed >= delay_seconds

    def _personalize(self, template: str, contact: Contact) -> str:
        """Simple template personalization with contact data."""
        replacements = {
            "{imie}": contact.name or "",
            "{name}": contact.name or "",
            "{firma}": contact.company or "",
            "{company}": contact.company or "",
            "{stanowisko}": contact.position or "",
            "{position}": contact.position or "",
            "{telefon}": contact.phone,
            "{phone}": contact.phone,
            "{email}": contact.email or "",
        }

        # Add custom fields
        for key, value in contact.custom_fields.items():
            replacements[f"{{{key}}}"] = value

        result = template
        for placeholder, value in replacements.items():
            result = result.replace(placeholder, value)

        return result

    def _rate_limit_reached(self, campaign: Campaign) -> bool:
        """Check if campaign rate limits are hit."""
        cid = campaign.id
        hourly = self._hourly_counts.get(cid, 0)
        daily = self._daily_counts.get(cid, 0)

        if hourly >= campaign.schedule.max_sms_per_hour:
            return True
        if daily >= campaign.schedule.max_sms_per_day:
            return True
        return False

    def _reset_counters_if_needed(self) -> None:
        """Reset hourly/daily rate limit counters."""
        now = datetime.utcnow()

        if (now - self._last_hour_reset).total_seconds() >= 3600:
            self._hourly_counts.clear()
            self._last_hour_reset = now

        if (now - self._last_day_reset).total_seconds() >= 86400:
            self._daily_counts.clear()
            self._last_day_reset = now

    async def _check_completion(self, campaign: Campaign) -> None:
        """Check if campaign should be marked as completed."""
        contacts = await self.contacts.contacts.list(campaign_id=campaign.id)
        if not contacts:
            return

        total_steps = len(campaign.steps)
        all_done = all(c.current_step >= total_steps for c in contacts)

        if all_done:
            campaign.status = CampaignStatus.COMPLETED
            campaign.completed_at = datetime.utcnow()
            await self.campaigns.campaigns.update(campaign)
            logger.info(f"Campaign completed: {campaign.id} '{campaign.name}'")
