# Verification results

Each result was produced by `python scripts/check.py all`, `python scripts/check_mutations.py`, and `python scripts/export_evidence.py`, run from a clean copy of this source snapshot on Windows x64 with the toolchain in [environment.md](environment.md). Outcome labels: VERIFIED means the check executed and passed, MEASURED means a tool-reported count, and ESTIMATED means a model-based figure.

## Toolchain and oracle

VERIFIED. `check.py env` found every required tool and printed its version. `check.py oracle` ran three unit-test methods: twelve hand-calculated arithmetic fixtures, ten invalid-input fixtures, and a seeded 1,024-element cancellation case. `check.py smoke` compiled a signed 8×8 multiplier and checked four signed products under cocotb.

## Lint and simulation

VERIFIED. Verilator `--lint-only -Wall` passed for `LANES=1` and `LANES=4`. Four cocotb tests passed for each lane count, with none skipped:

| Test | What it checks |
| --- | --- |
| `directed_arithmetic` | 17 directed vectors. They cover INT8 extremes, each INT32 bound and one value inside it, intermediate sums that cross a bound and return, and 1,024-element extremes in both directions. RESULT and STATUS are read back after each vector. |
| `randomized_protocol` | Every illegal register access. Every malformed mask for each legal beat width. Errors raised and cleared on the same edge. Reset during receipt, on the final operand, and during a held result. The earliest legal restart. A 40-cycle input gap. 64 seeded transactions (seed `0xED6E2026`) with random gaps and output stalls. |
| `randomized_interruptions` | 300 seeded transactions (seed `0xED6E2026 ^ 0x5EED`). Random register accesses (reads, busy writes, error clears, invalid addresses) arrive on random edges, each response predicted from the specification. Random resets also arrive. The test fails if no reset lands in a nonfinal beat, a final beat, a held result, or an acknowledgment. RESULT reads during input gaps and beats check that the previous result stays readable while the next operation is received. |
| `all_vector_lengths` | One vector at every length from 1 to 1,024 (seed `0xED6E0400`), scored against the oracle |

The scoreboard decodes operands from accepted handshakes and computes each expected value with the Python oracle before it compares the DUT output.

## Formal

Tools: SymbiYosys v0.70-9-g79fc9ed, the `smtbmc` engine with Z3 4.15.5 or Bitwuzla 0.9.1, and `abc pdr` from Yosys 0.71+34. The harness assumes reset on the first edge. After that, resets, register requests, operand beats, and sink readiness are all unconstrained.

VERIFIED. Protocol harness `verification/formal/edgemac.sby`: eight tasks, all PASS.

| Task, for each lane count | Engine | Vector-length limit | Bound | Properties |
| --- | --- | ---: | --- | --- |
| `bmc` | smtbmc z3 | 8 | 16 steps | Safety and bounded response |
| `cover` | smtbmc z3 | 8 | 16 steps | 5 reachability witnesses, plus a partial final beat for 4 lanes |
| `prove` | abc pdr | 8 | Unbounded | Safety and bounded response |
| `prove_full` | abc pdr | 1,024 | Unbounded | Safety only |

The safety properties cover the following:
- result stability and ownership until acknowledgment
- no premature or duplicated result
- mask accounting
- reset gating and recovery
- the STATUS bits
- the busy and idle transitions

The bounded-response properties state that operands remain only while fewer than ⌈N/LANES⌉ legal beats have been accepted, and that a pending result follows exactly that many. They are proven unbounded at the 8-element limit. At 1,024, PDR had not converged on them after about nine minutes, so the `prove_full` task disables them (`CHECK_RESPONSE=0`). Instead, simulation checks the beat count and immediate acceptance at every length from 1 to 1,024. This is a bounded-response argument, not an LTL liveness proof.

| Cover witness | 1 lane | 4 lanes |
| --- | --- | --- |
| Stalled result | Step 5 | Step 5 |
| Malformed-mask abort | Step 5 | Step 5 |
| Reset interrupts a stalled result | Step 6 | Step 6 |
| Second result after an acknowledgment | Step 8 | Step 8 |
| Legal three-element final beat | Not applicable | Step 4 |
| Completed result at the 8-element limit | Reached | Reached |

VERIFIED. Arithmetic harness `verification/formal/arithmetic.sby`: eight tasks, all PASS. The harness drives two legal beats and compares the saturated DUT output with a 34-bit scalar reference sum. The bias is any INT32 value. Covers reach lower saturation, upper saturation, and an in-range zero.

| Task, for each lane count | Operand domain | Solver | Transaction |
| --- | --- | --- | --- |
| `bmc`, `cover` | [−2, 1] | Z3 | Length 2 (1 lane) or 5 (4 lanes), 10-step bound |
| `bmc_int8`, `cover_int8` | [−128, 127] | Bitwuzla | Same |

The four-lane length-5 transaction ends with a partial beat, and three masked-off lanes carry unconstrained payloads. The arithmetic proofs say nothing about longer vectors. Simulation covers those.

## Mutation checks

VERIFIED. `scripts/check_mutations.py` copies the RTL, injects one defect, and requires the named test to fail with a specific message. The unmodified RTL must pass the same test first.

| Defect | Lanes | Caught by | Expected message |
| --- | ---: | --- | --- |
| Wraparound instead of saturation | 1 | `directed_arithmetic` | `expected 2147483647, got -2147483648` |
| Lane mask ignored in the product | 4 | `directed_arithmetic` | `expected 0, got 49152` |
| `result_valid` drops while stalled | 4 | `randomized_protocol` | `assert None == -32` |
| Activation zero-extended | 4 | `directed_arithmetic` | `expected 16383, got -16385` |
| Completion one beat early | 1 | `directed_arithmetic` | `premature result` |
| RESULT reads zero while the next operation is received | 4 | `randomized_interruptions` | `expected register response` |

The script also requires two formal tasks to fail with a counterexample. `one_prove_full` must refute the `result_valid` defect, and `four_bmc_int8` must refute the zero-extension defect. Every mutant is written under `build/mutations/`. The source RTL is never modified.

## Synthesis, timing, and cycles

MEASURED. `check.py synth` ran the same Yosys generic mapping script, with `check -assert`, for both lane counts. `check.py perf` produced 20 checked rows per lane count (seed `0xED6EBEEF`): 10 vector lengths, each with and without an 8-cycle output stall. ESTIMATED. `check.py timing` placed and routed both configurations for an iCE40 HX8K and reported an estimated internal clock. [hardware-comparison.md](hardware-comparison.md) has the figures and their conditions.

## Reproducibility

VERIFIED. All of the checks above were run from a clean export of this snapshot, starting with no `build/` directory. `export_evidence.py` then regenerated `results/cycles.csv` and `results/hardware.json` byte-for-byte identical to the committed copies. The same regeneration had matched on an earlier audited revision. Hosted Linux results differ in their tool version strings, so CI uploads them as artifacts and does not compare them byte-for-byte.
