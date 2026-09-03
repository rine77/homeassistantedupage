[![HACS](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/hacs/integration)
[![Validate with hassfest](https://github.com/rine77/homeassistantedupage/actions/workflows/hassfest.yml/badge.svg)](https://github.com/rine77/homeassistantedupage/actions/workflows/hassfest.yml)
[![Tests](https://github.com/rine77/homeassistantedupage/actions/workflows/tests.yml/badge.svg)](https://github.com/rine77/homeassistantedupage/actions/workflows/tests.yml)

# EduPage for Home Assistant

EduPage for Home Assistant is an unofficial custom integration for the [EduPage](https://www.edupage.org/) school information system. It imports school data into Home Assistant so it can be displayed in calendars and dashboards or used in templates, scripts, and automations.

The integration is based on the [edupage-api](https://github.com/EdupageAPI/edupage-api) Python library.

> [!IMPORTANT]
> This project is not affiliated with or supported by EduPage. The information available to Home Assistant depends on the features enabled by the school and the permissions of the EduPage account.

## Features

- Lesson calendar with upcoming and cancelled lessons
- Canteen calendar for snacks, lunches, and afternoon snacks
- One grade sensor for each subject
- Notification sensor covering all available EduPage event types
- Structured, bounded notification data for dashboards and automations
- Sensors for timetable changes and missing teachers
- Sensor showing the next school-bell time
- First- and second-term grade-average sensors
- Services for choosing, cancelling, and rating meals
- Service for sending EduPage messages
- Multiple students and EduPage accounts through separate config entries
- Modern app-code two-factor authentication
- Interactive reauthentication when a stored session expires
- English, German, and Slovak translations

## Installation

### HACS

1. Open **HACS** in Home Assistant.
2. Select **Integrations**.
3. Search for **EduPage** or **homeassistantedupage**.
4. Install the integration.
5. Restart Home Assistant when requested.

### Manual installation

1. Download this repository.
2. Copy `custom_components/homeassistantedupage` into the `custom_components` directory of your Home Assistant configuration.
3. Restart Home Assistant.

The resulting directory should look like this:

```text
config/
└── custom_components/
    └── homeassistantedupage/
        ├── __init__.py
        ├── manifest.json
        └── ...
```

## Configuration

1. Open **Settings → Devices & services** in Home Assistant.
2. Select **Add integration**.
3. Search for **EduPage**.
4. Enter the EduPage username, password, and school subdomain.
5. Complete two-factor authentication if requested.
6. Select the student whose data should be imported.

For a school URL such as `https://example.edupage.org`, enter `example` as the subdomain.

Create a separate config entry for every student you want to expose. This also applies when an account contains multiple students or when several EduPage accounts use the same school domain.

## Authentication and reauthentication

The integration supports EduPage's modern app-code two-factor authentication flow. During normal operation, Home Assistant reuses the stored PHP session and does not request a new two-factor code during every update.

Starting with version 0.4.0, newly configured and reauthenticated entries do not store the EduPage password. They store the username, subdomain, selected student, and PHP session ID required for polling. If the session expires or becomes invalid, Home Assistant starts a reauthentication flow and asks for the password again.

The PHP session ID grants access to the EduPage session and must be treated as sensitive data.

## Entities

The exact entity IDs are assigned by Home Assistant and may differ from the examples in this document.

| Entity | State | Details |
| --- | --- | --- |
| Lesson calendar | Current or next lesson | Timetable and cancelled lessons as calendar events |
| Canteen calendar | Current or next meal | Snack, lunch, and afternoon-snack events |
| Subject sensor | Number of grades | Grade details in attributes |
| Notification sensor | Number of notifications | Structured events, event counts, and legacy flat attributes |
| Timetable-changes sensor | Number of changes today | Changed class, lesson, title, and action |
| Missing-teachers sensor | Number of missing teachers today | Teacher names and person IDs |
| Next-ringing sensor | Next ringing time | Ringing type and time |
| First-term average sensor | Numeric grade average | Grade count and per-subject averages |
| Second-term average sensor | Numeric grade average | Grade count and per-subject averages |

Seeing many entities after setup is expected. EduPage exposes subjects separately, so the integration creates one grade sensor for every subject returned by the school. Some schools may also expose class-like entries as subjects.

## Calendars

### Lesson calendar

Open **Calendar** in the Home Assistant sidebar to see the imported timetable. The state of a calendar entity normally represents only the event that is active now or comes next. Other lessons remain available in the calendar.

Cancelled lessons are included and marked with a `[Canceled]` prefix.

Example dashboard card:

```yaml
type: calendar
entities:
  - calendar.edupage_example_student
initial_view: listWeek
```

Replace the example entity ID with the calendar entity created on your system.

### Retrieving calendar events in an automation

Use `calendar.get_events` when an automation needs more than the current or next event:

```yaml
action: calendar.get_events
target:
  entity_id: calendar.edupage_example_student
data:
  duration:
    hours: 24
response_variable: school_agenda
```

The response variable can then be processed by template actions, for example to determine the first lesson of the day.

See the [Home Assistant calendar documentation](https://www.home-assistant.io/integrations/calendar/) for details.

### Canteen calendar

The canteen calendar is created even when no menu is currently available. If supported by the school, it contains snack, lunch, and afternoon-snack events for the next 14 days.

A missing or empty menu therefore results in an empty calendar rather than the entity being omitted. Not every school uses the EduPage canteen feature.

## Grade sensors

The integration creates a sensor for every subject. Its state is the number of imported grades for that subject. Grade details are exposed as numbered attributes, for example:

```yaml
student:
  id: 12345
  name: Example Student
grade_1_title: Written test
grade_1_grade_n: 2
grade_1_date: "2026-08-20 08:00:00"
grade_1_teacher: Example Teacher
```

Depending on the information supplied by the school, attributes may also include maximum points, percentages, class averages, and teacher comments.

Inspect the sensor under **Developer tools → States** to see its actual entity ID and attributes.

Example template:

```jinja2
{{ state_attr('sensor.edupage_example_student_mathematics',
              'grade_1_grade_n') }}
```

### Term-average sensors

Separate sensors expose the arithmetic average for numeric grades in the first and second terms. Non-numeric grades are ignored when calculating the average. Their attributes include:

- `school_year`
- `term`
- `grade_count`
- `grade_average`
- `subject_averages`

If no numeric grades are available, the sensor reports an unknown value.

## Notifications

The notification sensor counts all notification types returned by EduPage. Its `events` attribute contains a structured list of up to 50 recent events. Each item may include:

```yaml
id: 123456
type: homework
text: Complete exercises 1–5
timestamp: "2026-09-03 10:30:00"
deadline: "2026-09-05"
subject: Mathematics
author: Example Teacher
```

The `type_counts` attribute contains the number of events grouped by type. For backward compatibility, the integration also exposes flat attributes such as `event_1_text`, `event_1_deadline`, and `event_1_subject`. These are also limited to 50 events to prevent unbounded recorder growth.

Example Markdown card:

```yaml
type: markdown
title: Latest EduPage notification
content: >-
  {% set entity = 'sensor.edupage_notification_example_student' %}
  {% set events = state_attr(entity, 'events') or [] %}
  {% if events %}
    **{{ events[0].subject or events[0].type }}**

    {{ events[0].text }}

    Deadline: {{ events[0].deadline or 'not provided' }}
  {% else %}
    No notifications available.
  {% endif %}
```

There is currently no dedicated EduPage dashboard card. Home Assistant's standard calendar, entity, Markdown, and template cards can be used instead.

## Substitution and ringing sensors

The timetable-changes sensor exposes changes reported for the current day. Its state is the number of changes, with details stored in `change_N_*` attributes.

The missing-teachers sensor reports the number of missing teachers for the current day and exposes their names and person IDs as attributes.

The next-ringing sensor shows the next available school-bell time and whether it announces a lesson or a break.

These entities may remain empty or unavailable when the school does not publish the corresponding data through EduPage.

## Services

The integration registers the following services under the `homeassistantedupage` domain:

| Service | Purpose |
| --- | --- |
| `homeassistantedupage.choose_meal` | Choose a menu for a particular date and meal type |
| `homeassistantedupage.sign_off_meal` | Cancel the selected meal |
| `homeassistantedupage.rate_meal` | Rate a meal's quantity and quality |
| `homeassistantedupage.send_message` | Send a message to students or teachers |

The services appear as actions in Home Assistant's automation and script editors.

### Choose a meal

```yaml
action: homeassistantedupage.choose_meal
data:
  date: "2026-09-04"
  meal_type: lunch
  number: 2
```

Supported meal types are `snack`, `lunch`, and `afternoon_snack`. Menu numbers range from 1 to 8.

### Sign off a meal

```yaml
action: homeassistantedupage.sign_off_meal
data:
  date: "2026-09-04"
  meal_type: lunch
```

### Rate a meal

```yaml
action: homeassistantedupage.rate_meal
data:
  date: "2026-09-04"
  meal_type: lunch
  menu_number: "2"
  quantity: 8
  quality: 9
```

Both ratings use a scale from 1 to 10. Rating is only possible when EduPage provides a rating object for that menu.

### Send a message

```yaml
action: homeassistantedupage.send_message
data:
  recipients:
    - "12345"
    - "Example Teacher"
  body: "Please bring your homework tomorrow."
```

Recipients can be specified using a numeric EduPage person ID or an exact name, matched case-insensitively. Unknown recipients cause the service call to fail rather than sending the message to an unintended person.

### Selecting an account for a service

When only one EduPage config entry is loaded, `entry_id` can be omitted. When several entries are loaded, every service call must specify the target config entry:

```yaml
action: homeassistantedupage.choose_meal
data:
  entry_id: "01J4EXAMPLEENTRYID"
  date: "2026-09-04"
  meal_type: lunch
  number: 2
```

An unknown `entry_id` is rejected. This prevents a meal or message action from accidentally being executed for another account.

## Update interval

EduPage data is fetched from the cloud approximately every 30 minutes. Timetable and canteen data are requested for the next 14 days. Changes are therefore not necessarily visible immediately.

## Troubleshooting

### No entities appear

- Restart Home Assistant after installing or updating the integration.
- Check **Settings → System → Logs** for messages containing `homeassistantedupage`.
- Confirm that the username, password, and school subdomain are correct.
- Verify that the selected account can access student data in EduPage.

### Two-factor authentication fails

- Use the current app code shown by EduPage.
- Ensure that Home Assistant can reach the school's EduPage instance.
- Start the configuration again if the challenge has expired.

### Home Assistant requests reauthentication

The stored EduPage session has expired or become invalid. Enter the password again and complete two-factor authentication if requested. The new password is used to obtain a session and is not stored in the config entry.

### Only the next calendar event is visible

This is normal Home Assistant calendar behavior. Open the Calendar panel, use a calendar card, or call `calendar.get_events` to retrieve all events for a period.

### A subject sensor has no grade attributes

The school may expose the subject without publishing grades for it. The sensor will report that no grades are available yet.

### A canteen or substitution entity is empty

The school may not use that EduPage feature, the account may not have permission to access it, or no relevant data may currently be available.

## Privacy and security

- Protect the Home Assistant configuration directory and its backups.
- Treat the stored PHP session ID like a password.
- Do not publish usernames, passwords, session IDs, student names, grades, timetable details, or message contents.
- Sanitize debug logs before attaching them to a public issue. Debug logs may contain personal school data.

Version 0.4.0 and later do not persist the password for newly configured or reauthenticated entries. Entries created by older releases may still contain a previously stored password until they are reauthenticated or recreated.

## Reporting a problem

Before opening an issue:

1. Update the integration to the latest release.
2. Restart Home Assistant.
3. Check the Home Assistant logs.
4. Search the [existing issues](https://github.com/rine77/homeassistantedupage/issues).

When creating an issue, include:

- the integration version;
- the Home Assistant version;
- a concise description of the expected and actual behavior;
- relevant, sanitized log messages;
- whether the account uses two-factor authentication;
- which EduPage features are enabled by the school.

Never post credentials, PHP session IDs, or personally identifiable school data.

## Contributing

Bug reports, documentation improvements, translations, and pull requests are welcome. Please keep pull requests focused on one topic and include tests for behavior changes where possible.

## Credits

Thanks to everyone who has contributed code, testing, translations, reports, and feedback, and to the maintainers of [edupage-api](https://github.com/EdupageAPI/edupage-api).

## License

See the repository's [license file](LICENSE) for details.
