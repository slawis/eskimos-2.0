"""Claude AI adapter for Eskimos 2.0.

Provides AI-powered SMS personalization and auto-reply using Anthropic's Claude API.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from eskimos.adapters.ai.base import (
    AIAdapter,
    AutoReplyResult,
    ConversationContext,
    PersonalizedSMS,
)

logger = logging.getLogger(__name__)


# System prompts for different operations
PERSONALIZE_SYSTEM_PROMPT = """Jesteś ekspertem od personalizacji wiadomości SMS w kontekście B2B.
Twoim zadaniem jest spersonalizowanie szablonu SMS używając podanych danych.

ZASADY:
1. Zachowaj profesjonalny, ale przyjazny ton
2. Wiadomość musi być krótka (max 160 znaków dla jednego SMS)
3. Użyj podanych danych naturalnie w treści
4. NIE zmieniaj głównego przekazu wiadomości
5. Zwróć TYLKO spersonalizowaną wiadomość, bez żadnych dodatkowych komentarzy

Styl: {style}
Dostępne dane: {context}
"""

AUTO_REPLY_SYSTEM_PROMPT = """Jesteś asystentem do automatycznych odpowiedzi SMS w kontekście rekrutacji partnerów biznesowych.
Analizujesz wiadomości przychodzące i generujesz odpowiednie odpowiedzi.

KONTEKST ROZMOWY:
{conversation_context}

ZASADY:
1. Odpowiadaj profesjonalnie i konkretnie
2. Odpowiedź max 160 znaków
3. Jeśli osoba pisze STOP/KONIEC/NIE DZWON - NIE odpowiadaj (should_reply=false)
4. Jeśli osoba zadaje pytanie - odpowiedz na nie
5. Jeśli osoba jest zainteresowana - zaproponuj następny krok
6. Jeśli osoba nie jest zainteresowana - grzecznie się pożegnaj

Zwróć odpowiedź w formacie JSON:
{{
    "should_reply": true/false,
    "reply_content": "treść odpowiedzi lub null",
    "sentiment": "positive/negative/neutral/question",
    "intent": "interested/not_interested/question/stop",
    "confidence": 0.0-1.0,
    "reasoning": "krótkie wyjaśnienie decyzji"
}}
"""

SENTIMENT_SYSTEM_PROMPT = """Przeanalizuj sentyment poniższej wiadomości SMS.
Zwróć wynik jako JSON z wartościami 0.0-1.0 dla każdej kategorii:
{
    "positive": 0.0-1.0,
    "negative": 0.0-1.0,
    "neutral": 0.0-1.0
}

