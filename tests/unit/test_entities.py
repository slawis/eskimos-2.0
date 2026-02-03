"""Unit tests for core entities."""

from datetime import datetime, time

import pytest

from eskimos.core.entities.sms import SMS, SMSDirection, SMSStatus, generate_key
from eskimos.core.entities.campaign import (
    Campaign,
    CampaignSchedule,
    CampaignStep,
    CampaignStatus,
)
from eskimos.core.entities.contact import (
    Contact,
    ContactStatus,
    Blacklist,
    BlacklistReason,
    is_stop_message,
)
from eskimos.core.entities.modem import Modem, ModemType, ModemStatus


class TestGenerateKey:
    """Tests for key generation."""

    def test_generate_key_default_length(self):
        """Test default key length is 20."""
        key = generate_key()
        assert len(key) == 20

    def test_generate_key_custom_length(self):
        """Test custom key length."""
        key = generate_key(10)
        assert len(key) == 10

    def test_generate_key_alphanumeric(self):
        """Test key contains only lowercase alphanumeric."""
        key = generate_key(100)
        assert key.isalnum()
        assert key.islower() or key.isdigit()


class TestSMS:
    """Tests for SMS entity."""

    def test_create_outbound_sms(self):
        """Test creating outbound SMS."""
        sms = SMS(
            direction=SMSDirection.OUTBOUND,
            sender="886480453",
            recipient="123456789",
            content="Test message",
        )

        assert sms.direction == SMSDirection.OUTBOUND
        assert sms.status == SMSStatus.PENDING
        assert sms.sender == "886480453"
        assert sms.recipient == "123456789"
        assert sms.content == "Test message"
        assert sms.id.startswith("sms_")

    def test_phone_normalization_with_country_code(self):
        """Test phone normalization removes +48 prefix."""
        sms = SMS(
            direction=SMSDirection.OUTBOUND,
            sender="48886480453",
            recipient="+48123456789",
            content="Test",
        )

        assert sms.sender == "886480453"
        assert sms.recipient == "123456789"

    def test_phone_validation_invalid_length(self):
        """Test validation fails for invalid phone length."""
        with pytest.raises(ValueError, match="Invalid phone number"):
            SMS(
                direction=SMSDirection.OUTBOUND,
                sender="12345",  # Too short
                recipient="123456789",
                content="Test",
            )

    def test_sms_max_content_length(self):
        """Test SMS content max length (640 chars = 4 SMS)."""
        # Should work with 640 chars
        sms = SMS(
            direction=SMSDirection.OUTBOUND,
            sender="886480453",
            recipient="123456789",
            content="x" * 640,
        )
        assert len(sms.content) == 640

    def test_sms_content_too_long(self):
        """Test SMS content over max length fails."""
        with pytest.raises(ValueError):
            SMS(
                direction=SMSDirection.OUTBOUND,
                sender="886480453",
                recipient="123456789",
                content="x" * 641,
            )


class TestCampaign:
    """Tests for Campaign entity."""

    def test_create_campaign(self, sample_campaign_schedule):
        """Test creating campaign."""
        campaign = Campaign(
            user_id="user_123",
            name="Test Campaign",
            schedule=sample_campaign_schedule,
        )

        assert campaign.status == CampaignStatus.DRAFT
        assert campaign.id.startswith("camp_")
        assert campaign.sent_count == 0

    def test_campaign_delivery_rate(self, sample_campaign_schedule):
        """Test delivery rate calculation."""
        campaign = Campaign(
            user_id="user_123",
            name="Test",
            schedule=sample_campaign_schedule,
            sent_count=100,
            delivered_count=85,
        )

        assert campaign.delivery_rate == 85.0

    def test_campaign_delivery_rate_zero_sent(self, sample_campaign_schedule):
        """Test delivery rate is 0 when nothing sent."""
        campaign = Campaign(
            user_id="user_123",
            name="Test",
            schedule=sample_campaign_schedule,
        )

        assert campaign.delivery_rate == 0.0

    def test_campaign_step_delay(self):
        """Test campaign step delay calculation."""
        step = CampaignStep(
            step_number=2,
            message_template="Follow up",
            delay_hours=12,
            delay_days=1,
        )

        # 1 day + 12 hours = 36 hours = 129600 seconds
        assert step.total_delay_seconds == 129600


