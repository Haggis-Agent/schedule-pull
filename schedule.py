#!/usr/bin/env python3

import os
import requests
from icalendar import Calendar, Event, Alarm
from datetime import datetime, timedelta

ICS_FILENAME = "concert_schedule.ics"
FEED_URL = "https://aegwebprod.blob.core.windows.net/json/events/51/events.json"

def parse_utc_string(dt_str: str) -> datetime:
    """
    Convert an ISO8601 UTC string (e.g. '2025-01-31T19:00:00Z') into a Python datetime object in UTC.
    Python's datetime.fromisoformat doesn't handle 'Z' by default, so we replace 'Z' with '+00:00'.
    """
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

def dt_to_utc_ical_string(dt_utc: datetime) -> str:
    """
    Convert a Python datetime (assumed to be UTC) into the iCalendar-friendly string 'YYYYMMDDTHHMMSSZ'.
    """
    return dt_utc.strftime("%Y%m%dT%H%M%SZ")

def format_time_no_leading_zero(dt: datetime) -> str:
    """
    Return a 12-hour clock string like '8:00 PM' with:
      - No leading zero on the hour
      - Zero-padded minutes
      - AM/PM
    (Note: This is only used in DESCRIPTION for user display; the alarms themselves use UTC triggers.)
    """
    hour_12 = dt.hour % 12
    if hour_12 == 0:
        hour_12 = 12
    minute_str = f"{dt.minute:02d}"
    ampm_str = dt.strftime("%p")  # 'AM' or 'PM'
    return f"{hour_12}:{minute_str} {ampm_str}"

def create_or_update_ical_event(event_data, ical_event=None):
    """
    Create or update an all-day VEVENT in .ics.
    - Doors/Show times are stored in UTC fields (doorDateTimeUTC, eventDateTimeUTC).
    - We build absolute-time VALARMs (30 min before doors, 15 min before show).
    - For the event's DESCRIPTION lines, we still display local-like times (if you want),
      but you have the UT Cfields. Here we keep the "no leading zero" function for consistency.

    If doorDateTimeUTC or eventDateTimeUTC are missing, we skip the corresponding alarm.
    """

    fromiso_utc = parse_utc_string  # convenience

    # If no existing VEVENT is provided, create a new one
    if ical_event is None:
        ical_event = Event()

    # -------------------------
    # 1) UID
    # -------------------------
    event_id = event_data["eventId"]
    uid_value = f"{event_id}@thenationalva.com"
    ical_event["UID"] = uid_value

    # -------------------------
    # 2) Timestamps: DTSTAMP + LAST-MODIFIED
    # -------------------------
    created_str = event_data.get("createdUTC", "2025-01-01T00:00:00Z")
    modified_str = event_data.get("modifiedUTC", "2025-01-01T00:00:00Z")
    dt_modified = fromiso_utc(modified_str)

    ical_event["DTSTAMP"] = dt_modified
    ical_event["LAST-MODIFIED"] = dt_modified

    # -------------------------
    # 3) All-Day Event
    #    We use eventDateTimeUTC as the reference date for START/END
    # -------------------------
    show_utc_str = event_data.get("eventDateTimeUTC")
    if show_utc_str:
        show_dt_utc = fromiso_utc(show_utc_str)
        start_date = show_dt_utc.date()
        end_date = (show_dt_utc + timedelta(days=1)).date()
    else:
        # If there's no eventDateTimeUTC, fallback (or skip event?)
        # We'll just pick some default date to avoid errors
        start_date = datetime(2025, 1, 1).date()
        end_date = datetime(2025, 1, 2).date()

    ical_event["DTSTART"] = start_date
    ical_event["DTEND"] = end_date

    # -------------------------
    # 4) SUMMARY (Title)
    # -------------------------
    title_text = event_data["title"]["eventTitleText"]
    ical_event["SUMMARY"] = title_text

    # -------------------------
    # 5) LOCATION (Venue)
    # -------------------------
    venue_title = event_data["venue"]["title"]
    venue_address = event_data["venue"]["address_line"]
    ical_event["LOCATION"] = f"{venue_title}, {venue_address}"

    # -------------------------
    # 6) Build DESCRIPTION Lines
    #    We'll parse doorDateTimeUTC + eventDateTimeUTC for display only
    # -------------------------
    desc_lines = []

    door_utc_str = event_data.get("doorDateTimeUTC")
    if door_utc_str:
        door_dt_utc = fromiso_utc(door_utc_str)
        # For display, just show the hour in local or naive?
        # We'll just show the UTC hour here with no leading zero:
        desc_lines.append(f"Doors: {format_time_no_leading_zero(door_dt_utc)} UTC")

    if show_utc_str:
        show_dt_utc = fromiso_utc(show_utc_str)
        desc_lines.append(f"Show: {format_time_no_leading_zero(show_dt_utc)} UTC")

    # under21 => false => All Ages; true => 21+ Only
    headliners = event_data.get("associations", {}).get("headliners", [])
    if headliners:
        hl = headliners[0]
        under21 = hl.get("under21", False)
        minor_cat = hl.get("minorCategoryText", "Unknown Genre")
        age_str = "21+ Only" if under21 else "All Ages"
        desc_lines.append(f"Age: {age_str}")
        desc_lines.append(f"Genre: {minor_cat}")

    ical_event["DESCRIPTION"] = "\n".join(desc_lines)

    # -------------------------
    # 7) URL => Ticket Link
    # -------------------------
    ticket_url = event_data["ticketing"]["url"]
    ical_event["URL"] = ticket_url

    # -------------------------
    # 8) ALARMS: 30 min before doors, 15 min before show
    #    Using absolute UTC triggers (VALUE=DATE-TIME).
    # -------------------------
    # Remove old alarms first (optional). If you run update repeatedly, you can do so
    # to avoid duplicating alarms. Up to you. We'll remove them for clarity.
    for subcomp in list(ical_event.subcomponents):
        if subcomp.name == "VALARM":
            ical_event.subcomponents.remove(subcomp)

    # Alarm #1: Doors 30 min
    if door_utc_str:
        door_dt_utc = fromiso_utc(door_utc_str)
        alarm_time_doors = door_dt_utc - timedelta(minutes=30)

        valarm_doors = Alarm()
        valarm_doors.add("ACTION", "DISPLAY")
        valarm_doors.add("DESCRIPTION", "30 Minutes to Doors")
        valarm_doors.add(
            "TRIGGER;VALUE=DATE-TIME",
            dt_to_utc_ical_string(alarm_time_doors)
        )
        ical_event.add_component(valarm_doors)

    # Alarm #2: Show 15 min
    if show_utc_str:
        show_dt_utc = fromiso_utc(show_utc_str)
        alarm_time_show = show_dt_utc - timedelta(minutes=15)

        valarm_show = Alarm()
        valarm_show.add("ACTION", "DISPLAY")
        valarm_show.add("DESCRIPTION", "15 Minutes to Show")
        valarm_show.add(
            "TRIGGER;VALUE=DATE-TIME",
            dt_to_utc_ical_string(alarm_time_show)
        )
        ical_event.add_component(valarm_show)

    return ical_event

