STATE_NAMES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}

TERRITORIES = ("AS", "GU", "MP", "PR", "VI")

NEW_ENGLAND = ("CT", "ME", "MA", "NH", "RI", "VT")
MIDDLE_ATLANTIC = ("NJ", "NY", "PA")
EAST_NORTH_CENTRAL = ("IL", "IN", "MI", "OH", "WI")
WEST_NORTH_CENTRAL = ("IA", "KS", "MN", "MO", "NE", "ND", "SD")
SOUTH_ATLANTIC = ("DE", "DC", "FL", "GA", "MD", "NC", "SC", "VA", "WV")
EAST_SOUTH_CENTRAL = ("AL", "KY", "MS", "TN")
WEST_SOUTH_CENTRAL = ("AR", "LA", "OK", "TX")
MOUNTAIN = ("AZ", "CO", "ID", "MT", "NV", "NM", "UT", "WY")
PACIFIC = ("AK", "CA", "HI", "OR", "WA")

DIVISIONS = {
    "new england": NEW_ENGLAND,
    "middle atlantic": MIDDLE_ATLANTIC,
    "east north central": EAST_NORTH_CENTRAL,
    "west north central": WEST_NORTH_CENTRAL,
    "south atlantic": SOUTH_ATLANTIC,
    "east south central": EAST_SOUTH_CENTRAL,
    "west south central": WEST_SOUTH_CENTRAL,
    "mountain": MOUNTAIN,
    "pacific": PACIFIC,
}

REGIONS = {
    **DIVISIONS,
    "northeast": NEW_ENGLAND + MIDDLE_ATLANTIC,
    "midwest": EAST_NORTH_CENTRAL + WEST_NORTH_CENTRAL,
    "south": SOUTH_ATLANTIC + EAST_SOUTH_CENTRAL + WEST_SOUTH_CENTRAL,
    "west": MOUNTAIN + PACIFIC,
    "united states": tuple(STATE_NAMES),
    "us": tuple(STATE_NAMES),
    "usa": tuple(STATE_NAMES),
}

TERRITORY_STATEMENT = "U.S. states and the District of Columbia. Territories are excluded."
PEER_SET = (
    "Scheduled commercial-service airports in the named states. "
    "Closed airports and airports without scheduled service are excluded."
)
COMMERCIAL_TYPES = ("large_airport", "medium_airport", "small_airport")
LONG_HAUL_MILES = 3000

NAME_TO_CODE = {name.lower(): code for code, name in STATE_NAMES.items()}
NAME_TO_CODE["washington dc"] = "DC"
NAME_TO_CODE["washington d c"] = "DC"
NAME_TO_CODE["district of columbia"] = "DC"


def _key(text: str) -> str:
    return " ".join(text.strip().lower().replace(".", "").split())


def resolve_region(text: str) -> tuple[str, list[str]] | None:
    key = _key(text)
    if not key:
        return None
    if key in REGIONS:
        label = "United States" if key in {"us", "usa", "united states"} else key.title()
        if key == "washington dc" or key == "district of columbia":
            label = "District of Columbia"
        return label, list(REGIONS[key])
    if key in NAME_TO_CODE:
        code = NAME_TO_CODE[key]
        return STATE_NAMES[code], [code]
    if key.upper() in STATE_NAMES and len(key) == 2:
        code = key.upper()
        return STATE_NAMES[code], [code]
    return None


def is_territory(iso_region: str | None) -> bool:
    if not iso_region or "-" not in iso_region:
        return False
    return iso_region.split("-", 1)[1] in TERRITORIES
