"""Campaign management endpoints."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from eskimos.core.entities.campaign import CampaignSchedule, CampaignStatus, CampaignStep, ConditionType

router = APIRouter()


# ==================== Request/Response Models ====================

class CampaignStepRequest(BaseModel):
    step_number: int = Field(..., ge=1)
    message_template: str = Field(..., max_length=640)
    delay_hours: int = Field(default=0, ge=0)
    delay_days: int = Field(default=0, ge=0)
    condition_type: str = "always"
    use_ai_personalization: bool = False
    ai_style: str = "professional"


class CampaignScheduleRequest(BaseModel):
    start_date: datetime
    end_date: datetime | None = None
    send_time_start: str = "09:00"
    send_time_end: str = "20:00"
    allowed_days: list[int] = [0, 1, 2, 3, 4]
    max_sms_per_hour: int = Field(default=60, ge=1, le=500)
    max_sms_per_day: int = Field(default=500, ge=1, le=5000)
    min_delay_seconds: int = Field(default=30, ge=0)
    max_delay_seconds: int = Field(default=180, ge=0)


class CreateCampaignRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    user_id: str = "default"
    description: str | None = None
    steps: list[CampaignStepRequest] = []
    schedule: CampaignScheduleRequest
    enable_ai_replies: bool = False
    ai_reply_prompt: str | None = None


class UpdateCampaignRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    steps: list[CampaignStepRequest] | None = None
    enable_ai_replies: bool | None = None
    ai_reply_prompt: str | None = None


class CampaignResponse(BaseModel):
    id: str
    name: str
    description: str | None
    status: str
    total_contacts: int
    sent_count: int
    reply_count: int
    steps_count: int
    created_at: datetime
    started_at: datetime | None


class CampaignDetailResponse(CampaignResponse):
    steps: list[dict]
    schedule: dict
    delivery_rate: float
    reply_rate: float
    enable_ai_replies: bool


class AddContactsRequest(BaseModel):
    contact_ids: list[str]


# ==================== Helpers ====================

def _parse_time(s: str) -> time:
    parts = s.split(":")
    return time(int(parts[0]), int(parts[1]))


def _build_steps(steps_data: list[CampaignStepRequest]) -> list[CampaignStep]:
    return [
        CampaignStep(
            step_number=s.step_number,
            message_template=s.message_template,
            delay_hours=s.delay_hours,
            delay_days=s.delay_days,
            condition_type=ConditionType(s.condition_type),
            use_ai_personalization=s.use_ai_personalization,
            ai_style=s.ai_style,
        )
        for s in steps_data
    ]


def _build_schedule(req: CampaignScheduleRequest) -> CampaignSchedule:
    return CampaignSchedule(
        start_date=req.start_date,
        end_date=req.end_date,
        send_time_start=_parse_time(req.send_time_start),
        send_time_end=_parse_time(req.send_time_end),
        allowed_days=req.allowed_days,
        max_sms_per_hour=req.max_sms_per_hour,
        max_sms_per_day=req.max_sms_per_day,
        min_delay_seconds=req.min_delay_seconds,
        max_delay_seconds=req.max_delay_seconds,
    )


def _campaign_to_response(c) -> CampaignResponse:
    return CampaignResponse(
        id=c.id,
        name=c.name,
        description=c.description,
        status=c.status.value,
        total_contacts=c.total_contacts,
        sent_count=c.sent_count,
        reply_count=c.reply_count,
        steps_count=len(c.steps),
        created_at=c.created_at,
        started_at=c.started_at,
    )


def _campaign_to_detail(c) -> CampaignDetailResponse:
    return CampaignDetailResponse(
        id=c.id,
        name=c.name,
        description=c.description,
        status=c.status.value,
        total_contacts=c.total_contacts,
        sent_count=c.sent_count,
        reply_count=c.reply_count,
        steps_count=len(c.steps),
        created_at=c.created_at,
        started_at=c.started_at,
        steps=[s.model_dump() for s in c.steps],
        schedule=c.schedule.model_dump(mode="json"),
        delivery_rate=c.delivery_rate,
        reply_rate=c.reply_rate,
        enable_ai_replies=c.enable_ai_replies,
    )


# ==================== Endpoints ====================

@router.post("", response_model=CampaignDetailResponse)
async def create_campaign(req: CreateCampaignRequest):
    from eskimos.api.dependencies import get_campaign_service
    svc = get_campaign_service()

    campaign = await svc.create_campaign(
        name=req.name,
        user_id=req.user_id,
        schedule=_build_schedule(req.schedule),
        steps=_build_steps(req.steps),
        description=req.description,
        enable_ai_replies=req.enable_ai_replies,
        ai_reply_prompt=req.ai_reply_prompt,
    )
    return _campaign_to_detail(campaign)


@router.get("", response_model=list[CampaignResponse])
async def list_campaigns(
    status: str | None = None,
    skip: int = 0,
    limit: int = 100,
):
    from eskimos.api.dependencies import get_campaign_service
    svc = get_campaign_service()

    status_enum = CampaignStatus(status) if status else None
    campaigns = await svc.list_campaigns(status=status_enum, skip=skip, limit=limit)
    return [_campaign_to_response(c) for c in campaigns]


@router.get("/{campaign_id}", response_model=CampaignDetailResponse)
async def get_campaign(campaign_id: str):
    from eskimos.api.dependencies import get_campaign_service
    svc = get_campaign_service()

    campaign = await svc.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return _campaign_to_detail(campaign)


@router.put("/{campaign_id}", response_model=CampaignDetailResponse)
async def update_campaign(campaign_id: str, req: UpdateCampaignRequest):
    from eskimos.api.dependencies import get_campaign_service
    svc = get_campaign_service()

    fields: dict[str, Any] = {}
    if req.name is not None:
        fields["name"] = req.name
    if req.description is not None:
        fields["description"] = req.description
    if req.steps is not None:
        fields["steps"] = _build_steps(req.steps)
    if req.enable_ai_replies is not None:
        fields["enable_ai_replies"] = req.enable_ai_replies
    if req.ai_reply_prompt is not None:
        fields["ai_reply_prompt"] = req.ai_reply_prompt

    try:
        campaign = await svc.update_campaign(campaign_id, **fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return _campaign_to_detail(campaign)


@router.delete("/{campaign_id}")
async def delete_campaign(campaign_id: str):
    from eskimos.api.dependencies import get_campaign_service
    svc = get_campaign_service()

    try:
        result = await svc.delete_campaign(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not result:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"deleted": True}


@router.post("/{campaign_id}/start", response_model=CampaignDetailResponse)
async def start_campaign(campaign_id: str):
    from eskimos.api.dependencies import get_campaign_service
    svc = get_campaign_service()

    try:
        campaign = await svc.start_campaign(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _campaign_to_detail(campaign)


@router.post("/{campaign_id}/pause", response_model=CampaignDetailResponse)
async def pause_campaign(campaign_id: str):
    from eskimos.api.dependencies import get_campaign_service
    svc = get_campaign_service()

    try:
        campaign = await svc.pause_campaign(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _campaign_to_detail(campaign)


@router.post("/{campaign_id}/cancel", response_model=CampaignDetailResponse)
async def cancel_campaign(campaign_id: str):
    from eskimos.api.dependencies import get_campaign_service
    svc = get_campaign_service()

    try:
        campaign = await svc.cancel_campaign(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _campaign_to_detail(campaign)


@router.get("/{campaign_id}/stats")
async def campaign_stats(campaign_id: str):
    from eskimos.api.dependencies import get_campaign_service
    svc = get_campaign_service()

    stats = await svc.get_campaign_stats(campaign_id)
    if not stats:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return stats


@router.post("/{campaign_id}/contacts")
async def add_contacts_to_campaign(campaign_id: str, req: AddContactsRequest):
    from eskimos.api.dependencies import get_campaign_service, get_contact_service
    campaign_svc = get_campaign_service()
    contact_svc = get_contact_service()

    contacts = []
    for cid in req.contact_ids:
        contact = await contact_svc.get_contact(cid)
        if contact:
            contacts.append(contact)

    if not contacts:
        raise HTTPException(status_code=400, detail="No valid contacts found")

    try:
        added = await campaign_svc.add_contacts_to_campaign(campaign_id, contacts)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"added": added, "total_provided": len(req.contact_ids)}
