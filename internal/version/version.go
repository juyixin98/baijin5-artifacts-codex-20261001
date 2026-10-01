// Package version exposes build identity used by logs and stored runs.
package version

// Version is bumped on every behavior-changing release of ntpsim.
const Version = "0.1.0"

// ProtocolVersion identifies the on-wire NTP variant implemented here.
// We implement the SNTPv4 message layout (RFC 5905 compatible).
const ProtocolVersion = 4
