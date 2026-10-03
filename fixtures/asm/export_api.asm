# api is exported (GC root) though main never calls it; it pulls in data.
.object export_api.o
.section .data.tbl
tbl:
.word 99

.section .text.api
.globl api
.export api
api:
load tbl
halt
