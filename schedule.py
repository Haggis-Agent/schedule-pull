#!/usr/bin/env python3

import os
import requests
from datetime import datetime, timedelta, timezone
from icalendar import Calendar, Event
from icalendar.prop import vDatetime  # to force proper RFC 5545 formatting

ICS_FILENAME = "concert_schedule.ics"
FEED_URL = "https://aegwebprod.blob.core.windows.net/json/events/51/events.json"

def fetch_events():
    """
    Fetch all events from the AEG feed.
    Returns a list of event dictionaries.
    """
    resp = requests.get(FEED_URL)
    resp.raise_for_status()  # Raise an exception if HTTP error
    data = resp.json()
    # data should have keys: "meta" and "events"
    # "events" is the list of all event objects
    return data["events"]

def format_time_no_leading_zero(dt: datetime) -> str:
    """
    Return a 12-hour clock string like "8:00 PM" with:
      - No leading zero on the hour
      - Zero-padded minutes
      - AM/PM
    """
    hour_12 = dt.hour % 12
    if hour_12 == 0:
        hour_12 = 12
    minute_str = f"{dt.minute:02d}"  # zero-pad minutes
    ampm_str = dt.strftime("%p")     # "AM" or "PM"
    return f"{hour_12}:{minute_str} {ampm_str}"

def from_iso(dt_str):
    """
    Convert an ISO formatted string to a timezone-aware datetime in UTC.
    Assumes the input is in UTC.
    """
    # Parse the string to a datetime object
    dt = datetime.fromisoformat(dt_str)
    # Force it to be UTC (if it's not already timezone-aware)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def create_or_update_ical_event(event_data, ical_event=None):
    """
    Create a new VEVENT or update an existing one (if ical_event is provided).
    Uses all-day (date-based) DTSTART/DTEND.
    """
    if ical_event is None:
        ical_event = Event()

    # 1) UID
    event_id = event_data["eventId"]  # e.g. "765964"
    uid_value = f"{event_id}@thenationalva.com"
    ical_event["UID"] = uid_value

    # 2) created/modified times => DTSTAMP + LAST-MODIFIED
    created_str = event_data.get("createdUTC", "2025-01-01T00:00:00")
    modified_str = event_data.get("modifiedUTC", "2025-01-01T00:00:00")
    dt_modified = from_iso(modified_str)
    ical_event["DTSTAMP"] = vDatetime(dt_modified)
    ical_event["LAST-MODIFIED"] = vDatetime(dt_modified)

    # 3) All-day start/end from eventDateTime
    show_dt_str = event_data["eventDateTime"]  # e.g. "2025-01-31T20:00:00"
    show_dt = from_iso(show_dt_str)
    start_date = show_dt.date()
    end_date = (show_dt + timedelta(days=1)).date()
    # Remove any previous DTSTART/DTEND entries if updating
    if "DTSTART" in ical_event:
        del ical_event["DTSTART"]
    if "DTEND" in ical_event:
        del ical_event["DTEND"]
    # Add with proper parameters for date-only values
    ical_event.add("DTSTART", start_date, parameters={"VALUE": "DATE"})
    ical_event.add("DTEND", end_date, parameters={"VALUE": "DATE"})

    # 4) SUMMARY (Title)
    ical_event["SUMMARY"] = event_data["title"]["eventTitleText"]

    # 5) LOCATION (Venue)
    venue_title = event_data["venue"]["title"]
    venue_address = event_data["venue"]["address_line"]
    ical_event["LOCATION"] = f"{venue_title}, {venue_address}"

    # 6) DESCRIPTION lines
    desc_lines = []

    # Doors
    door_str = event_data.get("doorDateTime")
    if door_str:
        door_dt = from_iso(door_str)
        desc_lines.append(f"Doors: {format_time_no_leading_zero(door_dt)}")

    # Show
    desc_lines.append(f"Show: {format_time_no_leading_zero(show_dt)}")

    # Support (if available)
    supporting_text = event_data["title"].get("supportingText")
    if supporting_text:
        desc_lines.append(f"Support: {supporting_text}")

    # under21 => false => All Ages; true => 21+ Only, plus genre info
    headliners = event_data.get("associations", {}).get("headliners", [])
    if headliners:
        hl = headliners[0]
        under21 = hl.get("under21", False)
        minor_cat = hl.get("minorCategoryText", "Unknown Genre")
        age_str = "21+ Only" if under21 else "All Ages"
        desc_lines.append(f"Age: {age_str}")
        desc_lines.append(f"Genre: {minor_cat}")

    ical_event["DESCRIPTION"] = "\n".join(desc_lines)

    # 7) URL => ticket link
    ticket_url = event_data["ticketing"]["url"]
    ical_event["URL"] = ticket_url

    return ical_event

def main():
    """
    - Loads existing concert_schedule.ics or creates new.
    - Fetches all events from feed.
    - For each event in feed, add/update in ICS.
    - Does NOT remove old events => they remain in the .ics.
    - Writes updated .ics.
    """
    # 1) Load or create calendar
    if os.path.exists(ICS_FILENAME):
        with open(ICS_FILENAME, "rb") as f:
            cal = Calendar.from_ical(f.read())
        print(f"Loaded existing '{ICS_FILENAME}'.")
    else:
        cal = Calendar()
        cal.add("prodid", "-//TheNationalVA//ConcertSchedule//EN")
        cal.add("version", "2.0")
        # Add CALSCALE (recommended)
        cal.add("calscale", "GREGORIAN")
        print(f"No existing '{ICS_FILENAME}' found. Created a new one.")

    existing_events = {}
    for component in cal.walk("VEVENT"):
        uid = str(component.get("UID"))
        existing_events[uid] = component

    # 2) Fetch all events from feed
    all_events = fetch_events()
    print(f"Fetched {len(all_events)} events from feed '{FEED_URL}'.")

    # 3) Add or update events in the calendar
    for evt_data in all_events:
        uid_value = f"{evt_data['eventId']}@thenationalva.com"
        if uid_value in existing_events:
            vevent = existing_events[uid_value]
            create_or_update_ical_event(evt_data, vevent)
            print(f"Updated event => UID: {uid_value}")
        else:
            new_vevent = create_or_update_ical_event(evt_data)
            cal.add_component(new_vevent)
            existing_events[uid_value] = new_vevent
            print(f"Added new event => UID: {uid_value}")

    # 4) Write updated ICS
    with open(ICS_FILENAME, "wb") as f:
        f.write(cal.to_ical())

    print(f"Saved updated '{ICS_FILENAME}'. Past events remain.\nDone!")

if __name__ == "__main__":
    main()
