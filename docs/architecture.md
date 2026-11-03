# Sky Scout architecture

Sky Scout answers from a joined catalog of public airport files. A question is turned into a fixed query, the query reads only that catalog, and the reply is written from the rows. A model is optional, and it cannot add a number.

The catalog is OurAirports, NTAD T-100 domestic origin totals for 2024, FAA commercial-service enplanements for 2023 and 2024, and BTS reporting-carrier on-time performance for August 2026. The running server fetches those files on the first request and again every 6 hours. `data/snapshot/catalog.json` is the Nov 2, 2026 copy and is used only when no live fetch has succeeded. Tests read that snapshot and do not download.

## Scoring methodology

An airport is scored only when all four inputs are present. A missing input is not filled in, and that airport has no score.

| Weight | Input | Definition |
| --- | --- | --- |
| 32% | Runway pressure | 2024 domestic departures divided by paved runways of at least 5,000 feet that OurAirports does not list as closed. A blank or unrecognized surface does not count. |
| 24% | Scale | Natural log of 2024 domestic enplanements from the NTAD T-100 origin table. |
| 24% | Change | FAA commercial enplanements in calendar 2024 divided by calendar 2023, minus one. |
| 20% | Delay | Share of operated August 2026 reporting-carrier departures delayed at least 15 minutes. |

Each input is converted to a percentile across every joined airport that has all four inputs. Ties share the average rank. The percentile runs from 0 for the lowest value to 100 for the highest. The capacity score is the weighted sum of those four percentiles, so it also runs from 0 to 100.

The percentile pool is national. A state or region list looks up those same scores. Asking about one airport on its own returns the same score as the list.

A separate mark, called strong, is not a fifth weight. It requires three things at once: 2024 domestic enplanements of at least 100,000, an FAA enplanement increase, and a capacity score at or above the 75th percentile of the scored airports that clear that enplanement floor. The reply states how many airports are in that peer set for the catalog that is loaded.

A higher delay rate raises the score. In this formula, delay is operational pressure. The score is not revenue, concession income, airline charges, construction cost, NPV, or IRR.

## Key tradeoffs

The score is a fixed formula, not a model fit to investment outcomes. The weights do not change when the question changes, and they are not learned from the files. That keeps two analysts on the same catalog at the same number. It also means the weights are a judgment, not a measured effect.

Missing data drops the airport instead of imputing a runway, a delay rate, or an enplanement change. The ranking is then only among airports the files can describe. Airports with a real gap in one file disappear from the score rather than appearing with a guessed input.

Delay counts upward. A congested airport can outrank a quieter one with the same traffic and runways. The desk says this in the assumptions. It does not treat a high score as evidence that the airport runs well.

NTAD enplanements and FAA enplanements are different series. Scale uses the domestic T-100 origin total. The year-over-year term uses FAA commercial-service enplanements, which include traffic the domestic origin table does not. The two are not reconciled into one passenger number.

Delay is one month, August 2026, for domestic reporting carriers. A single month can move the score, and it says nothing about other months or about international flights. Freight, mail, passengers, and departures are 2024 annual domestic totals. The score mixes that year with one later month of delay.

The desk will not turn the score into a terminal-versus-runway diagnosis. Seats, gates, and load factor are not in the files. A question that says “terminal” still gets the capacity score, plus an explicit note that the terminal constraint was not measured.

Unmatched FAA codes are omitted. The loader does not guess that a code is a nearby airport. Four known collisions are merged by a fixed map, because the FAA id had been attached to a foreign field that reused the code: SAW to MQT, GRO to ROP, TNI to TIQ, and GSN to SPN. On the Nov 2 snapshot and the Nov 3 fetch, PBI was the FAA code left unmatched.

The same catalog is reused until the refresh interval. A failed refresh keeps the last successful catalog. Numbers can change after a refresh when the public files change, which is why the Nov 2 desk and the Nov 3 desk do not return the same lists.

## Where and how AI is used

Understanding the question does not use a model. `lib/agent/parse.ts` matches airports, places, metrics, comparisons, and follow-ups with rules. If the question needs stage length, seats, unmet demand, carriers, routes, fares, or another field that was not loaded, the parser returns a refusal. The query engine then answers from the catalog or repeats that refusal. Both paths are deterministic.

Speech input and spoken replies use the browser’s speech recognition and speech synthesis. Audio is not uploaded. The desk does not choose the voice.

A model is used in one place, and only after the rows exist. If `OPENAI_API_KEY` is set, `lib/agent/phrase.ts` sends the finished reply, the rows, and the assumptions to the OpenAI chat completions API. The default model is `gpt-4o-mini`. `OPENAI_MODEL` replaces it. Temperature is 0, and the call gives up after 8 seconds.

The instruction is to rewrite the reply in plain sentences and to use only numbers already present. The desk checks that every number in the model text already appears in the reply, the rows, or the assumptions. If the check fails, the call times out, or the API returns an error, the original reply is kept. Refusals are not sent to the model. With no API key, the original reply is what the desk shows.
