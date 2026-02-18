"""
Google Calendar toolset for agents.
"""

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Tool, RunContext

from app.agents.core import BusiboxDeps


class CalendarEvent(BaseModel):
    id: str
    summary: str
    start: str
    end: str
    html_link: Optional[str] = None


class CalendarListOutput(BaseModel):
    success: bool
    events: List[CalendarEvent] = Field(default_factory=list)
    count: int = 0
    error: Optional[str] = None


class CalendarCreateOutput(BaseModel):
    success: bool
    event: Optional[CalendarEvent] = None
    error: Optional[str] = None


def _calendar_access_token() -> Optional[str]:
    return os.environ.get("GOOGLE_CALENDAR_ACCESS_TOKEN")


def _calendar_id() -> str:
    return os.environ.get("GOOGLE_CALENDAR_ID", "primary")


def _to_event(item: Dict[str, Any]) -> CalendarEvent:
    start = (item.get("start") or {}).get("dateTime") or (item.get("start") or {}).get("date") or ""
    end = (item.get("end") or {}).get("dateTime") or (item.get("end") or {}).get("date") or ""
    return CalendarEvent(
        id=str(item.get("id", "")),
        summary=str(item.get("summary", "")),
        start=start,
        end=end,
        html_link=item.get("htmlLink"),
    )


async def calendar_list_events(
    ctx: RunContext[BusiboxDeps],
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 10,
) -> CalendarListOutput:
    """
    List upcoming events from Google Calendar.

    Datetime format: RFC3339, e.g. 2026-02-18T00:00:00Z
    """
    token = _calendar_access_token()
    if not token:
        return CalendarListOutput(success=False, error="GOOGLE_CALENDAR_ACCESS_TOKEN is not configured")

    calendar_id = _calendar_id()
    params: Dict[str, Any] = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": max(1, min(max_results, 50)),
    }
    if time_min:
        params["timeMin"] = time_min
    else:
        params["timeMin"] = datetime.utcnow().isoformat() + "Z"
    if time_max:
        params["timeMax"] = time_max

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            payload = response.json()
        items = payload.get("items", [])
        events = [_to_event(item) for item in items]
        return CalendarListOutput(success=True, events=events, count=len(events))
    except Exception as e:
        return CalendarListOutput(success=False, error=str(e))


async def calendar_create_event(
    ctx: RunContext[BusiboxDeps],
    summary: str,
    start: str,
    end: str,
    description: Optional[str] = None,
    timezone: str = "UTC",
) -> CalendarCreateOutput:
    """
    Create a Google Calendar event.

    start/end should be RFC3339 datetimes (e.g. 2026-02-18T15:00:00Z).
    """
    token = _calendar_access_token()
    if not token:
        return CalendarCreateOutput(success=False, error="GOOGLE_CALENDAR_ACCESS_TOKEN is not configured")

    calendar_id = _calendar_id()
    payload: Dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start, "timeZone": timezone},
        "end": {"dateTime": end, "timeZone": timezone},
    }
    if description:
        payload["description"] = description

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            item = response.json()
        return CalendarCreateOutput(success=True, event=_to_event(item))
    except Exception as e:
        return CalendarCreateOutput(success=False, error=str(e))


calendar_list_events_tool = Tool(
    calendar_list_events,
    takes_ctx=True,
    name="calendar_list_events",
    description="List upcoming Google Calendar events.",
)

calendar_create_event_tool = Tool(
    calendar_create_event,
    takes_ctx=True,
    name="calendar_create_event",
    description="Create a Google Calendar event.",
)
