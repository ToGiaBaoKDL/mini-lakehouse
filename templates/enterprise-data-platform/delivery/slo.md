# Data Product SLO Measurement

An SLO is measured from consumer-visible publication, not from a green job run. Each product
contract names a versioned `eligible_intervals_ref`: an adopting platform resolves this to the
expected logical intervals and business calendar, including holidays, timezone, schedule changes,
and ownership. The reference must be reviewable and immutable for a measurement window. An
unresolvable schedule is a configuration failure, not permission to exclude intervals.

All targets use the contract's rolling window. An interval is evaluated only after its publication
deadline (`logical_interval.end + freshness.error_after_minutes`) has passed; until then it is
`pending`, not a failure. The publication time is the instant an independent
product pointer, or a publication-set pointer containing it, became visible to authorized
consumers. `completed_at` and `certified` do not count as publication.

| Indicator | Measurement over eligible intervals in the rolling window |
| --- | --- |
| Freshness lag | `max(0, published_at - logical_interval.end)` in minutes for each first publication. Warn/error thresholds are per-interval objectives; an unpublished interval has no finite lag and is an availability failure. |
| Availability | Number of eligible intervals first published no later than `logical_interval.end + freshness.error_after_minutes`, divided by all eligible intervals. Compare with `availability_percent`. |
| Completeness | Sum of `observed` eligible business units divided by sum of `expected` eligible business units from the declared blocking `completeness_gate`. Compare with `completeness_percent`; the gate defines source authority, filters, dedup/delete accounting, and unit. |

The `completeness_gate` must be a blocking completeness or reconciliation gate with a declared
business-unit name and versioned control reference. Its publication result must carry integer
`0 <= observed <= expected` unit counts. Eligible-interval evidence carries the authoritative
`expected_units` count, including intervals with no publication; a publication's `expected` must
match it. A value such as 99.9% is not meaningful without this denominator. On a zero expected
count, `publish_empty` requires a consumer-visible empty publication; `skip_with_evidence` excludes
the interval only when immutable source evidence proves it was empty. Missing data or a failed
source capture is never an empty interval.

Measure each original eligible interval against its first consumer-visible publication. A later
correction or backfill gets a new publication and a separate correctness incident; it does not
retroactively erase a missed deadline. Report eligible interval count, good interval count,
completeness numerator/denominator, exclusions with evidence, rolling-window result, and error
budget by product and owner. The availability budget is `eligible * (100 - target_percent) / 100`
bad intervals; the completeness budget uses the same formula over expected units. Remaining budget
may be fractional or negative and is not silently rounded. A product with no eligible intervals is
`not_applicable`, not 100%; with eligible intervals but zero expected units, completeness is
`not_applicable` while availability is still evaluated.

The same definitions apply to stream products by declaring bounded measurement intervals in the
schedule reference. `measure.py` provides a portable reference calculation from explicit interval
evidence and publication records; the adopting platform owns the live schedule resolver, monitor,
alert route, and source-control verification. Readiness requires tests with on-time, late, missing,
empty, and corrected intervals.
