"""Diff fresh snapshots and schedule reads at booking/deadline boundaries."""

from datetime import timedelta

from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .const import CONF_DEADLINE_HOURS
from .participation import registration_open, registration_summary, relevant

PARTICIPATION_EVENTS = [
    "consultation_block_published",
    "consultation_booked",
    "consultation_booking_opened",
    "consultation_changed",
    "consultation_cancelled",
    "consultation_booking_closing",
    "registration_opened",
    "registration_deadline_soon",
]


def deadline_hours(coordinator):
    entry = coordinator.config_entry
    return entry.options.get(CONF_DEADLINE_HOURS, 24) if entry else 24


class ParticipationTracker:
    """No startup replay, no diff against failed/partial source data."""

    def __init__(self):
        self.blocks = None
        self.bookings = None
        self.registrations = None
        self.emitted = set()

    def _once(self, events, kind, key, attrs, enabled):
        identity = (kind, key)
        if enabled and identity not in self.emitted:
            events.append((kind, attrs))
        if enabled:
            self.emitted.add(identity)

    def update(self, coordinator, now):
        events = []
        if not coordinator.last_update_success:
            return events
        data = coordinator.data or {}
        ok = data.get("data_ok", {})
        hours = timedelta(hours=deadline_hours(coordinator))
        consultations = data.get("consultations")
        if ok.get("consultations") and consultations:
            blocks = {
                b.consultation_id: b
                for b in consultations["overview"].consultations
                if relevant(b, coordinator)
            }
            # Preserve visits until they end; a normal expiry is not a school cancellation.
            bookings = {
                r.visit_id or f"{r.consultation_id}|{r.starts_at}": r
                for r in consultations["overview"].reservations
                if r.starts_at
            }
            baseline = self.blocks is None
            for key, block in blocks.items():
                attrs = {
                    "consultation_id": key,
                    "teacher_id": block.teacher_id,
                    "teacher_name": block.teacher_name,
                    "start": block.starts_at.isoformat() if block.starts_at else None,
                }
                if (
                    not baseline
                    and key not in self.blocks
                    and (not block.ends_at or block.ends_at > now)
                ):
                    events.append(("consultation_block_published", attrs))
                opened = (
                    (block.booking_opens_at is None or block.booking_opens_at <= now)
                    and (
                        block.booking_closes_at is None or now < block.booking_closes_at
                    )
                    and (block.ends_at is None or now < block.ends_at)
                )
                opens_key = (key, str(block.booking_opens_at))
                # Seed existing open windows without emitting on startup.
                if baseline and opened:
                    self.emitted.add(("consultation_booking_opened", opens_key))
                self._once(
                    events,
                    "consultation_booking_opened",
                    opens_key,
                    attrs,
                    opened and not baseline,
                )
                teacher_booked = any(
                    blocks.get(r.consultation_id)
                    and blocks[r.consultation_id].teacher_id == block.teacher_id
                    for r in bookings.values()
                )
                near = (
                    block.booking_closes_at
                    and now < block.booking_closes_at <= now + hours
                    and not teacher_booked
                )
                close_key = (key, str(block.booking_closes_at))
                if baseline and near:
                    self.emitted.add(("consultation_booking_closing", close_key))
                self._once(
                    events,
                    "consultation_booking_closing",
                    close_key,
                    attrs,
                    near and not baseline,
                )
            if self.bookings is not None:
                for key, booking in bookings.items():
                    attrs = {
                        "visit_id": booking.visit_id,
                        "consultation_id": booking.consultation_id,
                        "start": booking.starts_at.isoformat(),
                    }
                    old = self.bookings.get(key)
                    if old and (
                        old.starts_at != booking.starts_at
                        or old.consultation_id != booking.consultation_id
                    ):
                        events.append(("consultation_changed", attrs))
                    elif not old:
                        events.append(("consultation_booked", attrs))
                cancelled = getattr(coordinator, "_ha_cancelled_visits", set())
                for key, old in self.bookings.items():
                    if (
                        key not in bookings
                        and old.starts_at > now
                        and str(old.visit_id) not in cancelled
                    ):
                        events.append(
                            (
                                "consultation_cancelled",
                                {
                                    "visit_id": old.visit_id,
                                    "consultation_id": old.consultation_id,
                                    "start": old.starts_at.isoformat(),
                                    "source": "outside_home_assistant",
                                },
                            )
                        )
                coordinator._ha_cancelled_visits = set()
            self.blocks, self.bookings = blocks, bookings
        if ok.get("registrations") and data.get("registrations") is not None:
            current = {a.action_id: a for a in data["registrations"]}
            baseline = self.registrations is None
            for key, action in current.items():
                summary = registration_summary(
                    action, coordinator.config_entry.data.get("subdomain", ""), now
                )
                opened = registration_open(action, now)
                identity = (key, str(action.opens_at))
                if baseline and opened:
                    self.emitted.add(("registration_opened", identity))
                self._once(
                    events,
                    "registration_opened",
                    identity,
                    summary,
                    opened and not baseline,
                )
                near = (
                    summary["needs_action"]
                    and action.closes_at
                    and now < action.closes_at <= now + hours
                )
                identity = (key, str(action.closes_at))
                if baseline and near:
                    self.emitted.add(("registration_deadline_soon", identity))
                self._once(
                    events,
                    "registration_deadline_soon",
                    identity,
                    summary,
                    near and not baseline,
                )
            self.registrations = current
        return events


def setup_refresh_schedule(coordinator, entry):
    """Refresh at opening/threshold boundaries; keep legacy sources at <=30 min."""
    cancel_timer = None
    stopped = False
    last_good = {}

    async def refresh(_now):
        if not stopped:
            await coordinator.async_request_refresh()

    def schedule():
        nonlocal cancel_timer
        if stopped or not coordinator.last_update_success:
            return
        if cancel_timer:
            cancel_timer()
            cancel_timer = None
        now = dt_util.now()
        points = []
        active = False
        data = coordinator.data or {}
        for section in ("consultations", "registrations"):
            if data.get("data_ok", {}).get(section) and data.get(section) is not None:
                last_good[section] = data[section]
        consultations = last_good.get("consultations")
        delta = timedelta(hours=deadline_hours(coordinator))
        if consultations:
            for block in consultations["overview"].consultations:
                if not relevant(block, coordinator):
                    continue
                points += [
                    block.booking_opens_at,
                    block.booking_closes_at - delta
                    if block.booking_closes_at
                    else None,
                ]
                active |= (
                    (not block.booking_opens_at or block.booking_opens_at <= now)
                    and (not block.booking_closes_at or now < block.booking_closes_at)
                    and (not block.ends_at or now < block.ends_at)
                )
        if "registrations" in last_good:
            for action in last_good["registrations"]:
                points += [
                    action.opens_at,
                    action.closes_at - delta if action.closes_at else None,
                ]
        # Do not slow existing timetable/grade polling to several hours.
        coordinator.update_interval = timedelta(minutes=10 if active else 30)
        future = [p for p in points if p and p > now + timedelta(seconds=1)]
        if future:
            cancel_timer = async_track_point_in_utc_time(
                coordinator.hass, refresh, min(future)
            )

    remove_listener = coordinator.async_add_listener(schedule)

    def stop():
        nonlocal stopped
        stopped = True
        remove_listener()
        if cancel_timer:
            cancel_timer()

    entry.async_on_unload(stop)
    schedule()
