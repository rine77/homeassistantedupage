"""Explicit consultation actions; dry runs never submit a reservation."""

import asyncio

from datetime import datetime

import voluptuous as vol
from edupage_api.exceptions import ConsultationError, ExpiredSessionException
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from .const import CONF_CONSULTATIONS_MY_TEACHERS, CONF_STUDENT_ID, DOMAIN
from .participation import consultation_item
from .session_tasks import finish_before_cancel


def _error(code):
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=code)


async def perform_action(
    hass,
    entry_id,
    action,
    *,
    consultation_id=None,
    start=None,
    visit_id=None,
    dry_run=True,
    note=None,
    allow_unrelated_teacher=False,
):
    coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
    if coordinator is None:
        raise _error("entry_not_loaded")
    entry = coordinator.config_entry
    params = {"dry_run": dry_run}
    if action == "book":
        starts_at = dt_util.parse_datetime(start) if isinstance(start, str) else start
        if (
            not isinstance(starts_at, datetime)
            or starts_at.tzinfo is None
            or starts_at.utcoffset() is None
        ):
            raise _error("invalid_start")
        params["require_related_teacher"] = (
            entry.options.get(CONF_CONSULTATIONS_MY_TEACHERS, True)
            and not allow_unrelated_teacher
        )
        params.update(
            consultation_id=str(consultation_id), starts_at=starts_at, note=note
        )
        method = "book_consultation_slot"
    else:
        method = "cancel_consultation"
        params["visit_id"] = str(visit_id)
    async def select_and_act():
        students = await coordinator.edupage.get_students()
        student = next(
            (s for s in students or []
             if str(s.person_id) == str(entry.data[CONF_STUDENT_ID])),
            None,
        )
        if student is None:
            raise _error("entry_not_loaded")
        result = await coordinator.edupage.consultation_action(student, method, **params)
        if not dry_run and action == "cancel":
            coordinator._ha_cancelled_visits = getattr(
                coordinator, "_ha_cancelled_visits", set()
            ) | {str(visit_id)}
        return result

    # Do not shield lock acquisition: cancelling a queued action must prevent it
    # from writing later. Once started, finish the action/restoration under lock.
    started = False
    try:
        async with coordinator.fetch_lock:
            started = True
            result = await finish_before_cancel(select_and_act())
        if not dry_run:
            await coordinator.async_request_refresh()
    except asyncio.CancelledError:
        if started and not dry_run:
            # Read back after a started action; never retry the write. The lock
            # has been released only after parent-context restoration.
            try:
                await finish_before_cancel(coordinator.async_request_refresh())
            except (Exception, asyncio.CancelledError):
                pass
        raise
    except ExpiredSessionException as exc:
        entry.async_start_reauth(hass)
        raise _error("session_expired") from exc
    except ConsultationError as exc:
        if not dry_run:
            await coordinator.async_request_refresh()
        raise _error(exc.code) from exc
    except HomeAssistantError:
        raise
    except Exception as exc:
        # Do not retry an uncertain write. Request a read refresh so the user
        # can inspect server state before choosing whether to try again.
        if not dry_run:
            await coordinator.async_request_refresh()
        raise _error("booking_uncertain") from exc
    if dry_run:
        return {"dry_run": True, "plan": result}
    if action == "cancel":
        return {"dry_run": False, "cancelled": True, "visit_id": str(visit_id)}
    data = coordinator.data.get("consultations") or {}
    blocks = (
        {b.consultation_id: b for b in data.get("overview").consultations}
        if data.get("overview")
        else {}
    )
    return {"dry_run": False, **consultation_item(result, blocks)}


async def async_setup_services(hass):
    if hass.services.has_service(DOMAIN, "book_consultation"):
        return

    async def book(call):
        return await perform_action(hass, **call.data, action="book")

    async def cancel(call):
        return await perform_action(hass, **call.data, action="cancel")

    base = {vol.Required("entry_id"): str, vol.Optional("dry_run", default=True): bool}
    hass.services.async_register(
        DOMAIN,
        "book_consultation",
        book,
        schema=vol.Schema(
            {
                **base,
                vol.Required("consultation_id"): str,
                vol.Required("start"): str,
                vol.Optional("note"): str,
                vol.Optional("allow_unrelated_teacher", default=False): bool,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "cancel_consultation",
        cancel,
        schema=vol.Schema({**base, vol.Required("visit_id"): str}),
        supports_response=SupportsResponse.OPTIONAL,
    )
