package sim

import (
	"errors"
	"time"

	"ntpsim/internal/transport"
)

// ErrVirtualTimeout is retained as a distinct sentinel for fabric-level callers
// that want to distinguish virtual timeouts, but Recv maps it onto the
// transport-level timeout so the client FSM classifies it uniformly.
var ErrVirtualTimeout = errors.New("sim: virtual receive deadline expired")

// DatagramEndpoint implements transport.Datagram against the virtual fabric.
type DatagramEndpoint struct {
	env  *Environment
	name string
}

// NewDatagramEndpoint is the exported constructor.
func NewDatagramEndpoint(e *Environment, name string) *DatagramEndpoint {
	return &DatagramEndpoint{env: e, name: name}
}

// Send enqueues the datagram into the fabric. It returns once the pump has
// ingested it at the current virtual instant.
func (d *DatagramEndpoint) Send(b []byte, to string) error {
	ack := make(chan struct{})
	d.env.reqs <- sendReq{from: d.name, to: to, data: b, reply: ack}
	<-ack
	return nil
}

// Recv blocks until a datagram arrives or the deadline expires. The pump parks
// virtual time at the delivery/deadline instant until this call returns its
// result, so the caller's subsequent Now() reads the correct virtual time.
func (d *DatagramEndpoint) Recv(deadline time.Time) ([]byte, string, error) {
	ch := make(chan recvResult, 1)
	ack := make(chan struct{})
	d.env.reqs <- recvReq{box: d.name, deadline: deadline, ch: ch, ack: ack}
	r := <-ch
	// Observed: release the pump to advance virtual time again.
	close(ack)
	if r.err != nil {
		if errors.Is(r.err, ErrVirtualTimeout) {
			return nil, "", transport.ErrTimeout
		}
		return nil, "", r.err
	}
	return r.data, r.from, nil
}

// LocalAddr returns the endpoint name.
func (d *DatagramEndpoint) LocalAddr() string { return d.name }

// Close is a no-op on the virtual fabric.
func (d *DatagramEndpoint) Close() error { return nil }
