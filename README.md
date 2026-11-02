# Sky Scout

Sky Scout is a desk for analysts looking at US airport capacity. It ranks airports with a fixed score and answers other questions only when the loaded public files contain the figure. If they do not, it says so.

## Run

```bash
npm install
npm run dev
```

Open [http://127.0.0.1:38471](http://127.0.0.1:38471).

```bash
npm test
```

## What it can answer

The desk joins three public sources that were fetched on 2 Nov 2026:

- [OurAirports](https://davidmegginson.github.io/ourairports-data/airports.csv) airports and runways.
- [NTAD T-100 domestic origin totals for 2024](https://services.arcgis.com/xOi1kZaI0eWDREZv/ArcGIS/rest/services/T100_Domestic_Market_and_Segment_Data/FeatureServer/1). Enplanements, passengers, departures, arrivals, freight, and mail. Domestic US-carrier traffic only.
- [FAA commercial-service enplanements](https://www.faa.gov/sites/faa.gov/files/2026-09/arp-cy2024-commercial-service-enplanements.xlsx) for calendar years 2023 and 2024.
- [BTS Reporting Carrier On-Time Performance, August 2026](https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2026_8.zip), reduced to the origin airport. Delay rate, mean departure delay minutes, taxi-out, cancellations, and delay-cause minutes.

The capacity score uses only airports that have every input:

- 32% departures per paved runway of at least 5,000 feet
- 24% log of 2024 domestic enplanements
- 24% FAA enplanement change from 2023 to 2024
- 20% August 2026 share of departures delayed at least 15 minutes

Percentiles use every joined airport that has all four inputs. A state or region list uses those same scores, so an airport does not get a new number when the question changes. A missing input drops that airport from the score. It is not imputed. A higher delay rate raises the score because the formula treats it as operational pressure.

The loader drops foreign fields that collided with a local code, and it keeps one row where an FAA id and a traffic code are the same airport: GRO is ROP, SAW is MQT, TNI is TIQ, and GSN is SPN. PBI is in the FAA file and did not join, so questions about Palm Beach International are refused.

The score is not revenue, cost, NPV, or a terminal-versus-runway diagnosis. Seats and gates are not in these files.

## What it will not answer

Stage length and long-haul share, seats, load factor, unmet demand, carriers, routes, international segments, fares, and construction cost. The BTS open-data API did not respond when these files were collected, and the TranStats prezip directory had no T-100 segment file to replace it.

Voice input and spoken replies use the browser speech APIs. Audio is not uploaded. Speech recognition depends on the browser.

If `OPENAI_API_KEY` is set, a model may rephrase a finished answer. Any number that is not already in the result is discarded, and the original reply is kept. The desk still runs with no key.
