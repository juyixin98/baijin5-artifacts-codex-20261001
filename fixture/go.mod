module modbusfixture/fixture

go 1.22

require (
	github.com/mattn/go-sqlite3 v1.14.52
	modbusfixture/config v0.0.0
	modbusfixture/core v0.0.0
	modbusfixture/mbap v0.0.0
)

replace modbusfixture/mbap => ../mbap

replace modbusfixture/core => ../core

replace modbusfixture/config => ../config
