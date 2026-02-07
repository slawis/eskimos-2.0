"""Contact management endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

router = APIRouter()


# ==================== Request/Response Models ====================

class CreateContactRequest(BaseModel):
    phone: str = Field(..., min_length=9, max_length=12)
    name: str | None = None
    company: str | None = None
    position: str | None = None
    email: str | None = None
    source: str | None = None
    tags: list[str] = []


class UpdateContactRequest(BaseModel):
    name: str | None = None
    company: str | None = None
    position: str | None = None
    email: str | None = None
    tags: list[str] | None = None


class ContactResponse(BaseModel):
    id: str
    phone: str
    status: str
    name: str | None
    company: str | None
    email: str | None
    current_campaign_id: str | None
    current_step: int
    total_sms_received: int
    total_sms_sent: int
    total_replies: int
    sentiment_score: float | None
    interest_level: str
    created_at: datetime


class BulkImportRequest(BaseModel):
    contacts: list[CreateContactRequest]


class BlacklistRequest(BaseModel):
    reason: str = "manual"
    reason_detail: str | None = None


# ==================== Helpers ====================

def _contact_to_response(c) -> ContactResponse:
    return ContactResponse(
        id=c.id,
        phone=c.phone,
        status=c.status.value,
        name=c.name,
        company=c.company,
        email=c.email,
        current_campaign_id=c.current_campaign_id,
        current_step=c.current_step,
        total_sms_received=c.total_sms_received,
        total_sms_sent=c.total_sms_sent,
        total_replies=c.total_replies,
        sentiment_score=c.sentiment_score,
        interest_level=c.interest_level.value,
        created_at=c.created_at,
    )


# ==================== Endpoints ====================

@router.post("", response_model=ContactResponse)
async def create_contact(req: CreateContactRequest):
    from eskimos.api.dependencies import get_contact_service
    svc = get_contact_service()

    try:
        contact = await svc.create_contact(
            phone=req.phone,
            name=req.name,
            company=req.company,
            position=req.position,
            email=req.email,
            source=req.source,
            tags=req.tags,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return _contact_to_response(contact)


@router.get("", response_model=list[ContactResponse])
async def list_contacts(
    campaign_id: str | None = None,
    status: str | None = None,
    skip: int = 0,
    limit: int = 100,
):
    from eskimos.api.dependencies import get_contact_service
    from eskimos.core.entities.contact import ContactStatus
    svc = get_contact_service()

    status_enum = ContactStatus(status) if status else None
    contacts = await svc.list_contacts(
        campaign_id=campaign_id, status=status_enum,
        skip=skip, limit=limit,
    )
    return [_contact_to_response(c) for c in contacts]


@router.get("/{contact_id}", response_model=ContactResponse)
async def get_contact(contact_id: str):
    from eskimos.api.dependencies import get_contact_service
    svc = get_contact_service()

    contact = await svc.get_contact(contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return _contact_to_response(contact)


@router.put("/{contact_id}", response_model=ContactResponse)
async def update_contact(contact_id: str, req: UpdateContactRequest):
    from eskimos.api.dependencies import get_contact_service
    svc = get_contact_service()

    fields = {}
    if req.name is not None:
        fields["name"] = req.name
    if req.company is not None:
        fields["company"] = req.company
    if req.position is not None:
        fields["position"] = req.position
    if req.email is not None:
        fields["email"] = req.email
    if req.tags is not None:
        fields["tags"] = req.tags

    contact = await svc.update_contact(contact_id, **fields)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return _contact_to_response(contact)


@router.delete("/{contact_id}")
async def delete_contact(contact_id: str):
    from eskimos.api.dependencies import get_contact_service
    svc = get_contact_service()

    result = await svc.delete_contact(contact_id)
    if not result:
        raise HTTPException(status_code=404, detail="Contact not found")
    return {"deleted": True}


@router.post("/import")
async def import_contacts(req: BulkImportRequest):
    from eskimos.api.dependencies import get_contact_service
    svc = get_contact_service()

    contacts_data = [
        {
            "phone": c.phone,
            "name": c.name,
            "company": c.company,
            "position": c.position,
            "email": c.email,
            "source": c.source or "api_import",
            "tags": c.tags,
        }
        for c in req.contacts
    ]

    created, skipped = await svc.bulk_create(contacts_data)
    return {"created": created, "skipped": skipped, "total": len(req.contacts)}


@router.post("/import-csv")
async def import_contacts_csv(file: UploadFile = File(...)):
    from eskimos.api.dependencies import get_contact_service
    svc = get_contact_service()

    content = await file.read()
    csv_data = content.decode("utf-8")
    created, skipped = await svc.import_csv(csv_data)
    return {"created": created, "skipped": skipped, "filename": file.filename}


@router.post("/{contact_id}/blacklist")
async def blacklist_contact(contact_id: str, req: BlacklistRequest):
    from eskimos.api.dependencies import get_contact_service
    from eskimos.core.entities.contact import BlacklistReason
    svc = get_contact_service()

    contact = await svc.get_contact(contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    try:
        reason = BlacklistReason(req.reason)
    except ValueError:
        reason = BlacklistReason.MANUAL

    entry = await svc.blacklist_contact(
        phone=contact.phone,
        reason=reason,
        reason_detail=req.reason_detail,
    )
    return {"blacklisted": True, "entry_id": entry.id}
