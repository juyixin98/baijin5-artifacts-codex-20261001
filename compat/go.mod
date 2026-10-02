module modbusfixture/compat

go 1.22

require (
	github.com/mattn/go-sqlite3 v1.14.52
	modbusfixture/client v0.0.0
	modbusfixture/config v0.0.0
	modbusfixture/fixture v0.0.0
	modbusfixture/vectors v0.0.0
)

replace modbusfixture/vectors => ../vectors

replace modbusfixture/config => ../config

replace modbusfixture/fixture => ../fixture

replace modbusfixture/client => ../client
