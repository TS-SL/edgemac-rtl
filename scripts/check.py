"""Run reproducible EdgeMAC verification commands from any directory."""

import argparse
import json
import os
import platform
import re
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
TOP = "edgemac_top"
RTL = ROOT / "rtl" / "edgemac_top.sv"
COCOTB = ROOT / "verification" / "cocotb"
LANE_COUNTS = (1, 4)
# Protocol: bounded BMC and covers at eight elements, then unbounded PDR proofs
# at eight and at the production 1024-element limit.
FORMAL_SUITES = (
    ("edgemac", "", ("bmc", "cover", "prove", "prove_full")),
    # Arithmetic: two-bit operands with Z3, then full-range INT8 operands with Bitwuzla.
    ("arithmetic", "arithmetic_", ("bmc", "cover", "bmc_int8", "cover_int8")),
)


def run(*command: str, env=None, timeout=120) -> None:
    print(subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True, timeout=timeout)


def verilator_command() -> str:
    return "verilator_bin" if os.name == "nt" else "verilator"


def environment() -> None:
    from importlib.metadata import PackageNotFoundError, version

    print(f"{platform.platform()} ({platform.machine()}), {os.cpu_count()} logical CPUs")
    print(f"Python {platform.python_version()}, {shutil.disk_usage(ROOT).free // (1024**3)} GiB free")
    failures = []
    try:
        print(f"cocotb {version('cocotb')}")
    except PackageNotFoundError:
        failures.append("cocotb (Python package)")

    programs = (
        ("iverilog", "-V"), ("vvp", "-V"), (verilator_command(), "--version"),
        ("yosys", "-V"), ("sby", "--version"), ("z3", "--version"), ("bitwuzla", "--version"),
        ("nextpnr-ice40", "--version"),
    )
    for program, flag in programs:
        if shutil.which(program) is None:
            failures.append(program)
            continue
        probe = subprocess.run(
            [program, flag], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        if probe.returncode:
            failures.append(f"{program} (version command exited {probe.returncode})")
        else:
            print(next((line for line in probe.stdout.splitlines() if line.strip()), program))
    for program in ("openroad", "openlane"):
        print(f"Optional {program}: " + ("available" if shutil.which(program) else "absent"))
    if failures:
        raise RuntimeError("Unavailable required tools: " + ", ".join(failures))


def oracle() -> None:
    run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v")


def split_icarus_compile(sources, top, build_dir, parameters):
    """Use checked Icarus stages on Windows hosts where the driver pipe crashes."""
    driver = Path(shutil.which("iverilog"))
    compiler_dir = driver.parent.parent / "lib" / "ivl"
    preprocessed = build_dir / "preprocessed.sv"
    config = build_dir / "iverilog.cfg"
    env = dict(os.environ, IVERILOG_ICONFIG=str(config))
    args = [f"-P{top}.{key}={value}" for key, value in parameters.items()]
    run(str(driver), "-E", "-g2012", "-s", top, *args,
        "-o", str(preprocessed), *(str(path) for path in sources), env=env)
    lines = config.read_text(encoding="utf-8").splitlines()
    if sum(line.startswith("out:") for line in lines) != 1:
        raise RuntimeError("Icarus preprocessing did not produce a valid compiler configuration")
    config.write_text(
        "\n".join(
            f"out:{(build_dir / 'sim.vvp').as_posix()}" if line.startswith("out:") else line
            for line in lines if not line.startswith("ivlpp:")
        ) + "\n",
        encoding="utf-8",
    )
    run(str(compiler_dir / "ivl.exe"), "-C", str(config),
        "-C", str(compiler_dir / "vvp.conf"), str(preprocessed))


def simulate(
    sources, top, test_module, test_source_dir, build_dir, tests, parameters=None, extra_env=None,
):
    from cocotb_tools.runner import get_runner

    for program in ("iverilog", "vvp"):
        if shutil.which(program) is None:
            raise RuntimeError(f"{program} is required on PATH")
    parameters = parameters or {}
    build_dir.mkdir(parents=True, exist_ok=True)
    runner = get_runner("icarus")
    if os.name == "nt" and os.getenv("EDGEMAC_ICARUS_SPLIT") == "1":
        split_icarus_compile(sources, top, build_dir, parameters)
    else:
        runner.build(
            sources=sources,
            hdl_toplevel=top,
            build_dir=build_dir,
            parameters=parameters,
            always=True,
        )
    python_path = sys.path.copy()
    try:
        sys.path[:0] = [str(test_source_dir), str(ROOT)]
        filter_expression = "(?:" + "|".join(map(re.escape, tests)) + ")$"
        results = runner.test(
            hdl_toplevel=top,
            test_module=test_module,
            hdl_toplevel_lang="verilog",
            build_dir=build_dir,
            test_dir=build_dir,
            seed=1,
            test_filter=filter_expression,
            extra_env=extra_env or {},
        )
    finally:
        sys.path[:] = python_path

    cases = ET.parse(results).findall(".//testcase")
    for name in tests:
        candidates = [case for case in cases if case.get("name") == name]
        if len(candidates) != 1 or any(
            child.tag in {"failure", "error", "skipped"} for child in candidates[0]
        ):
            raise RuntimeError(f"Required cocotb test {name} did not pass exactly once")


def smoke() -> None:
    source_dir = ROOT / "verification" / "smoke"
    simulate(
        [source_dir / "signed_multiplier.sv"], "signed_multiplier", "smoke_test",
        source_dir, ROOT / "build" / "smoke", ("signed_products",),
    )


def lint(lanes: int) -> None:
    verilator = verilator_command()
    run(verilator, "--lint-only", "-Wall", "--top-module", TOP,
        f"-GLANES={lanes}", RTL.relative_to(ROOT).as_posix())


def sim(lanes: int, directed_only: bool = False) -> None:
    tests = ("directed_arithmetic",)
    if not directed_only:
        tests += ("randomized_protocol", "randomized_interruptions", "all_vector_lengths")
    simulate(
        [RTL], TOP, "edgemac_test", COCOTB, ROOT / "build" / "sim" / f"lanes-{lanes}",
        tests, {"LANES": lanes},
    )


def formal(lanes: int) -> None:
    label = {1: "one", 4: "four"}[lanes]
    for suite, prefix, modes in FORMAL_SUITES:
        for mode in modes:
            task = f"{label}_{mode}"
            run("sby", "-f", "-d", f"build/formal/{prefix}{task}",
                f"verification/formal/{suite}.sby", task)


def synth(lanes: int) -> None:
    build_dir = ROOT / "build" / "synthesis"
    build_dir.mkdir(parents=True, exist_ok=True)
    script = build_dir / f"generic-{lanes}.ys"
    stats = build_dir / f"generic-{lanes}.json"
    log = build_dir / f"generic-{lanes}.log"
    stats.unlink(missing_ok=True)
    template = (ROOT / "synthesis" / "generic.ys").read_text(encoding="utf-8")
    script.write_text(
        template.replace("@LANES@", str(lanes)).replace(
            "@STATS@", stats.relative_to(ROOT).as_posix()
        ),
        encoding="utf-8",
    )
    env = dict(os.environ, TMP=str(build_dir), TEMP=str(build_dir), TMPDIR=str(build_dir))
    run("yosys", "-Q", "-T", "-q", "-l", log.relative_to(ROOT).as_posix(),
        "-s", script.relative_to(ROOT).as_posix(), env=env)
    report = json.loads(stats.read_text(encoding="utf-8"))
    cells = report["design"]["num_cells"]
    print(f"{lanes} lane(s): {cells} generic cells, see {stats.relative_to(ROOT)}")


def timing(lanes: int) -> None:
    build_dir = ROOT / "build" / "timing"
    build_dir.mkdir(parents=True, exist_ok=True)
    relative_dir = build_dir.relative_to(ROOT).as_posix()
    netlist = f"{relative_dir}/ice40-{lanes}.netlist.json"
    report = f"{relative_dir}/ice40-{lanes}.timing.json"
    (ROOT / report).unlink(missing_ok=True)
    env = dict(os.environ, TMP=str(build_dir), TEMP=str(build_dir), TMPDIR=str(build_dir))
    script = (
        f"read_verilog -sv rtl/edgemac_top.sv; chparam -set LANES {lanes} {TOP}; "
        f"synth_ice40 -top {TOP} -json {netlist}; check -assert"
    )
    run("yosys", "-Q", "-T", "-q", "-l", f"{relative_dir}/ice40-{lanes}.yosys.log",
        "-p", script, env=env)
    run("nextpnr-ice40", "--hx8k", "--package", "ct256", "--json", netlist,
        "--freq", "50", "--seed", "1", "--pcf-allow-unconstrained", "--timing-allow-fail",
        "--report", report, "--log", f"{relative_dir}/ice40-{lanes}.nextpnr.log", "--quiet")
    try:
        clocks = json.loads((ROOT / report).read_text(encoding="utf-8"))["fmax"]
        if len(clocks) != 1:
            raise ValueError("expected a single constrained clock")
        estimate = next(iter(clocks.values()))["achieved"]
    except (KeyError, ValueError) as error:
        raise RuntimeError(f"Invalid timing report {report}: {error}") from error
    print(f"{lanes} lane(s): {estimate:.2f} MHz estimated on iCE40 HX8K CT256, see {report}")


def perf(lanes: int) -> None:
    build_dir = ROOT / "build" / "perf" / f"lanes-{lanes}"
    benchmark = build_dir / "edgemac-perf.json"
    benchmark.unlink(missing_ok=True)
    simulate(
        [RTL], TOP, "edgemac_test", COCOTB, build_dir,
        ("benchmark_cycles",), {"LANES": lanes},
        {"EDGEMAC_PERF_JSON": str(benchmark)},
    )
    report = json.loads(benchmark.read_text(encoding="utf-8"))
    if report["lanes"] != lanes or not report["rows"]:
        raise RuntimeError(f"Invalid benchmark data in {benchmark}")
    print(f"{lanes} lane(s): {len(report['rows'])} measurements, see {benchmark.relative_to(ROOT)}")


def main() -> int:
    shared_actions = {"env": environment, "oracle": oracle, "smoke": smoke}
    lane_actions = {
        "lint": lint, "sim": sim, "formal": formal, "synth": synth,
        "perf": perf, "timing": timing,
    }
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=[*shared_actions, *lane_actions, "all"])
    parser.add_argument("--lanes", type=int, choices=LANE_COUNTS)
    parser.add_argument("--directed-only", action="store_true")
    args = parser.parse_args()
    if args.directed_only and args.check != "sim":
        parser.error("--directed-only applies to sim")
    if args.lanes and args.check not in lane_actions:
        parser.error("--lanes applies to per-configuration checks")
    lanes_to_run = (args.lanes,) if args.lanes else LANE_COUNTS
    try:
        if args.check == "all":
            for action in shared_actions.values():
                action()
            for action in lane_actions.values():
                for lanes in lanes_to_run:
                    action(lanes)
        elif args.check in shared_actions:
            shared_actions[args.check]()
        elif args.check == "sim":
            for lanes in lanes_to_run:
                sim(lanes, args.directed_only)
        else:
            for lanes in lanes_to_run:
                lane_actions[args.check](lanes)
    except (ImportError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"{args.check} failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