def fetch_events():
    """
    Fetch the entire list of events from the AEG feed.
    Return them as a list of dicts. Each event now presumably has doorDateTimeUTC, eventDateTimeUTC, etc.
    """
    resp = requests.get(FEED_URL)
    resp.raise_for_status()
    data = resp.json()
    return data["events"]

def main():
    """
    1) Load existing or create new .ics
    2) Fetch all events from feed
    3) For each event, add or update VEVENT with:
       - All-day dtstart/dtend
       - 2 alarms: 30 min before door, 15 min before show (absolute UTC triggers)
       - Keep old events not in feed
    4) Save .ics
    """

    # --- 1) Load or create calendar
    if os.path.exists(ICS_FILENAME):
        with open(ICS_FILENAME, "rb") as f:
            cal = Calendar.from_ical(f.read())
        print(f"Loaded existing '{ICS_FILENAME}'.")
    else:
        cal = Calendar()
        cal.add("prodid", "-//TheNationalVA//ConcertSchedule//EN")
        cal.add("version", "2.0")
        print(f"No existing '{ICS_FILENAME}' found. Created a new one.")

    # --- Build dict of existing VEVENTs by UID
    existing_events = {}
    for component in cal.walk("VEVENT"):
        uid = str(component.get("UID"))
        existing_events[uid] = component

    # --- 2) Fetch all events from feed
    all_events = fetch_events()
    print(f"Fetched {len(all_events)} events from '{FEED_URL}'.")

    # --- 3) Add or update
    for evt_data in all_events:
        uid_value = f"{evt_data['eventId']}@thenationalva.com"
        if uid_value in existing_events:
            vevent = existing_events[uid_value]
            create_or_update_ical_event(evt_data, vevent)
            print(f"Updated event => UID: {uid_value}")
        else:
            new_vevent = create_or_update_ical_event(evt_data, None)
            cal.add_component(new_vevent)
            existing_events[uid_value] = new_vevent
            print(f"Added new event => UID: {uid_value}")

    # --- 4) Write updated .ics (and keep old events)
    with open(ICS_FILENAME, "wb") as f:
        f.write(cal.to_ical())

    print(f"Saved '{ICS_FILENAME}' with 2 absolute UTC alarms per event (if door/show UTC times exist).")

if __name__ == "__main__":
    main()
