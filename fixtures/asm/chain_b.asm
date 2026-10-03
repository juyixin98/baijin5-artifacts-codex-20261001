.object chain_b.o
.section .text.b
.globl b
b:
call c
pushi 1
add
ret
