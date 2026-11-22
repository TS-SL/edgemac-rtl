# One lane versus four lanes

Both configurations use the same RTL and the default 1024-element limit. Every figure on this page was regenerated from this source snapshot, and the regenerated `results/` files matched the committed copies byte for byte. The full measured cycle sweep is in `results/cycles.csv`, and the tool-reported hardware data is in `results/hardware.json`.

## Measured cycles

The cocotb benchmark used seed `0xED6EBEEF` and no input gaps. Start-to-result counts rising-edge intervals from the accepted start to the edge that first makes the result valid. Start-to-ack includes the result handshake. The two preceding configuration writes are excluded.

| Length | Start-to-result, 1 lane | Start-to-result, 4 lanes | No-stall start-to-ack, 1 lane | No-stall start-to-ack, 4 lanes |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 1 | 2 | 2 |
| 3 | 3 | 1 | 4 | 2 |
| 4 | 4 | 1 | 5 | 2 |
| 5 | 5 | 2 | 6 | 3 |
| 7 | 7 | 2 | 8 | 3 |
| 16 | 16 | 4 | 17 | 5 |
| 63 | 63 | 16 | 64 | 17 |
| 128 | 128 | 32 | 129 | 33 |
| 255 | 255 | 64 | 256 | 65 |
| 1024 | 1024 | 256 | 1025 | 257 |

Accepted input beats equaled start-to-result cycles in these no-gap runs. An eight-cycle output stall added eight cycles to acknowledgment in every run. At 1024 elements, the one-lane and four-lane designs accepted 1.00 and 4.00 pairs per input cycle, respectively. The no-stall start-to-ack speedup was 1025/257, approximately 3.99×. Fixed setup, partial final beats, input gaps, and output stalls can lower end-to-end gains.

## Measured mapping resources and estimated clock

| Metric | 1 lane | 4 lanes |
| --- | ---: | ---: |
| Yosys generic cells | 1,466 | 3,256 |
| Generic flip-flop cells | 145 | 145 |
| Signed 8×8 multipliers before logic mapping | 1 | 4 |
| Longest generic topological path, cells | 69 | 76 |
| Routed iCE40 logic cells | 590 | 1,307 |
| Estimated iCE40 internal clock | 88.97 MHz | 71.75 MHz |

Yosys 0.71+34 used identical mapping settings and reported zero structural-check problems. ABC reported combinational input networks because it mapped extracted combinational logic. The flip-flop counts account separately for the sequential state. Four lanes use about 2.22× the generic cells. The added multipliers and beat reduction increase combinational resources while the register count stays constant.

Yosys and nextpnr 0.11.1-92-g5e2718b6 targeted a Lattice iCE40 HX8K CT256, with a 50 MHz target, seed 1, and automatic I/O placement in both runs. Both cleared the 50 MHz internal target in this run. The tool warned that both designs lacked a PCF. The configurations have different top-level I/O widths, and no external board timing was modeled. The generic cell and path counts carry no units of area or time. [nextpnr clock constraints](https://github.com/YosysHQ/nextpnr/blob/main/docs/constraints.md)

If external streams could sustain both respective estimated clocks, the 1024-element no-stall start-to-ack throughput ratio would be approximately 3.22×. This projection combines simulated cycle counts and estimated internal clock rates. No board or silicon measurements were made.
