.object circ_even.o
.section .text.iseven
.globl iseven
.export iseven
iseven:
jz iseven_zero
pushi 1
sub
call isodd
ret
iseven_zero:
pop
pushi 1
halt
