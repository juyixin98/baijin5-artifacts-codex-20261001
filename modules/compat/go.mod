module hpacklab.local/compat

go 1.22

require (
	golang.org/x/net v0.33.0
	hpacklab.local/hpack v0.0.0
)

require hpacklab.local/codec v0.0.0 // indirect

replace (
	hpacklab.local/codec => ../codec
	hpacklab.local/hpack => ../hpack
)
