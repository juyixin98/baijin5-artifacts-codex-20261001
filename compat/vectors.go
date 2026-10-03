// Package compat holds compatibility tests: golden vectors transcribed by
// hand from RFC 7541 Appendix C (public, implementation-independent
// reference data) and cross-checks against the independent
// golang.org/x/net/http2/hpack implementation. None of the reference
// answers in this package are produced by the hpacklab core.
package compat

// vector is one header block from RFC 7541 Appendix C with its expected
// decode result and expected dynamic table state afterwards.
type vector struct {
	name string
	hex  string // header block, hex encoded, transcribed from the RFC
	// want is the expected decoded field list, in order.
	want [][2]string
	// wantDynEntries lists the dynamic table contents after the block,
	// newest first, as "name value" pairs. nil means "do not check".
	wantDynEntries [][2]string
	// wantDynSize is the expected dynamic table size in bytes after the
	// block; -1 means "do not check".
	wantDynSize int
}

// rfc7541AppendixC holds the request/response examples of RFC 7541
// Appendix C.3..C.6. C.3/C.4 share one decoder (request examples,
// 4096-byte table); C.5/C.6 use a 256-byte table (response examples).
var (
	// C.3: request examples without Huffman coding.
	c3 = []vector{
		{
			name: "C.3.1 first request",
			hex:  "828684410f7777772e6578616d706c652e636f6d",
			want: [][2]string{
				{":method", "GET"}, {":scheme", "http"}, {":path", "/"},
				{":authority", "www.example.com"},
			},
			wantDynEntries: [][2]string{{":authority", "www.example.com"}},
			wantDynSize:    57,
		},
		{
			name: "C.3.2 second request",
			hex:  "828684be58086e6f2d6361636865",
			want: [][2]string{
				{":method", "GET"}, {":scheme", "http"}, {":path", "/"},
				{":authority", "www.example.com"}, {"cache-control", "no-cache"},
			},
			wantDynEntries: [][2]string{
				{"cache-control", "no-cache"}, {":authority", "www.example.com"},
			},
			wantDynSize: 110,
		},
		{
			name: "C.3.3 third request",
			hex:  "828785bf400a637573746f6d2d6b65790c637573746f6d2d76616c7565",
			want: [][2]string{
				{":method", "GET"}, {":scheme", "https"}, {":path", "/index.html"},
				{":authority", "www.example.com"}, {"custom-key", "custom-value"},
			},
			wantDynEntries: [][2]string{
				{"custom-key", "custom-value"}, {"cache-control", "no-cache"},
				{":authority", "www.example.com"},
			},
			wantDynSize: 164,
		},
	}

	// C.4: request examples with Huffman coding.
	c4 = []vector{
		{
			name: "C.4.1 first request (huffman)",
			hex:  "828684418cf1e3c2e5f23a6ba0ab90f4ff",
			want: [][2]string{
				{":method", "GET"}, {":scheme", "http"}, {":path", "/"},
				{":authority", "www.example.com"},
			},
			wantDynEntries: [][2]string{{":authority", "www.example.com"}},
			wantDynSize:    57,
		},
		{
			name: "C.4.2 second request (huffman)",
			hex:  "828684be5886a8eb10649cbf",
			want: [][2]string{
				{":method", "GET"}, {":scheme", "http"}, {":path", "/"},
				{":authority", "www.example.com"}, {"cache-control", "no-cache"},
			},
			wantDynEntries: [][2]string{
				{"cache-control", "no-cache"}, {":authority", "www.example.com"},
			},
			wantDynSize: 110,
		},
		{
			name: "C.4.3 third request (huffman)",
			hex:  "828785bf408825a849e95ba97d7f8925a849e95bb8e8b4bf",
			want: [][2]string{
				{":method", "GET"}, {":scheme", "https"}, {":path", "/index.html"},
				{":authority", "www.example.com"}, {"custom-key", "custom-value"},
			},
			wantDynEntries: [][2]string{
				{"custom-key", "custom-value"}, {"cache-control", "no-cache"},
				{":authority", "www.example.com"},
			},
			wantDynSize: 164,
		},
	}

	// C.5: response examples without Huffman coding, 256-byte table.
	c5 = []vector{
		{
			name: "C.5.1 first response",
			hex: "4803333032580770726976617465611d4d6f6e2c203231204f637420" +
				"323031332032303a31333a323120474d546e1768747470733a2f2f7777" +
				"772e6578616d706c652e636f6d",
			want: [][2]string{
				{":status", "302"}, {"cache-control", "private"},
				{"date", "Mon, 21 Oct 2013 20:13:21 GMT"},
				{"location", "https://www.example.com"},
			},
			wantDynEntries: [][2]string{
				{"location", "https://www.example.com"},
				{"date", "Mon, 21 Oct 2013 20:13:21 GMT"},
				{"cache-control", "private"},
				{":status", "302"},
			},
			wantDynSize: 222,
		},
		{
			name: "C.5.2 second response",
			hex:  "4803333037c1c0bf",
			want: [][2]string{
				{":status", "307"}, {"cache-control", "private"},
				{"date", "Mon, 21 Oct 2013 20:13:21 GMT"},
				{"location", "https://www.example.com"},
			},
			wantDynEntries: [][2]string{
				{":status", "307"},
				{"location", "https://www.example.com"},
				{"date", "Mon, 21 Oct 2013 20:13:21 GMT"},
				{"cache-control", "private"},
			},
			wantDynSize: 222,
		},
		{
			name: "C.5.3 third response",
			hex: "88c1611d4d6f6e2c203231204f637420323031332032303a31333a32" +
				"3220474d54c05a04677a69707738666f6f3d4153444a4b48514b425a584f" +
				"5157454f50495541585157454f49553b206d61782d6167653d333630303b" +
				"2076657273696f6e3d31",
			want: [][2]string{
				{":status", "200"}, {"cache-control", "private"},
				{"date", "Mon, 21 Oct 2013 20:13:22 GMT"},
				{"location", "https://www.example.com"},
				{"content-encoding", "gzip"},
				{"set-cookie", "foo=ASDJKHQKBZXOQWEOPIUAXQWEOIU; max-age=3600; version=1"},
			},
			wantDynEntries: [][2]string{
				{"set-cookie", "foo=ASDJKHQKBZXOQWEOPIUAXQWEOIU; max-age=3600; version=1"},
				{"content-encoding", "gzip"},
				{"date", "Mon, 21 Oct 2013 20:13:22 GMT"},
			},
			wantDynSize: 215,
		},
	}

	// C.6: response examples with Huffman coding, 256-byte table.
	c6 = []vector{
		{
			name: "C.6.1 first response (huffman)",
			hex: "488264025885aec3771a4b6196d07abe941054d444a8200595040b81" +
				"66e082a62d1bff6e919d29ad171863c78f0b97c8e9ae82ae43d3",
			want: [][2]string{
				{":status", "302"}, {"cache-control", "private"},
				{"date", "Mon, 21 Oct 2013 20:13:21 GMT"},
				{"location", "https://www.example.com"},
			},
			wantDynEntries: [][2]string{
				{"location", "https://www.example.com"},
				{"date", "Mon, 21 Oct 2013 20:13:21 GMT"},
				{"cache-control", "private"},
				{":status", "302"},
			},
			wantDynSize: 222,
		},
		{
			name: "C.6.2 second response (huffman)",
			hex:  "4883640effc1c0bf",
			want: [][2]string{
				{":status", "307"}, {"cache-control", "private"},
				{"date", "Mon, 21 Oct 2013 20:13:21 GMT"},
				{"location", "https://www.example.com"},
			},
			wantDynEntries: [][2]string{
				{":status", "307"},
				{"location", "https://www.example.com"},
				{"date", "Mon, 21 Oct 2013 20:13:21 GMT"},
				{"cache-control", "private"},
			},
			wantDynSize: 222,
		},
		{
			name: "C.6.3 third response (huffman)",
			hex: "88c16196d07abe941054d444a8200595040b8166e084a62d1bffc05a" +
				"839bd9ab77ad94e7821dd7f2e6c7b335dfdfcd5b3960d5af27087f3672c1" +
				"ab270fb5291f9587316065c003ed4ee5b1063d5007",
			want: [][2]string{
				{":status", "200"}, {"cache-control", "private"},
				{"date", "Mon, 21 Oct 2013 20:13:22 GMT"},
				{"location", "https://www.example.com"},
				{"content-encoding", "gzip"},
				{"set-cookie", "foo=ASDJKHQKBZXOQWEOPIUAXQWEOIU; max-age=3600; version=1"},
			},
			wantDynEntries: [][2]string{
				{"set-cookie", "foo=ASDJKHQKBZXOQWEOPIUAXQWEOIU; max-age=3600; version=1"},
				{"content-encoding", "gzip"},
				{"date", "Mon, 21 Oct 2013 20:13:22 GMT"},
			},
			wantDynSize: 215,
		},
	}
)
