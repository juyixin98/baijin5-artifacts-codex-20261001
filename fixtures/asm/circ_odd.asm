.object circ_odd.o
.section .text.isodd
.globl isodd
isodd:
jz isodd_zero
pushi 1
sub
call iseven
ret
isodd_zero:
pop
pushi 0
halt
