"""Unit tests for MockModemAdapter."""

import pytest

from eskimos.adapters.modem.mock import MockModemAdapter, MockModemConfig
from eskimos.core.entities.modem import ModemStatus


class TestMockModemAdapter:
    """Tests for MockModemAdapter."""

    @pytest.mark.asyncio
    async def test_connect_disconnect(self, mock_modem_config):
        """Test connecting and disconnecting."""
        adapter = MockModemAdapter(mock_modem_config)

        assert not adapter.is_connected
        assert adapter.status == ModemStatus.OFFLINE

        await adapter.connect()

        assert adapter.is_connected
        assert adapter.status == ModemStatus.ONLINE

        await adapter.disconnect()

        assert not adapter.is_connected
        assert adapter.status == ModemStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_send_sms_success(self, mock_modem):
        """Test successful SMS send."""
        result = await mock_modem.send_sms("123456789", "Test message")

        assert result.success
        assert result.message_id is not None
        assert result.message_id.startswith("mock_")
        assert result.sent_at is not None
        assert result.modem_number == "886480453"

    @pytest.mark.asyncio
    async def test_send_sms_tracks_outbox(self, mock_modem):
        """Test sent SMS is tracked in outbox."""
        await mock_modem.send_sms("123456789", "Test 1")
        await mock_modem.send_sms("987654321", "Test 2")

        assert len(mock_modem.outbox) == 2
        assert mock_modem.was_sent_to("123456789")
        assert mock_modem.was_sent_to("987654321")
        assert not mock_modem.was_sent_to("555555555")

    @pytest.mark.asyncio
    async def test_send_sms_failure_by_rate(self, failing_modem_config):
        """Test SMS send failure with 0% success rate."""
        adapter = MockModemAdapter(failing_modem_config)
        await adapter.connect()

        result = await adapter.send_sms("123456789", "Test")

        assert not result.success
        assert result.error == "Simulated failure"
        assert result.message_id is None

        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_send_sms_failure_by_number(self):
        """Test SMS send failure for specific numbers."""
        config = MockModemConfig(
            phone_number="886480453",
            success_rate=1.0,
            fail_on_numbers=["111111111"],
        )
        adapter = MockModemAdapter(config)
        await adapter.connect()

        # Should succeed for other numbers
        result1 = await adapter.send_sms("123456789", "Test")
        assert result1.success

        # Should fail for blacklisted number
        result2 = await adapter.send_sms("111111111", "Test")
        assert not result2.success

        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_receive_sms_empty(self, mock_modem):
        """Test receiving SMS when inbox is empty."""
        messages = await mock_modem.receive_sms()
        assert messages == []

    @pytest.mark.asyncio
    async def test_receive_sms_with_messages(self, mock_modem):
        """Test receiving simulated incoming SMS."""
        mock_modem.simulate_incoming("555555555", "Hello!")
        mock_modem.simulate_incoming("666666666", "Hi there!")

        assert mock_modem.inbox_size == 2

        messages = await mock_modem.receive_sms()

        assert len(messages) == 2
        assert messages[0].sender == "555555555"
        assert messages[0].content == "Hello!"
        assert messages[1].sender == "666666666"

        # Inbox should be empty after receive
        assert mock_modem.inbox_size == 0

    @pytest.mark.asyncio
    async def test_auto_reply_enabled(self):
        """Test auto-reply feature."""
        config = MockModemConfig(
            phone_number="886480453",
            auto_reply_enabled=True,
            auto_reply_content="Auto response",
        )
        adapter = MockModemAdapter(config)
        await adapter.connect()

        # Send SMS
        await adapter.send_sms("123456789", "Test")

        # Should have auto-reply in inbox
        messages = await adapter.receive_sms()
        assert len(messages) == 1
        assert messages[0].sender == "123456789"
        assert messages[0].content == "Auto response"

        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_health_check(self, mock_modem):
        """Test health check."""
        assert await mock_modem.health_check()

    @pytest.mark.asyncio
    async def test_health_check_not_connected(self, mock_modem_config):
        """Test health check when not connected."""
        adapter = MockModemAdapter(mock_modem_config)
        assert not await adapter.health_check()

    @pytest.mark.asyncio
    async def test_signal_strength(self, mock_modem):
        """Test signal strength."""
        strength = await mock_modem.get_signal_strength()
        assert strength == 75  # Default mock value

        mock_modem.set_signal_strength(50)
        assert await mock_modem.get_signal_strength() == 50

    @pytest.mark.asyncio
    async def test_context_manager(self, mock_modem_config):
        """Test async context manager usage."""
        async with MockModemAdapter(mock_modem_config) as adapter:
            assert adapter.is_connected

            result = await adapter.send_sms("123456789", "Test")
            assert result.success

        # Should be disconnected after context
        assert not adapter.is_connected

    @pytest.mark.asyncio
    async def test_clear_outbox(self, mock_modem):
        """Test clearing outbox."""
        await mock_modem.send_sms("123456789", "Test")
        assert len(mock_modem.outbox) == 1

        mock_modem.clear_outbox()
        assert len(mock_modem.outbox) == 0

    @pytest.mark.asyncio
    async def test_get_last_sent(self, mock_modem):
        """Test getting last sent message."""
        assert mock_modem.get_last_sent() is None

        await mock_modem.send_sms("123456789", "First")
        await mock_modem.send_sms("987654321", "Second")

        last = mock_modem.get_last_sent()
        assert last is not None
        assert last["recipient"] == "987654321"
        assert last["message"] == "Second"
