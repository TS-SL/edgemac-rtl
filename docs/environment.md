# Toolchain and environment

## Pinned tools

All hardware tools come from one OSS CAD Suite release, `2026-11-14`. The Python packages are pinned in `requirements-sim.txt`.

| Tool | Version used for the recorded results (Windows x64 build) |
| --- | --- |
| CPython | 3.12.10 |
| cocotb | 2.1.0 |
| Icarus Verilog and vvp | 14.0 development build, s20260301-996-g7025c6613-dirty |
| Verilator (lint only) | 5.055 development build, v5.054-260-gb9f22851c |
| Yosys, including ABC | 0.71+34 (e419c4eef) |
| SymbiYosys | v0.70-9-g79fc9ed |
| Z3 | 4.15.5 |
| Bitwuzla | 0.9.1 (required by the full-range INT8 arithmetic tasks) |
| nextpnr-ice40 | 0.11.1-92-g5e2718b6 |

`python scripts/check.py env` prints the platform, the Python and cocotb versions, and the first version line from each tool. It exits nonzero if any required tool is missing. It also reports whether the optional `openroad` and `openlane` flows are present. Neither was used.

Suite archives and SHA-256 digests (from the upstream [release page](https://github.com/YosysHQ/oss-cad-suite-build/releases/tag/2026-11-14)):

| Platform | Asset | SHA-256 |
| --- | --- | --- |
| Linux x64 | `oss-cad-suite-linux-x64-20261114.tgz` | `25d1960bc0f002c43b3579423c4e622bcffed49b7bb8b1f02df6426b1bdd2f26` |
| Windows x64 | Windows x64 archive of the same release | `3928bd7a49c6ba4cee27205b9df9f41829a518398db8abe964b8378206232d97` |

## Windows notes

- On Windows the Verilator executable is `verilator_bin`. `check.py` selects it automatically.
- The native `iverilog` driver crashed with status `0xC0000005` during piped compilation, in this suite's build and in MSYS2 Icarus 13.0. `scripts/use-windows-suite.ps1` sets `EDGEMAC_ICARUS_SPLIT=1`. `check.py` then preprocesses with `iverilog -E` and compiles with `ivl` as two separate, checked steps. Each step, and the vvp run under cocotb, must succeed. Other platforms use the standard cocotb Icarus runner.
- `check.py` points ABC's temporary files at `build/`, because ABC failed on a temporary directory whose path contained `#`.

## Linux notes

The CI workflow installs the Linux suite, verifies its digest, and appends its `bin/` to `PATH` after `setup-python`. The pinned CPython 3.12 therefore stays first, and cocotb uses it rather than the suite's bundled Python.

- [cocotb simulator support](https://docs.cocotb.org/en/v2.0.0/simulator_support.html)
