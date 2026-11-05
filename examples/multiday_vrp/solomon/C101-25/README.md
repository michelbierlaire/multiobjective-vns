# Solomon C101, 25 customers

`C101.txt` is the Solomon C101 instance with the depot and the first 25
customers, copied in the original Solomon text format. The 25-customer
Solomon variant is defined by retaining the first 25 customers of the
100-customer instance.

This is a clustered-customer instance with the type-1 (narrow-window) data.
It has vehicle capacity 200 and a fleet limit of 25. The published
distance-optimal result is 191.3 using 3 vehicles.

The benchmark convention used for comparison is Euclidean arc distance
truncated to one decimal, with total distance reported to one decimal. Solomon
time windows are hard constraints in the benchmark. The package-format input
bundle in [`input/`](input/README.md) records how C101-25 is translated to the
VRP model, including its soft-penalty treatment of time windows and the
service-duration adjustment needed to preserve the Solomon latest-start
convention. For direct benchmark comparison, use plans with zero window and
availability penalties.

The bundle manifest is [`input/instance.toml`](input/instance.toml). Its
ScenarioBank is in [`input/scenario-bank/`](input/scenario-bank/).

Sources:

- [CVRPLIB C101 instance](https://galgos.inf.puc-rio.br/cvrplib/en/download/instance/380)
- [Solomon benchmark overview](https://www.sintef.no/projectweb/top/vrptw/solomon-benchmark/)
- He, Irnich, and Song, [Table 3: optimal costs and fleet sizes](https://optimization-online.org/wp-content/uploads/2018/03/6509.pdf)
