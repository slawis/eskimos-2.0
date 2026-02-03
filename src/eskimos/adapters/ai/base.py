"""Base AI adapter interface.

Defines the Protocol for AI adapters (Claude, OpenAI, etc.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class PersonalizedSMS:
    """Result of SMS personalization."""

    original: str
    personalized: str
    variables_used: dict[str, str] = field(default_factory=dict)
    tokens_used: int = 0


@dataclass
class AutoReplyResult:
    """Result of auto-reply generation."""

    should_reply: bool
    reply_content: str | None = None

    # Analysis
    sentiment: str = "neutral"  # positive, negative, neutral, question
    intent: str = "unknown"  # interested, not_interested, question, stop
    confidence: float = 0.0

    # Metadata
    tokens_used: int = 0
    reasoning: str | None = None


@dataclass
class ConversationContext:
    """Context for conversation-aware AI operations."""

    contact_name: str | None = None
    contact_company: str | None = None

    # Previous messages in conversation
    previous_messages: list[dict] = field(default_factory=list)

    # Campaign context
    campaign_name: str | None = None
    campaign_goal: str | None = None
    current_step: int = 1

    # Custom context
    custom_context: str | None = None


@runtime_checkable
class AIAdapter(Protocol):
    """Protocol for AI adapters.

    Every AI provider (Claude, OpenAI, etc.) must implement this interface.

    Example:
        adapter = ClaudeAdapter(api_key="...")

        # Personalize SMS
        result = await adapter.personalize_sms(
            "Cześć {name}! Czy interesuje Cię współpraca?",
            {"name": "Jan", "company": "ABC"},
        )
        print(result.personalized)

        # Generate auto-reply
        reply = await adapter.generate_auto_reply(
            "Jestem zainteresowany, proszę o więcej informacji",
            ConversationContext(contact_name="Jan"),
        )
        if reply.should_reply:
            print(reply.reply_content)
    """

    async def personalize_sms(
        self,
        template: str,
        context: dict[str, str],
        *,
        style: str = "professional",
    ) -> PersonalizedSMS:
        """Personalize SMS template using AI.

        Args:
            template: Message template with {placeholders}
            context: Data for personalization (name, company, etc.)
            style: Writing style (professional, casual, formal)

        Returns:
            PersonalizedSMS with original and personalized content
        """
        ...

    async def generate_auto_reply(
        self,
        incoming_message: str,
        conversation: ConversationContext,
    ) -> AutoReplyResult:
        """Generate automatic reply to incoming message.

        Analyzes the incoming message and generates appropriate response
        based on conversation context.

        Intents detected:
        - interested: Person wants to learn more
        - question: Person has a question
        - not_interested: Person is not interested
        - stop: Person wants to opt out (STOP)

        Args:
            incoming_message: The message to respond to
            conversation: Context about the conversation

        Returns:
            AutoReplyResult with should_reply flag and content
        """
        ...

    async def analyze_sentiment(
        self,
        message: str,
    ) -> dict[str, float]:
        """Analyze sentiment of a message.

        Returns:
            Dict with sentiment scores: {positive, negative, neutral}
        """
        ...
