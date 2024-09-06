#!/usr/bin/env python3

import dateutil.parser
import sys
from dateutil.tz import gettz

# Define a mapping for timezone abbreviations
tzinfos = {
    "UTC": gettz("UTC"),
    "EDT": gettz("America/New_York"),
    "EST": gettz("America/New_York"),
    "CDT": gettz("America/Chicago"),
    "CST": gettz("America/Chicago"),
    "MDT": gettz("America/Denver"),
    "MST": gettz("America/Denver"),
    "PDT": gettz("America/Los_Angeles"),
    "PST": gettz("America/Los_Angeles"),
    "GMT": gettz("GMT"),
    "BST": gettz("Europe/London"),
    "CET": gettz("Europe/Paris"),
    "EET": gettz("Europe/Helsinki"),
    "CEST": gettz("Europe/Berlin"),
    "EEST": gettz("Europe/Helsinki"),
    "IST": gettz("Asia/Kolkata"),
    "JST": gettz("Asia/Tokyo"),
    "WIB": gettz("Asia/Jakarta"),
    "IDT": gettz("Asia/Jerusalem"),
    "AEST": gettz("Australia/Sydney"),
    "AEDT": gettz("Australia/Sydney"),
    "ACST": gettz("Australia/Adelaide"),
    "AKST": gettz("America/Anchorage"),
    "AST": gettz("America/Halifax"),
    "AWST": gettz("Australia/Perth"),
    "CST": gettz("Asia/Shanghai"), # Note that CST can also be China Standard Time
    "HKT": gettz("Asia/Hong_Kong"),
    "HST": gettz("Pacific/Honolulu"),
    "KST": gettz("Asia/Seoul"),
    "MSK": gettz("Europe/Moscow"),
    "NZST": gettz("Pacific/Auckland"),
    "PKT": gettz("Asia/Karachi"),
    "SGT": gettz("Asia/Singapore"),
    "WAT": gettz("Africa/Lagos"),
    "WET": gettz("Europe/Lisbon")
    # Add more timezones as needed
}

date_str = sys.stdin.read().strip()
parsed_date = dateutil.parser.parse(date_str, fuzzy=True, tzinfos=tzinfos)
#print(parsed_date.date())
print(parsed_date.strftime("%d"))
