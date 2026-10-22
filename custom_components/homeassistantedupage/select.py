"""Choose a consultation; selecting never makes a server reservation."""

from datetime import timedelta

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CONF_STUDENT_ID, CONF_STUDENT_NAME, DOMAIN
from .entity_helpers import student_device_info
from .participation import available_slots


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            ConsultationSelect(coordinator, entry, False),
            ConsultationSelect(coordinator, entry, True),
        ]
    )


class ConsultationSelect(CoordinatorEntity, SelectEntity):
    def __init__(self, coordinator, entry, bookings):
        super().__init__(coordinator)
        self.entry = entry
        self.bookings = bookings
        self._attr_translation_key = (
            "consultation_booking" if bookings else "consultation_slot"
        )
        self._attr_unique_id = (
            f"edupage_{self._attr_translation_key}_{entry.data[CONF_STUDENT_ID]}"
        )
        self._attr_has_entity_name = True
        self._attr_device_info = student_device_info(
            entry.data[CONF_STUDENT_ID], entry.data[CONF_STUDENT_NAME]
        )
        self.selected_key = None

    def _choices(self):
        data = (self.coordinator.data or {}).get("consultations")
        if not data:
            return {}
        blocks = {b.consultation_id: b for b in data["overview"].consultations}
        items = (
            [
                r
                for r in data["overview"].reservations
                if r.visit_id and r.starts_at and r.starts_at > dt_util.now()
            ]
            if self.bookings
            else available_slots(self.coordinator, dt_util.now())
        )
        result = {}
        for item in sorted(items, key=lambda r: r.starts_at):
            block = blocks.get(item.consultation_id)
            local = dt_util.as_local(item.starts_at)
            key = (
                str(item.visit_id)
                if self.bookings
                else f"{item.consultation_id}|{item.starts_at.isoformat()}"
            )
            end = dt_util.as_local(item.starts_at + timedelta(minutes=block.slot_minutes)) if block else None
            duration = f"–{end:%H:%M}" if end else ""
            # Block end also distinguishes overlapping blocks with equal slot lengths.
            block_end = f" / {dt_util.as_local(block.ends_at):%H:%M}" if block and block.ends_at else ""
            label = (
                f"{block.teacher_name if block else '?'} – {local:%d. %m. %Y %H:%M}{duration}{block_end} [{item.consultation_id}"
                + (f"/{item.visit_id}" if self.bookings else "")
                + "]"
            )
            result[label] = (key, item)
        return result

    @property
    def available(self):
        return bool(
            self.coordinator.last_update_success
            and (self.coordinator.data or {}).get("data_ok", {}).get("consultations")
        )

    @property
    def options(self):
        return list(self._choices())[:200]

    @property
    def current_option(self):
        return next(
            (
                label
                for label, (key, _) in self._choices().items()
                if key == self.selected_key
            ),
            None,
        )

    async def async_select_option(self, option):
        choices = self._choices()
        if option not in choices:
            from homeassistant.exceptions import ServiceValidationError

            raise ServiceValidationError("Consultation option no longer available")
        self.selected_key = choices[option][0]
        setattr(
            self.coordinator,
            "_selected_booking" if self.bookings else "_selected_slot",
            self.selected_key,
        )
        self.async_write_ha_state()

    def _handle_coordinator_update(self):
        keys = {key for key, _ in self._choices().values()}
        if self.selected_key not in keys:
            self.selected_key = None
            setattr(
                self.coordinator,
                "_selected_booking" if self.bookings else "_selected_slot",
                None,
            )
        super()._handle_coordinator_update()
