# Verification plan

| Requirement | Check |
| --- | --- |
| Exact signed product, bias, and final-only saturation | Independent Python oracle, directed cocotb boundary vectors, seeded cocotb transactions, bounded scalar arithmetic comparison with two-bit and full-range INT8 operands |
| 1, 4 and 1024 supported lengths, including partial final beats | Every legal length 1–1024 in both lane configurations, with directed boundary cases and cycle benchmarks |
| Correct accepted element count, completion, and result ownership | cocotb independent scoreboard, SBY control and bounded-response assertions for each lane count |
| Idle, busy, done, cancellation and backpressure | Directed and randomized protocol tests, seeded resets in every transaction phase, first legal restart after acknowledgment, and SBY reset, result stability and transaction-budget checks, bounded and unbounded |
| Previous RESULT readable while the next operation is received | Random RESULT reads during input gaps and beats in the interruption test, and a mutation that zeroes RESULT only while receiving |
| Malformed masks, invalid lengths, invalid accesses, busy writes and error recovery | cocotb exercises every malformed mask for each final-beat width and for a nonfinal beat, and checks random register accesses during live transactions. SBY checks masks and length error status, bounded and unbounded. |
| Reachability under formal reset assumption | SBY covers for stalled result, abort, reset during stall, repeated transaction and partial four-lane beat |
| Equal synthesis flow | Verilator lint, identical Yosys generic mapping and structural check with only LANES changed |
| Test sensitivity | Six simulation mutations, each with an expected failure message, plus two formal refutations: the unbounded 1024-element protocol proof and the full-range INT8 arithmetic check |
| Measured cycles | Measured cocotb handshake counts on deterministic vectors, both immediate and stalled output |

The formal harness requires an initial reset edge, and allows later resets and adversarial inputs. BMC and covers limit the design to eight elements. The PDR proofs run at eight and 1024. Its bounded properties address transaction control and data stability. RTL arithmetic is scored against the independent arbitrary-precision Python oracle across the full configuration range in simulation. A separate 10-step arithmetic harness checks the two-beat cases at lengths two and five with all operands in [-2, 1], arbitrary INT32 bias, and three reachable saturation and in-range paths per lane count. The same harness repeats with full-range INT8 operands using Bitwuzla.
