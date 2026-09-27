"""Preserve event names omitted by edupage-api 0.12.6's timetable parser."""

from edupage_api import Edupage
from edupage_api.timetables import Timetable, Timetables


class EventNameTimetables(Timetables):
    """Keep top-level event names as well as flags.event.name."""

    def _Timetables__parse_timetable(self, plan):
        # The pinned API only reads flags.event.name, but currenttt also returns
        # events with a top-level name and no flags. Parse each item separately
        # so skipped headers cannot misalign raw entries and parsed lessons.
        lessons = []
        for item in plan:
            timetable = super()._Timetables__parse_timetable([item])
            for lesson in timetable:
                name = item.get("name")
                if (
                    lesson.is_event
                    and not lesson.curriculum
                    and isinstance(name, str)
                    and name.strip()
                ):
                    lesson.curriculum = name.strip()
                lessons.append(lesson)
        return Timetable(lessons)


class TimetableEdupage(Edupage):
    """Use the compatibility parser for both supported timetable paths."""

    def get_timetable(self, target, date):
        return EventNameTimetables(self).get_timetable(target, date)

    def get_my_timetable(self, date):
        return EventNameTimetables(self).get_my_timetable(date)
