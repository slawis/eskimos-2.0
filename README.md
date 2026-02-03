# Eskimos 2.0

> SMS Gateway with AI - Professional Python Architecture

Profesjonalna implementacja systemu do automatycznego pozyskiwania pośredników przez SMS z AI.

## Features

- **Modem Adapter Pattern** - wsparcie dla różnych modemów GSM (IK41VE1, Dinstar)
- **AI Engine (Claude)** - personalizacja SMS i auto-reply
- **Campaign Manager** - sekwencje SMS (lejki konwersacyjne)
- **Rate Limiting** - 50-100 SMS/dzień, okno 9:00-20:00, jitter
- **CLI** - łatwe testowanie z linii poleceń
- **FastAPI** - REST API i dashboard (planowane)

## Installation

```bash
# Clone repo
cd eskimos-2.0

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Install dependencies
pip install -e .

# Or with dev dependencies
pip install -e ".[dev]"
```

## Quick Start

```bash
# Check available commands
eskimos --help

# Send test SMS (mock modem)
eskimos send 123456789 "Hello World" --modem mock

# Dry run (show what would be sent)
eskimos send 123456789 "Test message" --dry-run

# Check modem status
eskimos modem status

# Send real SMS via Puppeteer (IK41VE1)
eskimos send 123456789 "Real SMS" --modem puppeteer

# Run tests
pytest
```

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

Key settings:
- `MODEM_TYPE` - puppeteer, dinstar, or mock
- `MODEM_PHONE_NUMBER` - modem's phone number
- `ANTHROPIC_API_KEY` - Claude API key for AI features
- `RATE_LIMIT_SMS_PER_HOUR` - max SMS per hour

## Architecture

```
eskimos-2.0/
├── src/eskimos/
│   ├── core/           # Domain entities (SMS, Campaign, Contact)
│   ├── adapters/       # Modem & AI adapters
│   │   ├── modem/      # Puppeteer, Dinstar, Mock
│   │   └── ai/         # Claude API
│   ├── infrastructure/ # Config, DB, Scheduler
│   ├── api/            # FastAPI endpoints
│   └── cli/            # Typer CLI
└── tests/
```

## Modem Adapters

### MockModemAdapter (Testing)
```python
from eskimos.adapters.modem import MockModemAdapter, MockModemConfig

config = MockModemConfig(phone_number="886480453")
adapter = MockModemAdapter(config)

async with adapter:
    result = await adapter.send_sms("123456789", "Test")
    print(f"Success: {result.success}")
```

### PuppeteerModemAdapter (IK41VE1)
```python
from eskimos.adapters.modem.puppeteer import PuppeteerModemAdapter, PuppeteerConfig

config = PuppeteerConfig(phone_number="886480453", headless=False)
adapter = PuppeteerModemAdapter(config)

async with adapter:
    result = await adapter.send_sms("123456789", "Real SMS")
```

## AI Features

### SMS Personalization
```python
from eskimos.adapters.ai import ClaudeAdapter

adapter = ClaudeAdapter(api_key="sk-ant-...")

result = await adapter.personalize_sms(
    "Cześć {name}! Czy interesuje Cię współpraca?",
    {"name": "Jan", "company": "ABC"},
)
print(result.personalized)
```

### Auto-Reply
```python
from eskimos.adapters.ai import ClaudeAdapter
from eskimos.adapters.ai.base import ConversationContext

adapter = ClaudeAdapter(api_key="sk-ant-...")

reply = await adapter.generate_auto_reply(
    "Jestem zainteresowany, proszę o więcej info",
    ConversationContext(contact_name="Jan"),
)

if reply.should_reply:
    print(f"Reply: {reply.reply_content}")
    print(f"Intent: {reply.intent}")
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run tests with coverage
pytest --cov

# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/

# Type check
mypy src/
```

## Rate Limiting

Legal requirements for SMS marketing in Poland:
- Max 50-100 SMS/SIM/day
- Only 9:00-20:00
- Mon-Fri only (configurable)
- STOP opt-out mechanism

## Roadmap

- [x] Core entities (SMS, Campaign, Contact)
- [x] Modem adapter pattern
- [x] PuppeteerAdapter (IK41VE1)
- [x] MockAdapter (testing)
- [x] CLI commands
- [x] AI Engine (Claude)
- [ ] DinstarAdapter (HTTP API)
- [ ] FastAPI endpoints
- [ ] Campaign workflows
- [ ] Dashboard (htmx)

## License

MIT

---

*Eskimos 2.0 - Part of NinjaBot ecosystem*
