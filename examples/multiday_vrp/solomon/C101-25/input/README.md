# C101-25 package input

This directory contains C101-25 translated into the VRP package's version-1
input bundle. The original benchmark text file remains next to this directory
as `../C101.txt`.

## Translation

- Solomon location IDs `0` through `25` are the VRP node IDs; node `0` is the
  depot. The source coordinates are retained in `C101.txt`.
- Each customer becomes a package with its source demand as load, a zero-time
  pickup at node `0`, and a delivery at the customer's node. Each vehicle
  starts at the same depot with capacity 200; the source fleet limit of 25 is
  represented by 25 vehicles and one driver per vehicle.
- The manifest declares all 25 pickups as one symmetry group. Their order at
  the shared depot has no effect on this instance, so identity resolution
  treats qualifying consecutive pickup permutations as the same plan.
- The customer service duration is copied from the source record (90 minutes).
  Solomon's due time is the latest service-start time. The package evaluator
  requires the complete service interval to fit in its selected window, so the
  delivery-window upper endpoint is the source due time plus service duration.
  This preserves the source's permitted service-start interval.
- The benchmark's vehicle availability ends at its depot due time, minute
  1236. The same interval is assigned to each driver. In the package model,
  availability and time-window violations are soft penalties. The objective
  file records them in a separate `time-window-violation` objective, measured
  in violated minutes. Vehicle count, distance, and planned waiting time are
  separate unadjusted objectives. For direct benchmark comparison, select
  plans whose violation objective is zero.
- Driver records are identical in every assignment-relevant attribute. The
  input model still records a driver for each route, while the default
  catalog omits driver-reassignment and personnel-assignment Operators for
  this symmetric personnel profile.
- The ScenarioBank has one scenario with probability 1.0 and constant travel
  times. Each directed arc's travel time is its Euclidean distance, truncated
  to one decimal place, matching the Solomon/CVRPLIB convention. The two
  benchmark objectives report vehicles used and total distance separately.
  The package reports soft temporal violations in a separate third objective
  and adds total planned waiting time as a fourth objective. This package
  returns a Pareto set; for benchmark comparison, filter to zero-violation
  plans and compare vehicle count and distance.
- The planning date is set to 2025-01-02 in `Europe/Zurich`; it supplies the
  calendar context for timestamps and does not alter the benchmark's
  minute-based times.

CSV tables use only their exact schema headers: comment rows would be treated
as data and rejected by the loader. Explanatory comments are therefore in the
TOML manifests and this README.

Load the instance with:

```python
from multiobjective_vns.multiday_vrp import load_vrp_application

application = load_vrp_application('instance.toml')
```

Run that code with this directory as the current working directory, or pass
an absolute path to `instance.toml`.

References: [CVRPLIB Solomon benchmark conventions](https://galgos.inf.puc-rio.br/cvrplib/index.php/en/vrptw), [Solomon benchmark overview](https://www.sintef.no/projectweb/top/vrptw/solomon-benchmark/).
