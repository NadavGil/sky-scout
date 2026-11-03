from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///data/sky-scout.db"
    opensky_client_id: str = ""
    opensky_client_secret: str = ""
    traffic_cache_seconds: int = 60
    http_timeout_seconds: float = 15
    traffic_radius_km: float = 50
    snapshot_retention_days: int = 30
    ourairports_url: str = "https://davidmegginson.github.io/ourairports-data/airports.csv"
    opensky_api_url: str = "https://opensky-network.org/api"
    opensky_token_url: str = (
        "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
    )
    aviationweather_api_url: str = "https://aviationweather.gov/api/data"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    allow_project_costs_to_model: bool = False
    dataset_timeout_seconds: float = 180
    faa_enplanement_years: str = "2020,2021,2022,2023,2024,2025"
    faa_enplanements_url_template: str = (
        "https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/passenger/"
        "arp-cy{year}-commercial-service-enplanements.xlsx"
    )
    bts_t100_url_template: str = "https://transtats.bts.gov/PREZIP/T_T100_SEGMENT_ALL_CARRIER_{year}_{month}.zip"
    bts_t100_form_url: str = (
        "https://transtats.bts.gov/DL_SelectFields.aspx?QO_fu146_anzr=Nv4+Pn44vr45&gnoyr_VQ=FMG"
    )
    bts_ontime_url_template: str = (
        "https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
    )

    @property
    def opensky_flights_configured(self) -> bool:
        return bool(self.opensky_client_id and self.opensky_client_secret)

    @property
    def faa_years(self) -> list[int]:
        years = []
        for part in self.faa_enplanement_years.split(","):
            text = part.strip()
            if text:
                years.append(int(text))
        return years

    def faa_enplanements_url(self, year: int) -> str:
        known = {
            2020: "https://www.faa.gov/sites/faa.gov/files/airports/planning_capacity/passenger_allcargo_stats/passenger/cy20-commercial-service-enplanements.xlsx",
            2021: "https://www.faa.gov/sites/faa.gov/files/2022-09/cy21-commercial-service-enplanements.xlsx",
            2022: "https://www.faa.gov/sites/faa.gov/files/2023-09/cy22-commercial-service-enplanements.xlsx",
            2023: "https://www.faa.gov/sites/faa.gov/files/2024-10/cy23-commercial-service-enplanements.xlsx",
            2024: "https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/passenger/arp-cy2024-commercial-service-enplanements.xlsx",
            2025: "https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/passenger/arp-cy2025-commercial-service-enplanements.xlsx",
        }
        return known.get(year, self.faa_enplanements_url_template.format(year=year))