class TestCampaignSchedule:
    """Tests for CampaignSchedule."""

    def test_is_within_time_window_valid(self):
        """Test time within window."""
        schedule = CampaignSchedule(
            start_date=datetime(2026, 1, 1),
            send_time_start=time(9, 0),
            send_time_end=time(20, 0),
            allowed_days=[0, 1, 2, 3, 4],  # Mon-Fri
        )

        # Monday at 10:00
        test_time = datetime(2026, 2, 2, 10, 0)  # Monday
        assert schedule.is_within_time_window(test_time)

    def test_is_within_time_window_outside_hours(self):
        """Test time outside hours."""
        schedule = CampaignSchedule(
            start_date=datetime(2026, 1, 1),
            send_time_start=time(9, 0),
            send_time_end=time(20, 0),
        )

        # 8:00 is before window
        test_time = datetime(2026, 2, 2, 8, 0)
        assert not schedule.is_within_time_window(test_time)

    def test_is_within_time_window_weekend(self):
        """Test weekend is excluded by default."""
        schedule = CampaignSchedule(
            start_date=datetime(2026, 1, 1),
            allowed_days=[0, 1, 2, 3, 4],  # Mon-Fri only
        )

        # Saturday at 10:00
        test_time = datetime(2026, 2, 7, 10, 0)  # Saturday
        assert not schedule.is_within_time_window(test_time)


class TestContact:
    """Tests for Contact entity."""

    def test_create_contact(self):
        """Test creating contact."""
        contact = Contact(
            phone="123456789",
            name="Jan Kowalski",
        )

        assert contact.phone == "123456789"
        assert contact.name == "Jan Kowalski"
        assert contact.status == ContactStatus.ACTIVE
        assert contact.can_receive_sms

    def test_contact_display_name_with_name(self):
        """Test display name returns name when set."""
        contact = Contact(phone="123456789", name="Jan")
        assert contact.display_name == "Jan"

    def test_contact_display_name_without_name(self):
        """Test display name returns phone when no name."""
        contact = Contact(phone="123456789")
        assert contact.display_name == "123456789"

    def test_blacklisted_contact_cannot_receive(self):
        """Test blacklisted contact cannot receive SMS."""
        contact = Contact(
            phone="123456789",
            status=ContactStatus.BLACKLISTED,
        )

        assert not contact.can_receive_sms


class TestBlacklist:
    """Tests for Blacklist entity."""

    def test_create_blacklist_entry(self):
        """Test creating blacklist entry."""
        entry = Blacklist(
            phone="123456789",
            reason=BlacklistReason.KEYWORD_STOP,
        )

        assert entry.phone == "123456789"
        assert entry.reason == BlacklistReason.KEYWORD_STOP
        assert entry.id.startswith("bl_")


class TestStopKeywords:
    """Tests for STOP keyword detection."""

    @pytest.mark.parametrize("message", [
        "STOP",
        "stop",
        "Stop",
        "koniec",
        "KONIEC",
        "nie dzwon",
        "NIE DZWON",
        "wypisz",
        "rezygnuje",
        "rezygnuję",
    ])
    def test_is_stop_message_exact_match(self, message):
        """Test STOP keywords are detected."""
        assert is_stop_message(message)

    @pytest.mark.parametrize("message", [
        "STOP prosze",
        "Proszę o STOP",
        "nie dzwon więcej",
    ])
    def test_is_stop_message_contains(self, message):
        """Test STOP keywords detected in longer messages."""
        assert is_stop_message(message)

    @pytest.mark.parametrize("message", [
        "Dziękuję za informację",
        "Jestem zainteresowany",
        "Kiedy można zadzwonić?",
    ])
    def test_is_stop_message_false(self, message):
        """Test non-STOP messages are not detected."""
        assert not is_stop_message(message)


class TestModem:
    """Tests for Modem entity."""

    def test_create_modem(self):
        """Test creating modem."""
        modem = Modem(
            phone_number="886480453",
            modem_type=ModemType.MOCK,
            name="Test Modem",
        )

        assert modem.phone_number == "886480453"
        assert modem.modem_type == ModemType.MOCK
        assert modem.status == ModemStatus.OFFLINE

    def test_modem_display_name(self):
        """Test modem display name."""
        modem = Modem(
            phone_number="886480453",
            modem_type=ModemType.MOCK,
            name="Main",
        )

        assert modem.display_name == "Main (886480453)"

    def test_modem_availability(self):
        """Test modem availability check."""
        modem = Modem(
            phone_number="886480453",
            modem_type=ModemType.MOCK,
            is_active=True,
            status=ModemStatus.ONLINE,
            max_sms_per_hour=30,
            current_hour_count=0,
        )

        assert modem.is_available

    def test_modem_not_available_when_over_limit(self):
        """Test modem not available when over rate limit."""
        modem = Modem(
            phone_number="886480453",
            modem_type=ModemType.MOCK,
            is_active=True,
            status=ModemStatus.ONLINE,
            max_sms_per_hour=30,
            current_hour_count=30,
        )

        assert not modem.is_available

    def test_modem_record_send(self):
        """Test recording SMS send."""
        modem = Modem(
            phone_number="886480453",
            modem_type=ModemType.MOCK,
        )

        modem.record_send()

        assert modem.total_sent == 1
        assert modem.current_hour_count == 1
        assert modem.last_activity_at is not None
