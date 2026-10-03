# main pushes 2, calls provider, adds: 2 + provider()
.object ws_main.o
.section .text.main
.globl main
.export main
main:
pushi 2
call provider
add
halt
