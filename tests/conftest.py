"""Pytest configuration and fixtures for Eskimos 2.0 tests."""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Generator

import pytest

from eskimos.adapters.modem.mock import MockModemAdapter, MockModemConfig
from eskimos.core.entities.sms import SMS, SMSDirection, SMSStatus
from eskimos.core.entities.campaign import Campaign, CampaignSchedule, CampaignStep
from eskimos.core.entities.contact import Contact
from eskimos.core.entities.modem import Modem, ModemType


# ==================== Event Loop ====================

@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ==================== Modem Fixtures ====================

@pytest.fixture
def mock_modem_config() -> MockModemConfig:
    """Create mock modem configuration."""
    return MockModemConfig(
        phone_number="886480453",
        success_rate=1.0,
        min_send_delay_ms=0,
        max_send_delay_ms=10,
    )


@pytest.fixture
async def mock_modem(mock_modem_config: MockModemConfig) -> AsyncGenerator[MockModemAdapter, None]:
    """Create and connect mock modem adapter."""
    adapter = MockModemAdapter(mock_modem_config)
    await adapter.connect()
    yield adapter
    await adapter.disconnect()


@pytest.fixture
def failing_modem_config() -> MockModemConfig:
    """Create mock modem that always fails."""
    return MockModemConfig(
        phone_number="886480453",
        success_rate=0.0,
    )


# ==================== Entity Fixtures ====================

@pytest.fixture
def sample_sms() -> SMS:
    """Create sample outbound SMS."""
    return SMS(
        direction=SMSDirection.OUTBOUND,
        status=SMSStatus.PENDING,
        sender="886480453",
        recipient="123456789",
        content="Test message from Eskimos 2.0",
    )


@pytest.fixture
def sample_contact() -> Contact:
    """Create sample contact."""
    return Contact(
        phone="123456789",
        name="Jan Kowalski",
        company="Test Company",
    )


@pytest.fixture
def sample_modem() -> Modem:
    """Create sample modem entity."""
    return Modem(
        phone_number="886480453",
        modem_type=ModemType.MOCK,
        name="Test Modem",
        max_sms_per_hour=30,
    )


@pytest.fixture
def sample_campaign_schedule() -> CampaignSchedule:
    """Create sample campaign schedule."""
    from datetime import datetime
    return CampaignSchedule(
        start_date=datetime.now(),
        max_sms_per_hour=60,
        max_sms_per_day=500,
    )


@pytest.fixture
def sample_campaign(sample_campaign_schedule: CampaignSchedule) -> Campaign:
    """Create sample campaign."""
    return Campaign(
        user_id="user_123",
        name="Test Campaign",
        schedule=sample_campaign_schedule,
        steps=[
            CampaignStep(
                step_number=1,
                message_template="Cześć {name}! Czy interesuje Cię współpraca?",
            ),
            CampaignStep(
                step_number=2,
                message_template="Przypominam o naszej ofercie...",
                delay_days=1,
            ),
        ],
    )


# ==================== Test Helpers ====================

def assert_phone_valid(phone: str) -> None:
    """Assert phone number is valid (9 digits)."""
    assert len(phone) == 9
    assert phone.isdigit()
