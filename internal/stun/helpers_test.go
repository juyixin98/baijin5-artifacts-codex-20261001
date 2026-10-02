package stun_test

import (
	"encoding/json"
	"net"
)

func jsonUnmarshal(b []byte, v any) error { return json.Unmarshal(b, v) }

func parseIP(s string) net.IP { return net.ParseIP(s) }
