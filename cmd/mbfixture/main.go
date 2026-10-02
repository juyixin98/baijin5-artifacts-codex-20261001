// Command mbfixture is the local Modbus TCP master/slave fixture. All
// command logic lives in internal/mbcli; this is only the process entry
// point.
package main

import (
	"os"

	"mbfixture/internal/mbcli"
)

func main() {
	os.Exit(mbcli.Run(os.Args[1:], os.Stdout, os.Stderr))
}
