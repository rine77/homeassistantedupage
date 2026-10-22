"""Bounded, privacy-conscious summaries of registrations and consultations."""

from urllib.parse import urlencode


def registration_open(action, now):
    """Use server eligibility plus dates; assigned lists also contain closed items."""
    return (
        action.active
        and action.votable
        and (action.opens_at is None or action.opens_at <= now)
        and (action.closes_at is None or action.closes_at >= now)
    )


def registration_summary(action, subdomain, now=None):
    """Do not copy raw questions, answers or other people's identifiers into HA."""
    from homeassistant.util import dt as dt_util

    now = now or dt_util.now()
    opened = registration_open(action, now)
    return {
        "id": action.action_id,
        "opens_at": action.opens_at.isoformat() if action.opens_at else None,
        "open": opened,
        "needs_action": opened
        and (
            not action.selection
            or any(s.status == "reserved" for s in action.selection)
            or (action.requires_parent_confirmation and not action.parent_confirmed)
        ),
        "title": action.name[:200],
        "deadline": action.closes_at.isoformat() if action.closes_at else None,
        "question_count": sum(target.question is not None for target in action.targets),
        "selection_states": sorted(
            {selection.status for selection in action.selection}
        ),
        "parent_confirmed": action.parent_confirmed,
        "url": f"https://{subdomain}.edupage.org/signin/?"
        + urlencode({"cmd": "SigninActionPlayer", "actionid": action.action_id}),
    }


def free_slot(slot, blocks, now):
    block = blocks.get(slot.consultation_id)
    return (
        block is not None
        and slot.status == "free"
        and not slot.closed
        and slot.starts_at > now
        and (block.booking_opens_at is None or block.booking_opens_at <= now)
        and (block.booking_closes_at is None or block.booking_closes_at >= now)
    )


def relevant(block, coordinator):
    from .const import CONF_CONSULTATIONS_MY_TEACHERS

    entry = coordinator.config_entry
    filtered = (
        entry.options.get(CONF_CONSULTATIONS_MY_TEACHERS, True) if entry else True
    )
    return not filtered or block.teaches_student is True or block.is_class_teacher


def consultation_item(item, blocks):
    from datetime import timedelta

    block = blocks.get(item.consultation_id)
    return {
        "consultation_id": item.consultation_id,
        "visit_id": getattr(item, "visit_id", None),
        "start": item.starts_at.isoformat(),
        "end": (item.starts_at + timedelta(minutes=block.slot_minutes)).isoformat()
        if block
        else None,
        "slot_minutes": block.slot_minutes if block else None,
        "teacher_id": block.teacher_id if block else None,
        "teacher_name": block.teacher_name if block else None,
        "block_note": block.note[:200] if block else "",
        "booking_closes_at": block.booking_closes_at.isoformat()
        if block and block.booking_closes_at
        else None,
        "teaches_student": block.teaches_student if block else None,
        "is_class_teacher": block.is_class_teacher if block else False,
    }


def block_summaries(data, coordinator, now):
    blocks = {b.consultation_id: b for b in data["overview"].consultations}
    result = []
    for block in blocks.values():
        if (
            not relevant(block, coordinator)
            or not block.starts_at
            or (block.ends_at and block.ends_at < now)
        ):
            continue
        slots = [s for s in data["slots"] if s.consultation_id == block.consultation_id]
        result.append(
            {
                "consultation_id": block.consultation_id,
                "teacher_id": block.teacher_id,
                "teacher_name": block.teacher_name,
                "start": block.starts_at.isoformat(),
                "end": block.ends_at.isoformat() if block.ends_at else None,
                "teaches_student": block.teaches_student,
                "is_class_teacher": block.is_class_teacher,
                "booking_opens_at": block.booking_opens_at.isoformat()
                if block.booking_opens_at
                else None,
                "booking_closes_at": block.booking_closes_at.isoformat()
                if block.booking_closes_at
                else None,
                "free_count": sum(free_slot(s, blocks, now) for s in slots)
                if block.consultation_id
                in data.get("slots_loaded", [s.consultation_id for s in slots])
                else None,
            }
        )
    return sorted(
        result, key=lambda r: (r["start"], r["teacher_id"], r["consultation_id"])
    )


def available_slots(coordinator, now):
    data = coordinator.data.get("consultations")
    if not data or not coordinator.data.get("data_ok", {}).get("consultations", False):
        return []
    blocks = {b.consultation_id: b for b in data["overview"].consultations}
    return [
        s
        for s in data["slots"]
        if s.consultation_id in blocks
        and relevant(blocks[s.consultation_id], coordinator)
        and free_slot(s, blocks, now)
    ]
