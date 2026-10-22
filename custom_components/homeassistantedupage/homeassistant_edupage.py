import logging

from edupage_api import Edupage as APIEdupage
from edupage_api import LoginSession
from edupage_api.exceptions import (
    BadCredentialsException,
    ConsultationError,
    InsufficientPermissionsException,
    NotParentException,
)

from homeassistant.helpers.update_coordinator import UpdateFailed

_LOGGER = logging.getLogger(__name__)


class EdupageSessionExpired(UpdateFailed):
    """Raised when the stored PHPSESSID is invalid/expired.

    Subclass of UpdateFailed so the DataUpdateCoordinator still treats it as a
    failed update, but it can be caught separately in __init__ to trigger
    Home Assistant's reauthentication flow.
    """


class Edupage:
    """Async wrapper around the edupage-api library.

    Runtime polling is deliberately session-only: a fresh PHPSESSID obtained
    during the config flow (after 2FA) is reused via `LoginSession.reload_data`.
    We never call `api.login()` at runtime so a 2FA prompt is never re-triggered
    by the 30-minute coordinator poll. If the stored session turns out to be
    expired we signal that with EdupageSessionExpired so the integration can
    start the reauth flow.
    """

    def __init__(self, hass, sessionid=""):
        self.hass = hass
        self.sessionid = sessionid
        self.api = APIEdupage()

    def _load_session(self, subdomain, sessionid, username):
        """Reload a stored EduPage session synchronously."""
        login = LoginSession(self.api)
        login.reload_data(subdomain, sessionid, username)

    async def login(self, username, subdomain, sessionid):
        """Load the stored session. Never starts username/password login."""
        self.sessionid = sessionid
        try:
            await self.hass.async_add_executor_job(
                self._load_session, subdomain, sessionid, username
            )
            if not self.api.is_logged_in:
                _LOGGER.error(
                    "EDUPAGE stored session is invalid or expired; "
                    "re-authenticate to refresh it."
                )
                raise EdupageSessionExpired(
                    "EduPage session expired; please re-authenticate."
                )
            _LOGGER.debug("EDUPAGE session loaded for %s@%s", username, subdomain)
            return True
        except EdupageSessionExpired:
            # Our own expiration signal must pass through untouched so the
            # integration can start the reauthentication flow. Do not let the
            # broad handler below convert it into a generic UpdateFailed.
            raise
        except BadCredentialsException as e:
            _LOGGER.error(
                "EDUPAGE stored session is invalid or expired: %s. "
                "Re-authenticate to refresh the session.",
                e,
            )
            raise EdupageSessionExpired(
                "EduPage session invalid/expired; please re-authenticate the "
                "integration to refresh it."
            ) from e
        except IndexError as e:
            # LoginSession.reload_data() raises IndexError on a real
            # installation when the stored session is expired
            # (homeassistantedupage#70). Treat it
            # as an expired session so the reauthentication flow starts again.
            _LOGGER.error(
                "EDUPAGE stored session could not be reloaded (expired): %s. "
                "Re-authenticate to refresh the session.",
                e,
            )
            raise EdupageSessionExpired(
                "EduPage session invalid/expired; please re-authenticate the "
                "integration to refresh it."
            ) from e
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("EDUPAGE unexpected session-load error: %s", e)
            raise UpdateFailed(f"EduPage session load failed: {e}") from e

    async def get_classes(self):
        try:
            return await self.hass.async_add_executor_job(self.api.get_classes)
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(f"EDUPAGE error updating get_classes() data from API: {e}")

    def _grade_read(self, student, method, *args):
        """Grade endpoints implicitly use the server's selected child."""
        switched = False
        try:
            if student is not None:
                try:
                    self.api.switch_to_child(student)
                    switched = True
                except NotParentException:
                    pass
            result = getattr(self.api, method)(*args)
            report = getattr(self.api, "grade_read_report", None)
            if isinstance(report, dict):
                if not hasattr(self, "grade_reports"):
                    self.grade_reports = {}
                key = "grades" if method == "get_grades" else f"term_{args[1].value}"
                self.grade_reports[key] = dict(report)
            return result
        finally:
            if switched:
                self.api.switch_to_parent()

    def _log_grade_failure(self, operation, student, error):
        """Record the concrete cause without cookies, login identity or payloads."""
        from importlib.metadata import PackageNotFoundError, version
        from pathlib import PurePath
        try:
            library_version = version("edupage-api")
        except PackageNotFoundError:
            library_version = "source checkout"
        secrets = [self.sessionid, getattr(self.api, "username", None)]
        chain = []
        seen = set()
        root = error
        while root is not None and id(root) not in seen and len(chain) < 4:
            seen.add(id(root))
            message = str(root)
            for secret in secrets:
                if isinstance(secret, str) and secret:
                    message = message.replace(secret, "[redacted]")
            chain.append(f"{type(root).__name__}: {message[:512]}")
            next_error = root.__cause__ or root.__context__
            if next_error is None:
                break
            root = next_error
        trace = root.__traceback__ if root else None
        while trace and trace.tb_next:
            trace = trace.tb_next
        origin = (f"{PurePath(trace.tb_frame.f_code.co_filename).name}:"
                  f"{trace.tb_lineno} {trace.tb_frame.f_code.co_name}") if trace else "unknown"
        summary = {"library_version": library_version, "cause": " <- ".join(chain), "origin": origin}
        if not hasattr(self, "grade_errors"):
            self.grade_errors = {}
        self.grade_errors[operation] = summary
        _LOGGER.warning("EduPage %s failed (student=%s, library=%s): %s; origin=%s",
            operation, getattr(student, "person_id", "current"), library_version, summary["cause"], origin)

    async def get_grades(self, student=None):
        try:
            result = await self.hass.async_add_executor_job(self._grade_read, student, "get_grades")
            getattr(self, "grade_errors", {}).pop("get_grades", None)
            return result
        except Exception as exc:
            await self.hass.async_add_executor_job(self._log_grade_failure, "get_grades", student, exc)
            raise UpdateFailed("EduPage get_grades failed; see the detailed grade diagnostic") from exc

    def _read_participation(self, student, method, timezone_name, params=None):
        """Keep each read in the selected child's context, restoring the parent."""
        switched = False
        try:
            try:
                self.api.switch_to_child(student)
                switched = True
            except NotParentException:
                pass
            if method in ("book_consultation_slot", "cancel_consultation"):
                params = dict(params or {})
                require_related = params.pop("require_related_teacher", False)
                if method == "book_consultation_slot" and require_related:
                    # Read in THIS child's context, not from another entry's cache.
                    current = self.api.get_consultations(timezone_name)
                    block = next((b for b in current.consultations
                                  if b.consultation_id == str(params["consultation_id"])), None)
                    related = block is not None and block.teaches_student is True
                    if block is not None and not related:
                        try:
                            related = any(
                                abs(int(c.class_id)) == abs(int(student.class_id))
                                and any(str(t.person_id).lstrip("-") == block.teacher_id.lstrip("-")
                                        for t in (c.homeroom_teachers or []))
                                for c in self.api.get_classes() or [])
                        except Exception:
                            related = False
                    if not related:
                        error = ConsultationError("Teacher is not verified for the selected child")
                        error.code = "unrelated_teacher"
                        raise error
                return getattr(self.api, method)(**params, timezone_name=timezone_name)
            if method == "registrations":
                return self.api.get_registrations(timezone_name)
            overview = self.api.get_consultations(timezone_name)
            from datetime import datetime, timezone
            # Cached school directories are read while the selected child is active.
            try:
                class_id = abs(int(student.class_id))
                classes = self.api.get_classes() or []
                teachers = {str(t.person_id).lstrip("-") for c in classes
                            if abs(int(c.class_id)) == class_id
                            for t in (c.homeroom_teachers or [])}
            except Exception:  # Optional directory enrichment must not discard slots.
                teachers = set()
            now = datetime.now(timezone.utc)
            slots = []
            loaded = []
            # Different blocks may overlap: their IDs must remain distinct.
            for block in overview.consultations:
                block.is_class_teacher = block.teacher_id.lstrip("-") in teachers
                if block.is_class_teacher:
                    block.teaches_student = True
                # No availability requests for past blocks or closed/not-yet-open windows.
                if ((block.ends_at and block.ends_at <= now)
                        or (block.booking_opens_at and block.booking_opens_at > now)
                        or (block.booking_closes_at and block.booking_closes_at < now)):
                    continue
                slots.extend(self.api.get_consultation_slots(block.consultation_id, timezone_name))
                loaded.append(block.consultation_id)
            return {"overview": overview, "slots": slots, "slots_loaded": loaded}
        finally:
            if switched:
                self.api.switch_to_parent()

    async def get_registrations(self, student):
        """Read assigned actions/surveys without submitting responses."""
        timezone_name = getattr(getattr(self.hass, "config", None), "time_zone", "Europe/Prague")
        try:
            return await self.hass.async_add_executor_job(
                self._read_participation, student, "registrations", timezone_name
            )
        except Exception as exc:
            raise UpdateFailed("EduPage registrations could not be read") from exc

    async def get_consultations(self, student):
        """Read offered slots and own bookings without making reservations."""
        timezone_name = getattr(getattr(self.hass, "config", None), "time_zone", "Europe/Prague")
        try:
            return await self.hass.async_add_executor_job(
                self._read_participation, student, "consultations", timezone_name
            )
        except Exception as exc:
            raise UpdateFailed("EduPage consultations could not be read") from exc

    async def consultation_action(self, student, method, **params):
        """Explicit actions only; caller holds the same lock used by polling."""
        timezone_name = getattr(getattr(self.hass, "config", None), "time_zone", "Europe/Prague")
        return await self.hass.async_add_executor_job(
            self._read_participation, student, method, timezone_name, params)

    def _get_text_grades(self, student):
        """Read in the selected child's context and restore the parent."""
        try:
            self.api.switch_to_child(student)
        except NotParentException:
            return self.api.get_text_grades()
        try:
            return self.api.get_text_grades()
        finally:
            self.api.switch_to_parent()

    async def get_text_grades(self, student):
        """Fetch textual assessments through the official API."""
        try:
            return await self.hass.async_add_executor_job(self._get_text_grades, student)
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed("EDUPAGE error updating text assessments") from e

    async def get_subjects(self):
        try:
            return await self.hass.async_add_executor_job(self.api.get_subjects)
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(f"EDUPAGE error updating get_subjects() data from API: {e}")

    async def get_notifications(self):
        try:
            notifications = await self.hass.async_add_executor_job(
                self.api.get_notifications
            )
            _LOGGER.debug("EDUPAGE Notifications found %s", notifications)
            return notifications
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_notifications() data from API: {e}"
            )

    async def get_students(self):
        try:
            return await self.hass.async_add_executor_job(self.api.get_students)
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_students() data from API: {e}"
            )

    async def get_user_id(self):
        try:
            return await self.hass.async_add_executor_job(self.api.get_user_id)
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_user_id() data from API: {e}"
            )

    async def get_classrooms(self):
        try:
            return await self.hass.async_add_executor_job(self.api.get_classrooms)
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_classrooms data from API: {e}"
            )

    async def get_teachers(self):
        try:
            return await self.hass.async_add_executor_job(self.api.get_teachers)
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_teachers data from API: {e}"
            )

    def _get_timetable(self, edu_student, day):
        """Get a timetable, falling back to the parent child-switch flow."""
        try:
            return self.api.get_timetable(edu_student, day)
        except InsufficientPermissionsException:
            _LOGGER.debug(
                "EDUPAGE direct timetable access was rejected for %s; "
                "retrying in the selected child context",
                day,
            )
            self.api.switch_to_child(edu_student)
            try:
                return self.api.get_my_timetable(day)
            finally:
                self.api.switch_to_parent()

    async def get_timetable(self, edu_student, day):
        try:
            timetable = await self.hass.async_add_executor_job(
                self._get_timetable, edu_student, day
            )
            if timetable is None:
                _LOGGER.debug("EDUPAGE timetable is None for %s", day)
            else:
                _LOGGER.debug("EDUPAGE timetable_data for %s: %s", day, timetable)
            return timetable
        except Exception as e:  # noqa: BLE001
            _LOGGER.error(
                "EDUPAGE error updating get_timetable() data for %s: %s", day, e
            )
            raise UpdateFailed(
                f"EDUPAGE error updating get_timetable() data for {day}: {e}"
            )

    async def get_meals(self, day):
        try:
            return await self.hass.async_add_executor_job(
                self.api.get_meals, day
            )
        except (IndexError, AttributeError) as e:
            _LOGGER.debug(
                "EDUPAGE get_meals returned empty or unsupported data for %s: %s",
                day,
                e,
            )
            return None
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_meals() data for {day}: {e}"
            ) from e

    async def get_timetable_changes(self, day):
        try:
            return await self.hass.async_add_executor_job(
                self.api.get_timetable_changes, day
            )
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_timetable_changes() data for {day}: {e}"
            )

    async def get_missing_teachers(self, day):
        try:
            result = await self.hass.async_add_executor_job(
                self.api.get_missing_teachers, day
            )
            return result or []
        except IndexError:
            _LOGGER.debug(
                "EDUPAGE get_missing_teachers returned invalid/empty data for %s",
                day,
            )
            return []
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_missing_teachers() data for {day}: {e}"
            ) from e

    async def get_next_ringing_time(self, day_time):
        try:
            return await self.hass.async_add_executor_job(
                self.api.get_next_ringing_time, day_time
            )
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_next_ringing_time() data from API: {e}"
            )

    async def get_school_year(self) -> int:
        """Returns the current school year."""
        try:
            return await self.hass.async_add_executor_job(self.api.get_school_year)
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_school_year() data from API: {e}"
            )

    async def get_grades_for_term(self, year: int, term, student=None):
        operation = f"get_grades_for_term:{year}:{getattr(term, 'value', term)}"
        try:
            result = await self.hass.async_add_executor_job(
                self._grade_read, student, "get_grades_for_term", year, term)
            getattr(self, "grade_errors", {}).pop(operation, None)
            return result
        except Exception as exc:
            await self.hass.async_add_executor_job(self._log_grade_failure, operation, student, exc)
            raise UpdateFailed("EduPage get_grades_for_term failed; see the detailed grade diagnostic") from exc

    # ------------------------------------------------------------------
    # Action methods used by services
    # ------------------------------------------------------------------

    async def choose_meal(self, meal, number: int):
        try:
            await self.hass.async_add_executor_job(meal.choose, self.api, number)
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(f"EDUPAGE error choosing meal: {e}")

    async def sign_off_meal(self, meal):
        try:
            await self.hass.async_add_executor_job(meal.sign_off, self.api)
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(f"EDUPAGE error signing off meal: {e}")

    async def rate_meal(self, rating, quantity: int, quality: int):
        try:
            await self.hass.async_add_executor_job(
                rating.rate, self.api, quantity, quality
            )
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(f"EDUPAGE error rating meal: {e}")

    async def resolve_recipients(self, recipients):
        """Resolve recipient strings to EduPage account objects.

        ``edupage-api``'s ``send_message`` expects account objects (or their
        ``get_id()`` strings like ``s123``/``u456``). A free-text name would be
        sent verbatim as ``selectedUser`` and fail against EduPage. We therefore
        match each recipient against the known students and teachers (by numeric
        ``person_id`` or by case-insensitive name) and raise if one cannot be
        resolved.
        """
        names = []
        ids = []
        for raw in recipients:
            raw = str(raw).strip()
            if not raw:
                continue
            if raw.isdigit():
                ids.append(raw)
            else:
                names.append(raw)
        if not names and not ids:
            raise UpdateFailed("EduPage send_message: no recipients given")

        resolved = []
        unmatched = list(names)
        for account_list in (await self.get_students(), await self.get_teachers()):
            for account in account_list or []:
                needle = str(account.person_id)
                if needle in ids:
                    resolved.append(account)
                    ids.remove(needle)
                elif account.name and account.name.lower() in [n.lower() for n in unmatched]:
                    resolved.append(account)
                    matched_name = next(n for n in unmatched if n.lower() == account.name.lower())
                    unmatched.remove(matched_name)

        if ids:
            raise UpdateFailed(
                f"EduPage send_message: could not resolve recipient id(s): {', '.join(ids)}"
            )
        if unmatched:
            raise UpdateFailed(
                f"EduPage send_message: could not resolve recipient(s): {', '.join(unmatched)}"
            )
        return resolved

    async def send_message(self, recipients, body: str):
        try:
            accounts = await self.resolve_recipients(recipients)
            # edupage-api sends the first ``selectedUser`` using the exact
            # ``EduAccount`` type check; student/teacher subclasses are not
            # accepted and would be string-joined, so we pass the resolved
            # recipient IDs (e.g. ``s123``/``u456``) instead of the objects.
            recipient_ids = [account.get_id() for account in accounts]
            return await self.hass.async_add_executor_job(
                self.api.send_message, recipient_ids, body
            )
        except UpdateFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(f"EDUPAGE error sending message: {e}")
