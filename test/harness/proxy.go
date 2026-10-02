// Package harness provides the local UDP test harness: an in-process
// controlled service and an IMPAIRMENT PROXY that sits between a raw UDP
// peer and the service. The proxy can drop, delay, duplicate and reorder
// datagrams in either direction and fire hooks on inspected messages — all
// locally, with no real devices.
package harness

import (
	"context"
	"net"
	"sync"
	"time"

	"coaplab/internal/wire"
)

// Direction tags a datagram crossing the proxy.
type Direction int

const (
	// C2S: client -> server (requests).
	C2S Direction = iota
	// S2C: server -> client (responses).
	S2C
)

func (d Direction) String() string {
	if d == C2S {
		return "C2S"
	}
	return "S2C"
}

// Packet is one inspected datagram.
type Packet struct {
	Dir    Direction
	Remote *net.UDPAddr // client address (S2C destination / C2S source)
	Data   []byte
	Msg    *wire.Message // nil when parsing failed
}

// Action tells the proxy what to do with a packet.
type Action int

const (
	ActionPass Action = iota
	ActionDrop
	ActionDelay
	ActionDuplicate
)

// Rule decides a packet's fate. delay is honoured for ActionDelay; duplicate
// sends the packet twice. A rule may also mutate external state via the hook
// (e.g. update the resource representation mid-download).
type Rule func(p Packet) (action Action, delay time.Duration)

// Proxy is a bidirectional UDP impairment proxy.
type Proxy struct {
	client *net.UDPConn // faces the test peer (Listen side)
	server *net.UDPConn // faces the real service

	serverAddr *net.UDPAddr

	mu      sync.Mutex
	clients map[string]*net.UDPAddr // client key -> its address
	rules   []Rule
	stats   Stats
}

// Stats counts observed/outcome packets.
type Stats struct {
	Seen      map[string]int
	Dropped   int
	Delayed   int
	Duplicate int
}

// NewProxy binds an ephemeral client-facing socket and dials the server.
func NewProxy(serverAddr string) (*Proxy, error) {
	saddr, err := net.ResolveUDPAddr("udp", serverAddr)
	if err != nil {
		return nil, err
	}
	lc, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		return nil, err
	}
	dc, err := net.DialUDP("udp", nil, saddr)
	if err != nil {
		_ = lc.Close()
		return nil, err
	}
	p := &Proxy{
		client:     lc,
		server:     dc,
		serverAddr: saddr,
		clients:    map[string]*net.UDPAddr{},
		stats:      Stats{Seen: map[string]int{}},
	}
	return p, nil
}

// ClientAddr is where the test peer sends datagrams (the proxy front door).
func (p *Proxy) ClientAddr() *net.UDPAddr { return p.client.LocalAddr().(*net.UDPAddr) }

// AddRule appends an impairment rule evaluated in order; the first non-Pass
// action wins; otherwise the packet passes.
func (p *Proxy) AddRule(r Rule) {
	p.mu.Lock()
	p.rules = append(p.rules, r)
	p.mu.Unlock()
}

// SnapshotStats returns a copy of counters.
func (p *Proxy) SnapshotStats() Stats {
	p.mu.Lock()
	defer p.mu.Unlock()
	s := Stats{Seen: map[string]int{}, Dropped: p.stats.Dropped, Delayed: p.stats.Delayed, Duplicate: p.stats.Duplicate}
	for k, v := range p.stats.Seen {
		s.Seen[k] = v
	}
	return s
}

// Run pumps both directions until ctx is cancelled.
func (p *Proxy) Run(ctx context.Context) {
	go p.pump(ctx, C2S)
	go p.pump(ctx, S2C)
}

// Close releases sockets.
func (p *Proxy) Close() {
	_ = p.client.Close()
	_ = p.server.Close()
}

func (p *Proxy) pump(ctx context.Context, dir Direction) {
	var conn *net.UDPConn
	if dir == C2S {
		conn = p.client
	} else {
		conn = p.server
	}
	buf := make([]byte, 65535)
	for {
		if ctx.Err() != nil {
			return
		}
		n, raddr, err := conn.ReadFromUDP(buf)
		if err != nil {
			return
		}
		data := append([]byte(nil), buf[:n]...)
		msg, _ := wire.Parse(data)
		var clientAddr *net.UDPAddr
		if dir == C2S {
			clientAddr = raddr
			p.mu.Lock()
			p.clients[raddr.String()] = raddr
			p.mu.Unlock()
		} else {
			// S2C: remote for return is the last registered client; with a
			// single test peer this is unambiguous.
			p.mu.Lock()
			for _, a := range p.clients {
				clientAddr = a
			}
			p.mu.Unlock()
		}
		p.process(Packet{Dir: dir, Remote: clientAddr, Data: data, Msg: msg})
	}
}

func (p *Proxy) process(pkt Packet) {
	p.mu.Lock()
	p.stats.Seen[pkt.Dir.String()]++
	rules := append([]Rule(nil), p.rules...)
	p.mu.Unlock()

	action := ActionPass
	var delay time.Duration
	for _, r := range rules {
		a, d := r(pkt)
		if a != ActionPass {
			action, delay = a, d
			break
		}
	}

	forward := func(b []byte) {
		if pkt.Dir == C2S {
			_, _ = p.server.Write(b)
		} else if pkt.Remote != nil {
			_, _ = p.client.WriteToUDP(b, pkt.Remote)
		}
	}

	p.mu.Lock()
	switch action {
	case ActionDrop:
		p.stats.Dropped++
		p.mu.Unlock()
		return
	case ActionDelay:
		p.stats.Delayed++
		p.mu.Unlock()
		go func() {
			time.Sleep(delay)
			forward(pkt.Data)
		}()
	case ActionDuplicate:
		p.stats.Duplicate++
		p.mu.Unlock()
		forward(pkt.Data)
		go func() {
			time.Sleep(5 * time.Millisecond)
			forward(pkt.Data)
		}()
	default:
		p.mu.Unlock()
		forward(pkt.Data)
	}
}
