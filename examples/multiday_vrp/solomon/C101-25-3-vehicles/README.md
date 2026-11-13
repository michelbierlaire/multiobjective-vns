# Solomon C101-25 with a three-vehicle fleet

This is a separate C101-25 instance bundle based on the first 25 customers of
Solomon's C101 instance. The original benchmark data are in `C101.txt`; the
package input bundle is in [`input/`](input/README.md).

The fleet contains exactly three vehicles, each with capacity 200. All 25
package requests are mandatory, and their total load is 460. Since two
vehicles can carry at most 400 load and each vehicle has one tour per day, all
three vehicles are required in every feasible plan. The vehicle count is fixed
by the fleet and capacity constraints, so it is not an objective.

The Solomon benchmark reports a distance-optimal three-vehicle solution with
distance 191.3. For direct comparison, use plans with zero value for the
separate time-window-violation objective; total planned waiting time is an
additional Pareto coordinate used to distinguish plans with equal distance.
See the input README for the translation conventions and source references.

## Route-cluster reassignment follow-up

The earlier 11-hour run finished with a best zero-violation distance of 359.8.
Its catalog contained the window-compatible package-cluster reassignment
Operator, with maximum neighborhood size 5. In the run summary, its
`total-distance`-targeted variant was called seven times at size 1, produced 45
candidates, and had no Pareto admissions. No calls for that variant at sizes
2–5 were recorded. This is evidence that the existing package-unit move did
not explore larger distance-improving reassignments in this run; it did not
include the new route-level Operator described below. See the
[11-hour run summary](run-logs-11h-20261110-seed-0/d7d2a74e2b694d2b8cd47d9683b1800b.summary.txt).

The implemented route-level Operator forms customer groups from static
shortest travel times and time-window compatibility, independently of their
current vehicle assignments. It maps selected groups to active vehicles,
rebuilds affected routes with time-window slack and travel-time regret
insertion, and returns complete plans for evaluation. The default catalog also
offers the ordered cluster-rebuild → 2-opt composite. This first version
supports single-day plans with one route per active vehicle, depot pickups,
and package pickup/delivery stops. It skips plans with transfers, other service
stops, assigned teams, post-service waits, breaks, tour-end markers, or multiple
routes per vehicle. No new C101 search result is available yet.

For this instance, the expected groups are customers `1–11`, `12–19`, and
`20–25`, with loads `160`, `190`, and `110`. Use these known groups as a
diagnostic; do not hard-code them into the generic Operator.

Evaluate the published route plan separately to confirm distance `191.3` and
zero time-window violation. For discovery experiments, keep it out of the
initial Pareto set. From the same greedy initial plan, compare route-cluster
reassignment alone, reassignment followed by one 2-opt stage, and a variant
with a second sequential 2-opt pass. Record the best zero-violation distance,
distinct route assignments, and the neighborhood sizes actually exercised.
The operator and experiment design are specified in
[`multiday_vrp_operators.md`](../../../../docs/design/multiday_vrp_operators.md).
