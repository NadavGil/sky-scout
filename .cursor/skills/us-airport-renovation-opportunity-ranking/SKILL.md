---
name: us-airport-renovation-opportunity-ranking
description: Compare United States airports for capacity-unlocking renovation opportunities using transparent demand, bottleneck, and intervention evidence. Separate opportunity screening from investment profitability.
---

# U.S. Airport Renovation Opportunity Ranking

## Purpose and geographic scope
This skill applies exclusively to airports located in the United States. Exclude airports outside the United States even when served by U.S. airlines. Include international services at eligible U.S. airports when they are covered by the selected data; U.S.-only geography does not mean domestic flights only.

Before ingestion, declare whether the project includes U.S. territories. Do not silently mix territory coverage across datasets. Record the chosen inclusion rule in every output.

Help analysts identify where renovation could unlock additional passengers or aircraft movements. Produce an explainable research shortlist, not unsupported investment recommendations.

## Reasoning chain
1. Demand exists or is credibly forecast.
2. A specific infrastructure constraint prevents some demand from being served.
3. A plausible intervention removes that constraint.
4. Remaining constraints do not prevent incremental traffic from materializing.
5. If financial evidence exists, incremental cash flows justify project cost.

Airport size, growth, congestion, and additional infrastructure do not independently establish a profitable renovation opportunity.

## Units of analysis and ranking outputs
- Airport screening: prioritize investigation of renovation opportunities.
- Airport-project pair: evaluate a particular intervention against its no-project baseline.
- Keep projects separate. Do not sum mutually exclusive projects or double-count traffic unlocked by overlapping interventions.
- An airport summary may highlight its best-supported eligible project but must disclose that selection.
- Without project economics, call the output renovation opportunity, never profitability.

## U.S. data strategy
Use free APIs, public downloads, and documented manual inputs. Verify actual source coverage, access method, licensing, definitions, and update frequency before relying on them. Do not imply every source has a free API.

Candidate source families to investigate:
- FAA passenger boarding/enplanement statistics for airport passenger history.
- BTS aviation datasets, including T-100, for traffic, seats, and departures where available and applicable.
- FAA airport reference and capacity/planning materials.
- Published airport master plans, capital programs, financial statements, and operator reports.
- Public airport/runway reference datasets for identifier and geographic enrichment, not for inferring operational capacity.

These are discovery candidates, not promises that any source provides a particular field, endpoint, or completeness level. Do not use Eurostat as the primary traffic source for this U.S.-only workflow.

## Coverage and definition controls
For every observation store airport identifier, metric, value, unit, period, source, retrieval date, publication date when available, and quality flags. Mark values as measured, derived, forecast, or analyst-assumed.

Explicitly declare:
- Geography and treatment of territories.
- Domestic-only versus domestic-plus-international traffic.
- Carrier reporting coverage and excluded operations.
- Scheduled versus nonscheduled coverage, where relevant.
- Commercial passenger versus cargo, general aviation, and other operations.
- Reporting lag, revisions, and latest complete observation period.

Never treat missing coverage as zero activity. An airport may appear to have fewer flights because the source excludes some operations.

### Identifier reconciliation
Maintain a canonical internal airport ID and source-specific mappings. Do not assume FAA location identifiers, IATA codes, ICAO codes, and BTS airport identifiers are interchangeable. Record mapping provenance and handle identifier changes, duplicate names, closed airports, and ambiguous matches explicitly.

### Passenger and movement definitions
Do not mix enplanements with arrivals-plus-departures passenger totals. Do not automatically double enplanements to estimate total terminal passengers. Document connecting-passenger treatment.

Keep passengers, seats, departures, and total movements distinct. Do not assume departures equal total movements or automatically multiply departures by two. Keep segment and market traffic measures distinct; do not sum records in ways that double-count passengers.

Capacity utilization requires numerator and denominator aligned on time period, direction, facility, and traffic coverage. Do not compare commercial departures alone with capacity defined for all arrivals and departures.

## Workflow

### 1. Validate observations
Resolve identifiers, duplicates, and geography. Use comparable periods and a common cutoff. Never compare an incomplete year with a complete year. Flag disruptions, recovery effects, definition changes, stale data, and incomplete coverage. Preserve raw source snapshots where practical.

Declare minimum history and completeness requirements before ranking. Missing values are not measured zeros.

### 2. Define U.S. peer groups
Compare airports in similar passenger-volume bands. Where supported by data, account for connecting-hub versus origin-and-destination role, operational restrictions, and market setting. Define categories explicitly; do not assign roles from airport names or intuition alone.

