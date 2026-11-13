# C101-25 package input with three vehicles

This bundle translates the depot and first 25 customers of Solomon C101 into
the VRP package's version-1 input format. `../C101.txt` preserves the original
benchmark records.

## Fleet restriction

`vehicles.csv` contains only `vehicle-01`, `vehicle-02`, and `vehicle-03`,
each with cargo capacity 200. Their three matching driver records, vehicle
availability rows, personnel availability rows, overnight depot records, and
vehicle-package compatibility rows are retained. Every package is compatible
with each of the three vehicles.

All 25 package requests are mandatory and have total demand 460. Two vehicles
have total capacity 400, so every feasible plan must use all three vehicles.
Each vehicle has one tour per day. The vehicle count is fixed by the fleet and
capacity constraints; it is not declared as an objective. The objectives are
total distance, time-window violation, and planned waiting time.

## Translation

- Solomon node IDs `0` through `25` are retained; node `0` is the depot.
- Each customer becomes a package with its source demand as load, pickup at
  node `0`, and delivery at the customer's node.
- The manifest declares all 25 pickups as one symmetry group. Their order at
  the shared depot has no effect on this instance, so identity resolution
  treats qualifying consecutive pickup permutations as the same plan.
- Customer service duration is 90 minutes. The delivery-window upper endpoint
  is the Solomon latest service-start time plus service duration, matching the
  package evaluator's full-service window convention.
- Vehicle and driver availability ends at minute 1236. Availability and
  service-window violations are soft penalties recorded in the separate
  `time-window-violation` objective. For benchmark comparison, select plans
  with zero violation value.
- The ScenarioBank contains one scenario with probability 1.0 and constant
  travel times. Arc travel times are Euclidean distances truncated to one
  decimal, matching the Solomon/CVRPLIB convention.
- The planning date is 2025-01-02 in `Europe/Zurich`; times are represented in
  minutes from the planning day's midnight.

CSV files contain only their exact schema headers and data rows. Explanatory
comments are in this README and the TOML manifests.

Load the instance with:

```python
from multiobjective_vns.multiday_vrp import load_vrp_application

application = load_vrp_application('instance.toml')
```

References: [CVRPLIB Solomon benchmark conventions](https://galgos.inf.puc-rio.br/cvrplib/index.php/en/vrptw), [Solomon benchmark overview](https://www.sintef.no/projectweb/top/vrptw/solomon-benchmark/).
