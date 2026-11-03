import csv
import io

COLUMNS = [
    "id",
    "ident",
    "type",
    "name",
    "latitude_deg",
    "longitude_deg",
    "elevation_ft",
    "continent",
    "iso_country",
    "iso_region",
    "municipality",
    "scheduled_service",
    "gps_code",
    "iata_code",
    "local_code",
    "home_link",
    "wikipedia_link",
    "keywords",
]


def airports_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        full = {column: "" for column in COLUMNS}
        full.update(row)
        writer.writerow(full)
    return buffer.getvalue()


JFK = {
    "id": "1",
    "ident": "KJFK",
    "type": "large_airport",
    "name": "John F Kennedy International",
    "latitude_deg": "40.6398",
    "longitude_deg": "-73.7789",
    "elevation_ft": "13",
    "continent": "NA",
    "iso_country": "US",
    "iso_region": "US-NY",
    "municipality": "New York",
    "scheduled_service": "yes",
    "gps_code": "KJFK",
    "iata_code": "JFK",
}
SMALL = {
    "id": "2",
    "ident": "00AA",
    "type": "small_airport",
    "name": "Small Field",
    "latitude_deg": "40.1",
    "longitude_deg": "-74.1",
    "iso_country": "US",
    "iso_region": "US-NJ",
    "municipality": "Nowhere",
    "scheduled_service": "no",
    "local_code": "1A0",
}
CLOSED = {
    "id": "3",
    "ident": "KOLD",
    "type": "closed",
    "name": "Old Field",
    "latitude_deg": "41.0",
    "longitude_deg": "-72.0",
    "iso_country": "US",
    "municipality": "Oldtown",
    "gps_code": "KOLD",
}
HEATHROW = {
    "id": "4",
    "ident": "EGLL",
    "type": "large_airport",
    "name": "London Heathrow",
    "latitude_deg": "51.47",
    "longitude_deg": "-0.46",
    "iso_country": "GB",
    "municipality": "London",
    "scheduled_service": "yes",
    "gps_code": "EGLL",
    "iata_code": "LHR",
}
DUP_ONE = {
    "id": "5",
    "ident": "KDUP",
    "type": "small_airport",
    "name": "First Dup",
    "latitude_deg": "42.0",
    "longitude_deg": "-71.0",
    "iso_country": "US",
    "municipality": "Dupville",
    "gps_code": "KDUP",
    "iata_code": "DUP",
}
DUP_TWO = {
    "id": "6",
    "ident": "EDUP",
    "type": "small_airport",
    "name": "Second Dup",
    "latitude_deg": "50.0",
    "longitude_deg": "8.0",
    "iso_country": "DE",
    "municipality": "Dupstadt",
    "gps_code": "EDUP",
    "iata_code": "DUP",
}

CATALOG = [JFK, SMALL, CLOSED, HEATHROW, DUP_ONE, DUP_TWO]
CATALOG_CSV = airports_csv(CATALOG)


def renamed_catalog() -> str:
    rows = []
    for row in CATALOG:
        copied = dict(row)
        if copied["ident"] == "KJFK":
            copied["name"] = "Renamed Kennedy"
        rows.append(copied)
    return airports_csv(rows)
