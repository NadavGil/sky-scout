# Sky Scout

Sky Scout is a desk for analysts looking at US airport capacity. It ranks airports with a fixed score and answers other questions only when the loaded public files contain the figure. If they do not, it says so.

The scoring formula, the tradeoffs in that formula, and where a model is used are in [docs/architecture.md](docs/architecture.md).

## Run

`curl` and `unzip` need to be on the path. The server uses them to download and read the public files.

```bash
npm install
npm run dev
```

Open [http://127.0.0.1:38471](http://127.0.0.1:38471).

```bash
npm test
```

Tests read `data/snapshot/catalog.json` and do not download anything.

If port 38471 is already taken, start this checkout on another port:

```bash
npm run dev -- -p 38472
```

Only the process started from this checkout fetches current files. An older server left on 38471 keeps serving the Nov 2 snapshot.

## When the files are fetched

The first page or chat request after the server starts downloads the four public files and waits until that catalog is ready. Later requests in the same process reuse it.

Every 6 hours while the server stays running, it downloads the files again in the background. A request that arrives during that download still uses the previous catalog. The next request uses the new one. An open desk checks `/api/catalog` once a minute and updates the “Files read” time.

A failed refresh keeps the last successful catalog and tries again after 5 minutes. `data/snapshot/catalog.json` is the Nov 2, 2026, 10:40 PM Asia/Jerusalem copy. The desk uses it only when no live fetch has succeeded.

Set `SKY_SCOUT_REFRESH_HOURS` to change the interval. The shortest interval is 15 minutes.

```bash
SKY_SCOUT_REFRESH_HOURS=1 npm run dev
```

These files change slowly. A refresh can leave the airport figures the same and only move the read time. OurAirports changes from time to time. The FAA workbook and the 2024 traffic totals change rarely. The on-time file changes when the August 2026 month is revised.

## What it can answer

The desk joins:

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

The loader drops foreign fields that collided with a local code, and it keeps one row where an FAA id and a traffic code are the same airport: GRO is ROP, SAW is MQT, TNI is TIQ, and GSN is SPN. An FAA id that OurAirports does not list stays unmatched, and questions about that airport are refused. On the Nov 2 snapshot and again on Nov 3, 2026, that code was PBI.

The score is not revenue, cost, NPV, or a terminal-versus-runway diagnosis. Seats and gates are not in these files.

## What it will not answer

Stage length and long-haul share, seats, load factor, unmet demand, carriers, routes, international segments, fares, and construction cost. The BTS open-data API did not respond when these files were first collected, and the TranStats prezip directory had no T-100 segment file to replace it. The desk reads the August 2026 on-time zip instead.

Voice input and spoken replies use the browser speech APIs. Audio is not uploaded. Speech recognition depends on the browser. The desk does not choose a voice, so the reader uses the system default.

If `OPENAI_API_KEY` is set, a model may rephrase a finished answer. Any number that is not already in the result is discarded, and the original reply is kept. The desk still runs with no key.
