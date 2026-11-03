# Sky Scout architecture

Sky Scout answers from a joined catalog of public airport files. A model reads the question, chooses which airports and metrics to measure, and writes which of them look promising. The measurements come from the catalog. The model cannot add a number the tools did not return.

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

A separate mark, called strong, is not a fifth weight. It requires three things at once: 2024 domestic enplanements of at least 100,000, an FAA enplanement increase, and a capacity score at or above the 75th percentile of the scored airports that clear that enplanement floor. The measured result states how many airports are in that peer set for the catalog that is loaded. The analyst may cite that mark. It still says, in its own words, which airports look promising.

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

The analyst is a model. On each question, `lib/agent/analyst.ts` sends the conversation to the OpenAI chat completions API with tools over the loaded catalog. The model decides what is being asked, which airports are meant, and which of them look promising. The default model is `gpt-4o-mini`. `OPENAI_MODEL` replaces it. Temperature is 0, and each call gives up after 30 seconds. `OPENAI_API_KEY` is required. Without it, the desk says so and does not answer from the rules parser.

A 429 or 503 does not become the answer. The desk retries when the server asks for a wait of under two seconds. A longer rate limit keeps any table already measured, or otherwise runs the same question through the rules desk, and says the model was rate-limited. Model calls then pause for 20 to 60 seconds. Other HTTP errors are still reported as themselves.

The tools do the measuring. `find_airports` resolves a name, city, or code. `look_up` runs the fixed score and the other loaded metrics for the airports, states, or region the model chose. `decline` is how the model refuses a figure the files do not contain, such as seats, stage length, unmet demand, carriers, routes, fares, or construction cost. The strong flag is evidence the model can cite. It is not a substitute for the model's own judgment.

Every number in the model's reply must already appear in a tool result or in the analyst instructions. If the reply adds a figure, the desk keeps the measured table and discards that wording. Speech input and spoken replies still use the browser’s speech recognition and speech synthesis. Audio is not uploaded, and the desk does not choose the voice.

`lib/agent/parse.ts` remains in the project as the earlier rules matcher. The chat desk does not use it to decide an answer.
