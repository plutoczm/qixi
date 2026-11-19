"""Validate the local project documents and their serial stage states."""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
FILES = {"spec.md", "plan_baseline.md", "plan_runtime.md", "progress.md", "project_notes.md"}
STAGES = [f"S{i}" for i in range(1, 13)]
ROW = re.compile(r"^\| (S\d+) \|", re.M)
STEP_ROW = re.compile(r"^\| (S\d+\.\d+) \|")


def check_plan(content: str, name: str, errors: list[str]) -> None:
    rows = [line.strip("|").split("|") for line in content.splitlines() if ROW.match(line)]
    if [row[0].strip() for row in rows] != STAGES:
        errors.append(f"{name}: expected S1–S12 in sequence")
        return
    for index, row in enumerate(rows):
        if len(row) != 5 or not all(cell.strip() for cell in row):
            errors.append(f"{name}: incomplete step for {STAGES[index]}")
            continue
        dependency_text = row[3].strip()
        dependencies = [] if dependency_text == "—" else re.split(r"[,，、\s]+", dependency_text)
        if len(dependencies) != len(set(dependencies)) or any(dep not in STAGES[:index] for dep in dependencies):
            errors.append(f"{name}: invalid dependency for {STAGES[index]}")


def check_runtime(content: str, criteria: set[str], errors: list[str]) -> list[str]:
    headings = re.compile(r"^## (S\d+) \S")
    current_stage = ""
    stages: list[str] = []
    steps: list[str] = []
    step_index = 0
    covered: set[str] = set()
    for line in content.splitlines():
        heading = headings.match(line)
        if heading:
            current_stage = heading[1]
            stages.append(current_stage)
            step_index = 0
        if not STEP_ROW.match(line):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        step = cells[0]
        step_index += 1
        predecessor = steps[-1] if steps else "—"
        steps.append(step)
        if len(cells) != 5 or not all(cells):
            errors.append(f"plan_runtime.md: incomplete step {step}")
            continue
        if step != f"{current_stage}.{step_index}" or cells[1] != predecessor:
            errors.append(f"plan_runtime.md: unexpected step or predecessor for {step}")
        references = cells[4].split()
        if not set(references).issubset(criteria):
            errors.append(f"plan_runtime.md: unknown criterion for {step}")
        covered.update(references)
    if stages != STAGES or {step.split(".")[0] for step in steps} != set(STAGES):
        errors.append("plan_runtime.md: each stage S1–S12 must have steps in order")
    if not {criterion for criterion in criteria if criterion.startswith("REF")}.issubset(covered):
        errors.append("plan_runtime.md: current acceptance criteria must all be covered")
    return steps


def check_progress(content: str, steps: list[str], errors: list[str]) -> None:
    fields: dict[str, str] = {}
    for field in ("current_stage", "current_step", "current_status", "last_accepted_step", "historical_through"):
        values = re.findall(rf"^{field}: (\S+)$", content, re.M)
        if len(values) != 1:
            errors.append(f"progress.md: expected one {field}")
            return
        fields[field] = values[0]

    checkpoints = ["null", *steps]
    accepted = fields["last_accepted_step"]
    historical = fields["historical_through"]
    if accepted not in checkpoints or historical not in checkpoints:
        errors.append("progress.md: step checkpoint is absent from the runtime plan")
        return
    accepted_count = checkpoints.index(accepted)
    historical_count = checkpoints.index(historical)
    if historical_count > accepted_count:
        errors.append("progress.md: historical checkpoint exceeds accepted progress")

    records = []
    for line in content.splitlines():
        if STEP_ROW.match(line):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) != 2 or not cells[1]:
                errors.append("progress.md: accepted step needs verification evidence")
            records.append(cells[0])
    if records != steps[historical_count:accepted_count]:
        errors.append("progress.md: accepted steps must have consecutive verification records")

    expected = steps[accepted_count] if accepted_count < len(steps) else "null"
    stage = expected.split(".")[0]
    status = fields["current_status"]
    allowed = {"done"} if expected == "null" else {"ready", "doing", "blocked"}
    if fields["current_step"] != expected or fields["current_stage"] != stage or status not in allowed:
        errors.append("progress.md: current position must be the first step after the last accepted one")

    rows = [[cell.strip() for cell in line.strip("|").split("|")]
            for line in content.splitlines() if ROW.match(line)]
    if [row[0] for row in rows] != STAGES:
        errors.append("progress.md: expected stage rows S1–S12 in sequence")
        return
    active_index = STAGES.index(stage) if stage in STAGES else len(STAGES)
    for index, row in enumerate(rows):
        expected_status = "done" if index < active_index else status if index == active_index else "waiting"
        if len(row) != 3 or row[1] != expected_status or not row[2]:
            errors.append(f"progress.md: stage status does not match the current step for {row[0]}")


def check() -> tuple[list[str], dict[str, int]]:
    errors: list[str] = []
    counts = {"documents": 0, "links": 0, "phases": 0, "steps": 0, "acceptance_cases": 0}
    if not DOCS.is_dir() or {path.name for path in DOCS.iterdir()} != FILES:
        errors.append(f"docs must contain exactly: {', '.join(sorted(FILES))}")
    docs = {}
    for path in [ROOT / "AGENTS.md", ROOT / "README.md", *(DOCS / name for name in sorted(FILES))]:
        if not path.is_file():
            errors.append(f"Missing document: {path.relative_to(ROOT)}")
            continue
        content = path.read_text(encoding="utf-8")
        docs[path.name] = content
        for number, line in enumerate(content.splitlines(), 1):
            if line != line.rstrip():
                errors.append(f"Trailing whitespace: {path.relative_to(ROOT)}:{number}")
        for target in re.findall(r"\[[^\]\n]+\]\(([^)\n]+)\)", re.sub(r"\x60{3}.*?\x60{3}", "", content, flags=re.S)):
            parsed = urlsplit(target.strip("<>"))
            if parsed.scheme or parsed.netloc:
                continue
            linked = (path.parent / unquote(parsed.path)).resolve()
            if not linked.is_relative_to(ROOT) or not linked.exists():
                errors.append(f"Invalid local link: {path.relative_to(ROOT)} -> {target}")
            counts["links"] += 1

    counts["documents"] = len(FILES.intersection(docs))
    check_plan(docs.get("plan_baseline.md", ""), "plan_baseline.md", errors)
    spec = docs.get("spec.md", "")
    cases = re.findall(r"^\| (AC\d{2}) \|", spec, re.M)
    refs = re.findall(r"^\| (REF\d{2}) \|", spec, re.M)
    if cases != [f"AC{i:02}" for i in range(1, 35)] or refs != [f"REF{i:02}" for i in range(1, 7)]:
        errors.append("spec.md: expected AC01–AC34 and REF01–REF06 in sequence")
    counts["acceptance_cases"] = len(cases) + len(refs)
    runtime = docs.get("plan_runtime.md", "")
    steps = check_runtime(runtime, set(cases + refs), errors)
    counts["phases"] = len(re.findall(r"^## S\d+ ", runtime, re.M))
    counts["steps"] = len(steps)
    check_progress(docs.get("progress.md", ""), steps, errors)
    return errors, counts


def main() -> int:
    errors, counts = check()
    print(json.dumps({"status": "FAIL" if errors else "PASS", "counts": counts, "errors": errors},
                     ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
