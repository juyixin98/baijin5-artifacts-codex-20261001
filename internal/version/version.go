// Package version exposes build identity used in logs and reports.
package version

import "runtime"

const (
	Module  = "funcspect"
	Version = "1.0.0"
)

func GoRuntime() string { return runtime.Version() }
