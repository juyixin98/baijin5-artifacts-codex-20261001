# weak default provider returns 2
.object ws_weak.o
.section .text.provider
.weak provider
provider:
pushi 2
ret
