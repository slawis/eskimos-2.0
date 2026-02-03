"""Core domain entities for Eskimos 2.0."""

from eskimos.core.entities.sms import (
    SMS,
    SMSDirection,
    SMSStatus,
    IncomingSMS,
    SMSResult,
)
from eskimos.core.entities.campaign import (
    Campaign,
    CampaignStatus,
    CampaignStep,
    CampaignSchedule,
)
from eskimos.core.entities.contact import (
    Contact,
    ContactStatus,
    Blacklist,
)
from eskimos.core.entities.modem import (
    Modem,
    ModemStatus,
    ModemType,
    ModemHealthStatus,
)

__all__ = [
    # SMS
    "SMS",
    "SMSDirection",
    "SMSStatus",
    "IncomingSMS",
    "SMSResult",
    # Campaign
    "Campaign",
    "CampaignStatus",
    "CampaignStep",
    "CampaignSchedule",
    # Contact
    "Contact",
    "ContactStatus",
    "Blacklist",
    # Modem
    "Modem",
    "ModemStatus",
    "ModemType",
    "ModemHealthStatus",
]
