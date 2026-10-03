# strong provider overrides weak and returns 40
.object ws_strong.o
.section .text.provider
.globl provider
provider:
pushi 40
ret
