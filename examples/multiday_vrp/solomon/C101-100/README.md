# Solomon C101, 100 customers

`C101.txt` is the full 100-customer Solomon C101 instance, copied in the
original Solomon text format.

This is a clustered-customer instance with the type-1 (narrow-window) data.
It has vehicle capacity 200 and a fleet limit of 25. The published
distance-optimal result is 827.3 using 10 vehicles.

The benchmark convention used for comparison is Euclidean arc distance
truncated to one decimal, with total distance reported to one decimal. Solomon
time windows are hard constraints in the benchmark. The current multi-day VRP
application's window-penalty model and service-finish convention need to be
accounted for when preparing a comparable instance.

Sources:

- [CVRPLIB C101 instance](https://galgos.inf.puc-rio.br/cvrplib/en/download/instance/380)
- [Solomon benchmark overview](https://www.sintef.no/projectweb/top/vrptw/solomon-benchmark/)
- He, Irnich, and Song, [Table 3: optimal costs and fleet sizes](https://optimization-online.org/wp-content/uploads/2018/03/6509.pdf)
