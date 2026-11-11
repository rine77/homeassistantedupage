"""Buttons perform writes only when explicitly pressed by the user."""

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_STUDENT_ID, CONF_STUDENT_NAME, DOMAIN
from .consultation_services import perform_action
from .entity_helpers import student_device_info


async def async_setup_entry(hass, entry, async_add_entities):
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [ConsultationButton(c, entry, "book"), ConsultationButton(c, entry, "cancel")]
    )


class ConsultationButton(CoordinatorEntity, ButtonEntity):
    def __init__(self, coordinator, entry, action):
        super().__init__(coordinator)
        self.entry, self.action = entry, action
        self._attr_unique_id = (
            f"edupage_consultation_{action}_{entry.data[CONF_STUDENT_ID]}"
        )
        self._attr_has_entity_name = True
        self._attr_device_info = student_device_info(
            entry.data[CONF_STUDENT_ID], entry.data[CONF_STUDENT_NAME]
        )
        self._attr_translation_key = (
            "book_consultation" if action == "book" else "cancel_consultation"
        )

    @property
    def available(self):
        key = "_selected_slot" if self.action == "book" else "_selected_booking"
        return bool(
            self.coordinator.last_update_success
            and (self.coordinator.data or {}).get("data_ok", {}).get("consultations")
            and getattr(self.coordinator, key, None)
        )

    async def async_press(self):
        key = "_selected_slot" if self.action == "book" else "_selected_booking"
        selected = getattr(self.coordinator, key, None)
        if not selected:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="select_consultation"
            )
        params = {}
        if self.action == "book":
            consultation_id, start = selected.split("|", 1)
            params.update(consultation_id=consultation_id, start=start)
        else:
            params["visit_id"] = selected
        await perform_action(
            self.hass, self.entry.entry_id, self.action, dry_run=False, **params
        )
        setattr(self.coordinator, key, None)
        self.async_write_ha_state()
