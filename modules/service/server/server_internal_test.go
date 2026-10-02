package server

import (
	"testing"

	"hpacklab.local/hpack"
	"hpacklab.local/service/frame"
)

func TestStripHeadersPadding(t *testing.T) {
	block := []byte("HPACKBLOCK")
	cases := []struct {
		name    string
		flags   byte
		payload []byte
		want    []byte
		wantErr bool
	}{
		{
			name:    "plain",
			flags:   frame.FlagEndHeaders,
			payload: block,
			want:    block,
		},
		{
			name:    "padded",
			flags:   frame.FlagEndHeaders | frame.FlagPadded,
			payload: append(append([]byte{3}, block...), 0xaa, 0xbb, 0xcc),
			want:    block,
		},
		{
			name:    "priority",
			flags:   frame.FlagEndHeaders | frame.FlagPriority,
			payload: append([]byte{0, 0, 0, 0, 1}, block...),
			want:    block,
		},
		{
			name:    "padded and priority",
			flags:   frame.FlagEndHeaders | frame.FlagPadded | frame.FlagPriority,
			payload: append(append([]byte{2, 0, 0, 0, 0, 1}, block...), 0x11, 0x22),
			want:    block,
		},
		{
			name:    "padding longer than payload",
			flags:   frame.FlagPadded,
			payload: []byte{9, 'a'},
			wantErr: true,
		},
		{
			name:    "priority too short",
			flags:   frame.FlagPriority,
			payload: []byte{0, 0},
			wantErr: true,
		},
	}
	for _, c := range cases {
		got, err := stripHeadersPadding(c.payload, c.flags)
		if c.wantErr {
			if err == nil {
				t.Errorf("%s: expected error", c.name)
			}
			continue
		}
		if err != nil {
			t.Errorf("%s: unexpected error: %v", c.name, err)
			continue
		}
		if string(got) != string(c.want) {
			t.Errorf("%s: got %q, want %q", c.name, got, c.want)
		}
	}
}

func TestSettingsHeaderTableSizeShrinksEncoderNotDecoder(t *testing.T) {
	// Build just enough conn to exercise applySettings.
	c := &conn{
		decoder: hpack.NewDecoder(hpack.DecoderOptions{MaxTableSize: 4096, Limits: hpack.DefaultLimits()}),
		encoder: hpack.NewEncoder(hpack.EncoderOptions{MaxTableSize: 4096}),
	}
	// Server-advertised decoder ceiling stays 4096; the client's SETTINGS
	// value of 256 bounds only our encoder.
	payload := []byte{
		0x00, 0x01, 0x00, 0x00, 0x01, 0x00, // HEADER_TABLE_SIZE = 256
	}
	if err := c.applySettings(payload); err != nil {
		t.Fatal(err)
	}
	if c.decoder.DynamicTableMax() != 4096 {
		t.Fatalf("decoder max changed to %d; must stay server-advertised 4096", c.decoder.DynamicTableMax())
	}
	// Encoder now has a pending leading size update to 256.
	wire := c.encoder.EncodeBlock([]hpack.HeaderField{{Name: ":status", Value: "200"}})
	if wire[0]&0xe0 != 0x20 {
		t.Fatalf("encoder did not lead the block with a size update: %02x", wire[0])
	}
}

func TestSettingsRejectsBadFrameSize(t *testing.T) {
	c := &conn{
		decoder: hpack.NewDecoder(hpack.DecoderOptions{MaxTableSize: 4096, Limits: hpack.DefaultLimits()}),
		encoder: hpack.NewEncoder(hpack.EncoderOptions{MaxTableSize: 4096}),
	}
	// MAX_FRAME_SIZE (id 5) below the minimum.
	payload := []byte{0x00, 0x05, 0x00, 0x00, 0x10, 0x00} // 4096 < 16384
	if err := c.applySettings(payload); err == nil {
		t.Fatal("expected rejection of sub-minimum MAX_FRAME_SIZE")
	}
}