Document cohort membership, size, and reference period. If a cohort is too small, widen it transparently or decline to calculate percentiles. Do not let membership changes silently alter score interpretation.

### 3. Calculate demand score D
Normalize each indicator to 0-100 within the declared peer group:
- Long-term passenger growth: 0.40.
- Recent passenger growth: 0.25.
- Absolute passenger increase: 0.20.
- Growth consistency: 0.15.

D = 0.40*G_long + 0.25*G_recent + 0.20*G_absolute + 0.15*G_consistency

Provisional operational defaults:
- Long-term: CAGR over five complete annual intervals, requiring six comparable annual observations.
- Recent: latest complete year versus the preceding complete year.
- Absolute increase: annual passenger count at the end minus at the beginning of the long-term window.
- Consistency: fraction of positive annual changes in that window, then normalized against peers.

CAGR requires a positive starting value. Do not silently replace invalid values or redistribute weights. Insufficient history means the full model is unavailable, unless a separately labeled reduced-data model was declared in advance.

These weights are design assumptions, not validated investment coefficients. Growth rates can exaggerate small-base growth or recovery. Served traffic is not unconstrained demand: flat traffic at a constrained airport does not prove weak demand. Passenger growth relative to departures does not establish occupancy without seat data. Do not fabricate unmet demand from growth alone.

### 4. Identify capacity pressure C
Identify the specific limiting facility before assigning a score.

Prefer:
- Peak movements versus credible runway operating capacity.
- Gate or stand utilization versus assessed usable capacity.
- Passenger-processing throughput versus an assessed operating limit.
- Current published evidence identifying a binding infrastructure constraint.

Congestion, peak concentration, delays, and annual throughput versus a design figure are supporting evidence only. Separate service-quality crowding from an actual throughput constraint. Delays alone do not establish that renovation is the remedy.

Do not infer practical capacity from runway number, runway length, or annual traffic alone.

Before assigning numerical C:
1. Declare the bottleneck metric and credible capacity denominator.
2. Declare a monotonic 0-100 mapping and its interpretation.
3. Establish comparability within the cohort and across constraint categories.
4. Publish the raw metric, period, and source beside C.

There is no assumed universal utilization-to-score conversion. Without a defensible mapping, report qualitative evidence and do not calculate a composite score. Unknown is not zero. Investigate utilization above 100% rather than silently clipping the raw observation.

### 5. Calculate renovation fit R
Provisional analyst rubric:
- 100: documented bottleneck and directly relevant intervention; no unresolved conflicting binding constraint identified in reviewed evidence.
- 75: documented bottleneck and plausible intervention; capacity gain or interactions remain uncertain.
- 50: suggestive bottleneck/intervention evidence needing verification.
- Unknown: insufficient evidence.

These values are rubric scores, not success probabilities. Record the source-backed justification. Affirmative evidence that the intervention does not address the bottleneck makes the pair ineligible for this ranking, not merely unknown.

Account for operating restrictions, airspace or other infrastructure constraints, airline-service uncertainty, and the opening dates of already committed projects. Do not assume physical expansion changes regulatory limits or guarantees new service.

### 6. Apply eligibility rules
Only rank an airport-project pair when:
- Comparable traffic evidence supports D.
- Credible bottleneck evidence and a declared mapping support C.
- A plausible, relevant intervention supports R.

Otherwise classify it as:
- Demand-growth watchlist: traffic evidence exists, but constraint or intervention evidence is missing.
- Ineligible for this capacity-unlocking ranking: documented intervention mismatch or scope exclusion.
- Insufficient data: traffic or other foundational evidence is inadequate.

Do not silently renormalize around missing dimensions.

### 7. Calculate opportunity score
For eligible pairs with D, C, and R on 0-100 scales:

S = 100 * (D/100)^0.40 * (C/100)^0.35 * (R/100)^0.25

The weighted geometric mean penalizes weak links more than an arithmetic mean, but it still allows some compensation and does not replace eligibility rules. A genuine zero yields zero; missing values never become zero. Do not add an arbitrary epsilon.

Weights are provisional. Show all component scores and evidence. S is not a probability, ROI, or estimate of incremental traffic.

### 8. Normalize reproducibly
Suggested initial convention: ascending within-cohort percentiles with average ranks for ties.

For average rank r among n valid observations, with n > 1:
normalized_score = 100 * (r - 1) / (n - 1)

Declare the tie policy, reference cohort, and small-sample handling. This convention can assign zero to the weakest peer, forcing a geometric score of zero even when its raw performance is positive. Disclose this behavior and test a bounded-percentile alternative if appropriate. Never change scoring rules after inspecting favored results without disclosure.