Suma powinna wynosić około 1.0.
"""


class ClaudeAdapter:
    """Claude AI adapter using Anthropic API.

    Example:
        adapter = ClaudeAdapter(api_key="sk-ant-...")

        result = await adapter.personalize_sms(
            "Cześć {name}!",
            {"name": "Jan"},
        )
        print(result.personalized)
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-haiku-20240307",
        max_tokens: int = 500,
    ):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self._client = None

    async def _get_client(self):
        """Get or create Anthropic client."""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic

                self._client = AsyncAnthropic(api_key=self.api_key)
            except ImportError:
                raise ImportError(
                    "anthropic package not installed. Run: pip install anthropic"
                )
        return self._client

    async def _call_claude(
        self,
        system_prompt: str,
        user_message: str,
    ) -> tuple[str, int]:
        """Make API call to Claude.

        Returns:
            Tuple of (response_text, tokens_used)
        """
        client = await self._get_client()

        response = await client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        text = response.content[0].text
        tokens = response.usage.input_tokens + response.usage.output_tokens

        return text, tokens

    async def personalize_sms(
        self,
        template: str,
        context: dict[str, str],
        *,
        style: str = "professional",
    ) -> PersonalizedSMS:
        """Personalize SMS template using Claude."""
        # First try simple variable substitution
        simple_result = self._simple_personalize(template, context)

        # If no API key, return simple result
        if not self.api_key:
            return simple_result

        try:
            system = PERSONALIZE_SYSTEM_PROMPT.format(
                style=style,
                context=json.dumps(context, ensure_ascii=False),
            )

            user_message = f"Szablon do spersonalizowania:\n{template}"

            response, tokens = await self._call_claude(system, user_message)

            return PersonalizedSMS(
                original=template,
                personalized=response.strip(),
                variables_used=context,
                tokens_used=tokens,
            )

        except Exception as e:
            logger.warning(f"Claude personalization failed, using simple: {e}")
            return simple_result

    def _simple_personalize(
        self,
        template: str,
        context: dict[str, str],
    ) -> PersonalizedSMS:
        """Simple variable substitution without AI."""
        result = template
        used = {}

        for key, value in context.items():
            placeholder = "{" + key + "}"
            if placeholder in result:
                result = result.replace(placeholder, value)
                used[key] = value

        return PersonalizedSMS(
            original=template,
            personalized=result,
            variables_used=used,
            tokens_used=0,
        )

    async def generate_auto_reply(
        self,
        incoming_message: str,
        conversation: ConversationContext,
    ) -> AutoReplyResult:
        """Generate automatic reply using Claude."""
        # Check for STOP keywords first (no AI needed)
        if self._is_stop_message(incoming_message):
            return AutoReplyResult(
                should_reply=False,
                reply_content=None,
                sentiment="negative",
                intent="stop",
                confidence=1.0,
                reasoning="STOP keyword detected",
            )

        # If no API key, return default response
        if not self.api_key:
            return self._default_auto_reply(incoming_message)

        try:
            # Build conversation context
            context_text = self._build_conversation_context(conversation)

            system = AUTO_REPLY_SYSTEM_PROMPT.format(
                conversation_context=context_text,
            )

            user_message = f"Wiadomość do odpowiedzenia:\n{incoming_message}"

            response, tokens = await self._call_claude(system, user_message)

            # Parse JSON response
            result = self._parse_auto_reply_response(response)
            result.tokens_used = tokens

            return result

        except Exception as e:
            logger.warning(f"Claude auto-reply failed: {e}")
            return self._default_auto_reply(incoming_message)

    def _is_stop_message(self, message: str) -> bool:
        """Check if message contains STOP keywords."""
        stop_keywords = {
            "stop", "koniec", "nie dzwon", "niedzwon", "wypisz",
            "rezygnuje", "rezygnuję", "nie pisz", "niepisz",
            "usun", "usuń", "anuluj",
        }
        message_lower = message.lower().strip()

        # Exact match
        if message_lower in stop_keywords:
            return True

        # Contains keyword
        for keyword in stop_keywords:
            if keyword in message_lower:
                return True

        return False

    def _build_conversation_context(self, conv: ConversationContext) -> str:
        """Build text representation of conversation context."""
        parts = []

        if conv.contact_name:
            parts.append(f"Nazwa kontaktu: {conv.contact_name}")
        if conv.contact_company:
            parts.append(f"Firma: {conv.contact_company}")
        if conv.campaign_name:
            parts.append(f"Kampania: {conv.campaign_name}")
        if conv.campaign_goal:
            parts.append(f"Cel: {conv.campaign_goal}")
        if conv.current_step:
            parts.append(f"Krok: {conv.current_step}")

        if conv.previous_messages:
            parts.append("\nHistoria:")
            for msg in conv.previous_messages[-5:]:  # Last 5 messages
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                parts.append(f"  [{role}]: {content}")

        if conv.custom_context:
            parts.append(f"\nDodatkowy kontekst: {conv.custom_context}")

        return "\n".join(parts) if parts else "Brak kontekstu"

    def _parse_auto_reply_response(self, response: str) -> AutoReplyResult:
        """Parse JSON response from Claude."""
        try:
            # Try to extract JSON from response
            json_match = re.search(r"\{[^{}]+\}", response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = json.loads(response)

            return AutoReplyResult(
                should_reply=data.get("should_reply", False),
                reply_content=data.get("reply_content"),
                sentiment=data.get("sentiment", "neutral"),
                intent=data.get("intent", "unknown"),
                confidence=float(data.get("confidence", 0.5)),
                reasoning=data.get("reasoning"),
            )

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse auto-reply response: {e}")
            return AutoReplyResult(
                should_reply=True,
                reply_content=response[:160] if response else None,
                sentiment="neutral",
                intent="unknown",
                confidence=0.3,
                reasoning="Failed to parse structured response",
            )

    def _default_auto_reply(self, message: str) -> AutoReplyResult:
        """Generate default auto-reply without AI."""
        # Simple keyword-based analysis
        message_lower = message.lower()

        if "?" in message or any(
            q in message_lower for q in ["jak", "kiedy", "gdzie", "ile", "co"]
        ):
            return AutoReplyResult(
                should_reply=True,
                reply_content="Dziękuję za pytanie. Skontaktuję się z Panem/Panią telefonicznie, aby odpowiedzieć szczegółowo.",
                sentiment="question",
                intent="question",
                confidence=0.6,
            )

        if any(
            w in message_lower for w in ["tak", "zgoda", "interesuje", "chętnie"]
        ):
            return AutoReplyResult(
                should_reply=True,
                reply_content="Świetnie! Odezwę się telefonicznie, aby omówić szczegóły współpracy.",
                sentiment="positive",
                intent="interested",
                confidence=0.7,
            )

        if any(
            w in message_lower for w in ["nie", "dzięki nie", "nie interesuje"]
        ):
            return AutoReplyResult(
                should_reply=True,
                reply_content="Rozumiem. Dziękuję za odpowiedź. Życzę powodzenia!",
                sentiment="negative",
                intent="not_interested",
                confidence=0.7,
            )

        # Default response
        return AutoReplyResult(
            should_reply=True,
            reply_content="Dziękuję za wiadomość. Odezwę się wkrótce.",
            sentiment="neutral",
            intent="unknown",
            confidence=0.4,
        )

    async def analyze_sentiment(
        self,
        message: str,
    ) -> dict[str, float]:
        """Analyze sentiment of a message."""
        if not self.api_key:
            return self._simple_sentiment(message)

        try:
            response, _ = await self._call_claude(
                SENTIMENT_SYSTEM_PROMPT,
                message,
            )

            data = json.loads(response)
            return {
                "positive": float(data.get("positive", 0.33)),
                "negative": float(data.get("negative", 0.33)),
                "neutral": float(data.get("neutral", 0.34)),
            }

        except Exception as e:
            logger.warning(f"Sentiment analysis failed: {e}")
            return self._simple_sentiment(message)

    def _simple_sentiment(self, message: str) -> dict[str, float]:
        """Simple keyword-based sentiment analysis."""
        message_lower = message.lower()

        positive_words = {"tak", "super", "świetnie", "zgoda", "ok", "dobrze"}
        negative_words = {"nie", "stop", "koniec", "źle", "nie chcę"}

        pos_count = sum(1 for w in positive_words if w in message_lower)
        neg_count = sum(1 for w in negative_words if w in message_lower)

        total = pos_count + neg_count + 1
        positive = pos_count / total
        negative = neg_count / total
        neutral = 1 - positive - negative

        return {
            "positive": positive,
            "negative": negative,
            "neutral": max(0, neutral),
        }
