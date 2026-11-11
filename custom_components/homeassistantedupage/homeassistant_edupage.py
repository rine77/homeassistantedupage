import logging
from requests.exceptions import RequestException
from contextlib import contextmanager
from urllib.parse import urlsplit

from edupage_api import Edupage as APIEdupage
from edupage_api import LoginSession
from edupage_api.exceptions import (
    BadCredentialsException,
    ExpiredSessionException,
    NotLoggedInException,
    MissingDataException,
    InsufficientPermissionsException,
    NotParentException,
)

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from .session_tasks import finish_before_cancel

_LOGGER = logging.getLogger(__name__)


class EdupageSessionExpired(ConfigEntryAuthFailed):
    """Stored session needs Home Assistant reauthentication."""


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
        self.auth_failed = False
        self.context_invalid = False

    async def _async_executor(self, function, *args):
        """Do not leave a shared-session operation running after cancellation."""
        if getattr(self, "auth_failed", False):
            raise EdupageSessionExpired("EduPage session needs reauthentication")
        if getattr(self, "context_invalid", False) and function not in (self._load_session, self._log_grade_failure):
            raise UpdateFailed("EduPage account context needs recovery before further requests")
        try:
            return await finish_before_cancel(
                self.hass.async_add_executor_job(function, *args)
            )
        except (ExpiredSessionException, NotLoggedInException, BadCredentialsException) as exc:
            operation = getattr(function, "__name__", "unknown")
            if operation in ("_read_participation", "_grade_read") and len(args) > 1:
                operation = str(args[1])
            if function != self._load_session:
                try:
                    state = await finish_before_cancel(
                        self.hass.async_add_executor_job(self._verify_account_session)
                    )
                except Exception:
                    state = None
                if state is not False:
                    status = "valid" if state is True else "unconfirmed"
                    raise UpdateFailed(
                        f"EduPage {operation} signalled {type(exc).__name__}; "
                        f"main account session is {status}, reauthentication not requested"
                    ) from None
            self.auth_failed = True
            raise EdupageSessionExpired(
                f"EduPage session needs reauthentication ({operation}: {type(exc).__name__})"
            ) from None

    def _verify_account_session(self):
        """Read /user with current cookies; never mutate the cached child context.

        True: authenticated bootstrap; False: explicit login redirect/HTTP 401;
        None: network/module/schema ambiguity. No response payload is logged.
        """
        response = self.api.session.get(f"https://{self.api.subdomain}.edupage.org/user/")
        path = urlsplit(response.url).path.rstrip("/")
        if response.status_code == 401 or path in ("/login", "/login/index.php") or "EdupageLoginFailed" in response.url:
            return False
        if response.status_code != 200:
            return None
        probe = APIEdupage()
        probe.session.close()
        try:
            LoginSession(probe).parse_login_data(response.text)
        except Exception:
            return None
        return isinstance(probe.data, dict) and bool(probe.data.get("userid")) or None

    def _load_session(self, subdomain, sessionid, username):
        """Reload a stored EduPage session synchronously."""
        _LOGGER.debug(
            "Session reload start entry_id=%s student_id=%s context_invalid=%s",
            getattr(self, "entry_id", None), getattr(self, "student_id", None), getattr(self, "context_invalid", False),
        )
        login = LoginSession(self.api)
        login.reload_data(subdomain, sessionid, username)
        _LOGGER.debug(
            "Session reload succeeded entry_id=%s student_id=%s context_invalid=%s gsec_hash_present=%s",
            getattr(self, "entry_id", None), getattr(self, "student_id", None), getattr(self, "context_invalid", False),
            bool(getattr(self.api, "gsec_hash", None)),
        )
        # Selecting a child does not leave the parent account. A successful
        # authenticated bootstrap recovers the session without an account switch.
        self.api._selected_child_id = None
        self.context_invalid = False

    async def logout(self):
        """Invalidate the session on the EduPage server.

        Only safe once the entry is being removed: the stored PHPSESSID is the
        integration's credential, so logging out while the entry is still loaded
        would force a full reauthentication on the next poll.
        """
        await self.hass.async_add_executor_job(self.api.logout)
        self.sessionid = ""

    async def login(self, username, subdomain, sessionid):
        """Load the stored session. Never starts username/password login."""
        self.sessionid = sessionid
        try:
            await self._async_executor(
                self._load_session, subdomain, sessionid, username
            )
            if not self.api.is_logged_in:
                self.auth_failed = True
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
            self.auth_failed = True
            # Our own expiration signal must pass through untouched so the
            # integration can start the reauthentication flow. Do not let the
            # broad handler below convert it into a generic UpdateFailed.
            raise
        except BadCredentialsException as e:
            self.auth_failed = True
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
            self.auth_failed = True
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
        except ConfigEntryAuthFailed:
            raise
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("EDUPAGE unexpected session-load error: %s", e)
            raise UpdateFailed(f"EduPage session load failed: {e}") from e

    async def get_classes(self):
        try:
            return await self._async_executor(self.api.get_classes)
        except ConfigEntryAuthFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(f"EDUPAGE error updating get_classes() data from API: {e}")

    @contextmanager
    def _child_context(self, student, operation="unknown"):
        """Select the target child inside the parent account for each operation.

        The caller holds the session lock until the executor finishes. Never
        restore via edupageChange: that switches accounts, not child selection.
        """
        if student is not None:
            try:
                self.api.switch_to_child(student)
            except NotParentException:
                pass
            except Exception:
                # A failed selection must never continue with the previous child.
                self.context_invalid = True
                raise
        yield


    def _grade_read(self, student, method, *args):
        """Grade endpoints implicitly use the server's selected child."""
        with self._child_context(student):
            result = getattr(self.api, method)(*args)
            report = getattr(self.api, "grade_read_report", None)
            if isinstance(report, dict):
                if not hasattr(self, "grade_reports"):
                    self.grade_reports = {}
                key = "grades" if method == "get_grades" else f"term_{args[1].value}"
                self.grade_reports[key] = dict(report)
            return result

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
            next_error = root.__cause__
            if next_error is None and not root.__suppress_context__:
                next_error = root.__context__
            if next_error is None:
                break
            root = next_error
        trace = root.__traceback__ if root else None
        frames = []
        while trace:
            frames.append(
                f"{PurePath(trace.tb_frame.f_code.co_filename).name}:"
                f"{trace.tb_lineno} {trace.tb_frame.f_code.co_name}"
            )
            trace = trace.tb_next
        origin = " <- ".join(frames[-8:]) or "unknown"
        summary = {"library_version": library_version, "cause": " <- ".join(chain), "origin": origin}
        if not hasattr(self, "grade_errors"):
            self.grade_errors = {}
        self.grade_errors[operation] = summary
        _LOGGER.warning("EduPage %s failed (student=%s, library=%s): %s; origin=%s",
            operation, getattr(student, "person_id", "current"), library_version, summary["cause"], origin)

    async def get_grades(self, student=None):
        try:
            result = await self._async_executor(self._grade_read, student, "get_grades")
            getattr(self, "grade_errors", {}).pop("get_grades", None)
            return result
        except ConfigEntryAuthFailed:
            raise
        except Exception as exc:
            await self._async_executor(self._log_grade_failure, "get_grades", student, exc)
            raise UpdateFailed("EduPage get_grades failed; see the detailed grade diagnostic") from None





    def _get_text_grades(self, student):
        """Read in the selected child's context and restore the parent."""
        with self._child_context(student):
            return self.api.get_text_grades()

    async def get_text_grades(self, student):
        """Fetch textual assessments through the official API."""
        try:
            return await self._async_executor(self._get_text_grades, student)
        except ConfigEntryAuthFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed("EDUPAGE error updating text assessments") from e

    async def get_subjects(self):
        try:
            return await self._async_executor(self.api.get_subjects)
        except ConfigEntryAuthFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(f"EDUPAGE error updating get_subjects() data from API: {e}")

    async def get_notifications(self):
        try:
            notifications = await self._async_executor(
                self.api.get_notifications
            )
            _LOGGER.debug("EDUPAGE Notifications found %s", notifications)
            return notifications
        except ConfigEntryAuthFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_notifications() data from API: {e}"
            )

    async def get_students(self):
        try:
            return await self._async_executor(self.api.get_students)
        except ConfigEntryAuthFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_students() data from API: {e}"
            )

    async def get_user_id(self):
        try:
            return await self._async_executor(self.api.get_user_id)
        except ConfigEntryAuthFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_user_id() data from API: {e}"
            )

    async def get_classrooms(self):
        try:
            return await self._async_executor(self.api.get_classrooms)
        except ConfigEntryAuthFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_classrooms data from API: {e}"
            )

    async def get_teachers(self):
        try:
            return await self._async_executor(self.api.get_teachers)
        except ConfigEntryAuthFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_teachers data from API: {e}"
            )

    def _get_timetable(self, edu_student, day):
        """Get a timetable, falling back to the parent child-switch flow."""
        try:
            return self.api.get_timetable(edu_student, day)
        except (InsufficientPermissionsException, MissingDataException):
            _LOGGER.debug(
                "EDUPAGE direct timetable data unavailable for %s; "
                "reading the verified child context once",
                day,
            )
            with self._child_context(edu_student):
                return self.api.get_my_timetable(day)

    async def get_timetable(self, edu_student, day):
        try:
            timetable = await self._async_executor(
                self._get_timetable, edu_student, day
            )
            if timetable is None:
                _LOGGER.debug("EDUPAGE timetable is None for %s", day)
            else:
                _LOGGER.debug("EDUPAGE timetable_data for %s: %s", day, timetable)
            return timetable
        except ConfigEntryAuthFailed:
            raise
        except Exception as e:  # noqa: BLE001
            _LOGGER.error(
                "EDUPAGE error updating get_timetable() data for %s: %s", day, e
            )
            raise UpdateFailed(
                f"EDUPAGE error updating get_timetable() data for {day}: {e}"
            )

    async def get_meals(self, day):
        try:
            return await self._async_executor(
                self.api.get_meals, day
            )
        except (IndexError, AttributeError) as e:
            _LOGGER.debug(
                "EDUPAGE get_meals returned empty or unsupported data for %s: %s",
                day,
                e,
            )
            return None
        except ConfigEntryAuthFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_meals() data for {day}: {e}"
            ) from e

    async def get_timetable_changes(self, day):
        try:
            return await self._async_executor(
                self.api.get_timetable_changes, day
            )
        except ConfigEntryAuthFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_timetable_changes() data for {day}: {e}"
            )

    async def get_missing_teachers(self, day):
        try:
            result = await self._async_executor(
                self.api.get_missing_teachers, day
            )
            return result or []
        except IndexError:
            _LOGGER.debug(
                "EDUPAGE get_missing_teachers returned invalid/empty data for %s",
                day,
            )
            return []
        except ConfigEntryAuthFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_missing_teachers() data for {day}: {e}"
            ) from e

    async def get_next_ringing_time(self, day_time):
        try:
            return await self._async_executor(
                self.api.get_next_ringing_time, day_time
            )
        except ConfigEntryAuthFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_next_ringing_time() data from API: {e}"
            )

    async def get_school_year(self) -> int:
        """Returns the current school year."""
        try:
            return await self._async_executor(self.api.get_school_year)
        except ConfigEntryAuthFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(
                f"EDUPAGE error updating get_school_year() data from API: {e}"
            )

    async def get_grades_for_term(self, year: int, term, student=None):
        operation = f"get_grades_for_term:{year}:{getattr(term, 'value', term)}"
        try:
            result = await self._async_executor(
                self._grade_read, student, "get_grades_for_term", year, term)
            getattr(self, "grade_errors", {}).pop(operation, None)
            return result
        except ConfigEntryAuthFailed:
            raise
        except Exception as exc:
            await self._async_executor(self._log_grade_failure, operation, student, exc)
            raise UpdateFailed("EduPage get_grades_for_term failed; see the detailed grade diagnostic") from None

    # ------------------------------------------------------------------
    # Action methods used by services
    # ------------------------------------------------------------------

    async def choose_meal(self, meal, number: int):
        try:
            await self._async_executor(meal.choose, self.api, number)
        except ConfigEntryAuthFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(f"EDUPAGE error choosing meal: {e}")

    async def sign_off_meal(self, meal):
        try:
            await self._async_executor(meal.sign_off, self.api)
        except ConfigEntryAuthFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(f"EDUPAGE error signing off meal: {e}")

    async def rate_meal(self, rating, quantity: int, quality: int):
        try:
            await self._async_executor(
                rating.rate, self.api, quantity, quality
            )
        except ConfigEntryAuthFailed:
            raise
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
            return await self._async_executor(
                self.api.send_message, recipient_ids, body
            )
        except UpdateFailed:
            raise
        except ConfigEntryAuthFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(f"EDUPAGE error sending message: {e}")