Keep raw values. Do not normalize unlike capacity measures together without a defensible common interpretation. Flag outliers and document any treatment. Relative scores are not absolute economic judgments.

### 9. Report incremental scale separately
Where credible demand and capacity inputs exist, a simplified screening model is:

P[s,t] = min(Demand[s,t], EffectiveCapacity[s,t])
DeltaP[t] = P[project,t] - P[baseline,t]

Use matching definitions and time bases. Include opening dates, ramp-up, remaining bottlenecks, and committed expansions in the baseline. Keep demand assumptions comparable unless a separate induced-demand mechanism is justified. Do not credit all future passengers to the renovation.

Annual screening does not replace peak-period capacity analysis. Do not convert flight gains into passengers without defensible seat and occupancy assumptions. If inputs are absent, report incremental passengers and movements as unknown.

### 10. Report confidence separately
Confidence concerns evidence quality, not investment attractiveness:
- High: current, comparable traffic; direct constraint and intervention evidence; traceable major assumptions.
- Medium: usable evidence with meaningful assumptions or incomplete validation.
- Low: indirect, incomplete, or stale evidence requiring substantial verification.

Assess confidence by dimension and explain gaps. Do not multiply S by a confidence percentage. Confidence labels do not override eligibility.

### 11. Test robustness
Vary weights, peer groups, normalization, and uncertain inputs. Remove indicators one at a time. Check correlations and double-counting. Report rank ranges or stable tiers where ordering is fragile. Declare all tested alternatives.

Historical tests must use only information available at the historical cutoff. Forecast accuracy is not causal proof that renovation caused growth or profit. Never claim robustness unless tests have actually run.

## Optional financial extension
Only with sufficient project inputs, calculate incremental project cash flows relative to the no-project baseline:

DeltaFCF[t] = DeltaRevenue[t] - DeltaOperatingCosts[t] - DeltaTaxes[t] - DeltaCapex[t] - DeltaWorkingCapital[t]
NPV = sum(DeltaFCF[t] / (1+r)^t), t=0..T

Separate aeronautical and non-aeronautical revenue. Include construction disruption, ramp-up, maintenance capital expenditure, and justified terminal value. Declare currency, inflation convention, horizon, discount rate, and investor perspective.

If public grants or other funding are relevant, distinguish total project economics from the airport sponsor's funding exposure. Do not assume grant availability or eligibility. Do not double-count financing costs through both cash flows and the discount rate.

Airport-owner returns, airline profits, and regional benefits are different outcomes. Keep total NPV separate from return per dollar. Without credible financial inputs, do not invent profitability, IRR, payback, or ROI rankings.

## Required output
1. U.S. scope, territory policy, traffic coverage, cutoff, and peer-group rules.
2. Eligible scored airport-project opportunities.
3. Separate watchlist, insufficient-data entries, and excluded/ineligible cases with reasons.
4. For every ranked pair: D, C, R, S; raw supporting metrics; source dates; bottleneck; intervention rationale; incremental scale or unknown; confidence; committed projects; remaining constraints; and sensitivity results if calculated.
5. Explicit statement that opportunity screening does not establish profitability.

Suggested table:
Airport | Project | Peer group | D | C | R | S | Incremental passengers | Incremental movements | Confidence | Bottleneck | Missing evidence

Cite empirical claims. Label illustrative data hypothetical. Never fabricate rankings when no input data exists.

## Acceptance checklist
- [ ] Only eligible U.S. airports appear; territory policy is explicit.
- [ ] Domestic and international traffic coverage is explicit.
- [ ] Enplanements, passenger totals, departures, and movements are not conflated.
- [ ] Source identifiers are reconciled and reporting gaps are not zeros.
- [ ] Growth alone cannot qualify an airport.
- [ ] Bottleneck, time basis, capacity denominator, and C mapping are documented.
- [ ] Proposed intervention addresses the constraint.
- [ ] Committed capacity is included in the baseline.
- [ ] Scoring, normalization, cohorts, and weights are reproducible.
- [ ] Opportunity, scale, and confidence remain separate.
- [ ] Profit claims require financial evidence.

## Methodological references for verification
These references support general methodology; they do not validate the proposed weights or rubric. Verify current source documentation before implementation.
- OECD, Handbook on Constructing Composite Indicators.
- FAA, Airport Capacity and Delay, AC 150/5060-5.
- FAA, Aviation Forecast Review and Approval Instructions.
- FAA passenger boarding statistics documentation.
- BTS documentation for the specific aviation tables selected.
