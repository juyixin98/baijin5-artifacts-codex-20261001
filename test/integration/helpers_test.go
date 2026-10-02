package integration_test

import (
	"net"

	"coaplab/internal/service"
	"coaplab/internal/transport"
	"coaplab/test/oracle"
)

// netUDPAddr aliases net.UDPAddr so test signatures stay terse.
type netUDPAddr = net.UDPAddr

// harnessServiceOpt forwards a handler wrapper into the service options.
func harnessServiceOpt(w func(transport.Handler) transport.Handler) service.Option {
	return service.WithHandlerWrapper(w)
}

// oracleInspect delegates to the independent oracle (no SUT code involved).
func oracleInspect(dgram []byte) (oracle.MessageView, error) {
	return oracle.Inspect(dgram)
}
