"""Collect portable evidence from a completed check.py all run."""

import csv
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from check import FORMAL_SUITES

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
RESULTS = ROOT / "results"


def load_json(path):
    return json.loads((BUILD / path).read_text(encoding="utf-8"))


def require_tests(path, expected):
    cases = ET.parse(BUILD / path).findall(".//testcase")
    names = [case.get("name") for case in cases]
    if sorted(names) != sorted(expected) or any(
        child.tag in {"error", "failure", "skipped"} for case in cases for child in case
    ):
        raise RuntimeError(f"Unexpected test results in {path}: {names}")


def main():
    require_tests("smoke/results.xml", ("signed_products",))
    hardware, cycles = {}, []
    for lanes, label in ((1, "one"), (4, "four")):
        require_tests(f"sim/lanes-{lanes}/results.xml",
                      ("directed_arithmetic", "randomized_protocol",
                       "randomized_interruptions", "all_vector_lengths"))
        require_tests(f"perf/lanes-{lanes}/results.xml", ("benchmark_cycles",))
        stats = load_json(f"synthesis/generic-{lanes}.json")
        timing = load_json(f"timing/ice40-{lanes}.timing.json")
        benchmark = load_json(f"perf/lanes-{lanes}/edgemac-perf.json")
        if benchmark["lanes"] != lanes or len(benchmark["rows"]) != 20:
            raise RuntimeError(f"Unexpected cycle measurements for {lanes} lanes")
        cycles.extend(dict(lanes=lanes, seed=f"{benchmark['seed']:#x}", **row)
                      for row in benchmark["rows"])
        formal = {}
        for suite, prefix, modes in FORMAL_SUITES:
            suite = "protocol" if suite == "edgemac" else suite
            formal[suite] = {}
            for mode in modes:
                case = BUILD / "formal" / f"{prefix}{label}_{mode}"
                status = (case / "status").read_text(encoding="utf-8").split()[0]
                if status != "PASS":
                    raise RuntimeError(f"{case.name} reported {status}")
                formal[suite][mode] = status

        log = (BUILD / f"synthesis/generic-{lanes}.log").read_text(encoding="utf-8")
        depth = int(re.search(r"Longest topological path.*\(length=(\d+)\)", log)[1])
        design = stats["design"]
        counts = design["num_cells_by_type"]
        hardware[lanes] = {
            "yosys": stats["creator"],
            "generic_cells": design["num_cells"],
            "generic_ff_cells": sum(number for cell, number in counts.items() if "DFF" in cell),
            "generic_cells_by_type": counts,
            "generic_logic_depth": depth,
            "fpga_clocks": timing["fmax"],
            "fpga_utilization": timing["utilization"],
            "formal": formal,
        }

    RESULTS.mkdir(exist_ok=True)
    output = json.dumps(hardware, indent=2) + "\n"
    (RESULTS / "hardware.json").write_text(output, encoding="utf-8", newline="\n")
    with (RESULTS / "cycles.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(cycles[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(cycles)


if __name__ == "__main__":
    main()
