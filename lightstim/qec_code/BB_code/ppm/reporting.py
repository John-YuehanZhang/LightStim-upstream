"""Readable catalogues and reports for the gross-code PPM demonstration.

These functions inspect the existing compiler and its completed plans.  They do
not search, choose alternative implementations, build circuits, or estimate LER.
Logical indices in human labels are one-based.  JSON retains the full signed
Pauli strings and ordered operations needed to audit the displayed reports.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import asdict
from html import escape
from io import BytesIO
from itertools import groupby
from pathlib import Path
from typing import Iterable

import stim

from .lowering import (
    BBPPMCompiler, BBPPMPlan, Measure, NativeMeasurement, PauliFrameUpdate,
    RecordParity, Shift,
)
from .shift import decompose_logical_shift


def _signed_letters(pauli: str | stim.PauliString) -> str:
    p = stim.PauliString(pauli)
    if p.sign not in (1, -1):
        raise ValueError("A reported observable must be a Hermitian Pauli.")
    return ("+" if p.sign == 1 else "-") + "".join(
        "IXYZ"[p[i]] for i in range(len(p)))


def format_logical_pauli(pauli: str | stim.PauliString) -> str:
    """Return an untruncated signed label, for example ``-X2 Z7 X12``."""
    text = _signed_letters(pauli)
    factors = [f"{p}{i}" for i, p in enumerate(text[1:], start=1) if p != "I"]
    return text[0] + (" ".join(factors) if factors else "I")


def format_record_parity(parity: RecordParity) -> str:
    """Format the XOR of result *bits*, not a product of +/-1 eigenvalues."""
    terms = list(parity.keys)
    if parity.constant:
        terms.append("1")
    return " XOR ".join(terms) if terms else "0"


def _shift_label(key) -> str:
    return f"x: {int(key[0])}, y: {int(key[1])}"


def _basic_pauli(compiler, p1, p7):
    p = stim.PauliString(compiler.num_logicals)
    p[compiler.pivot - 1] = p1
    p[compiler.partner - 1] = p7
    return p


def _write_json(path: Path, value) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(path.resolve())


def _write_csv(path: Path, rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                k: json.dumps(v, ensure_ascii=False, separators=(",", ":"))
                if isinstance(v, (list, tuple, dict)) else v
                for k, v in row.items()
            })
    return str(path.resolve())


def _native_record(compiler: BBPPMCompiler, native: NativeMeasurement, native_id: int | None = None):
    base = _basic_pauli(compiler, native.p1, native.p7)
    image = compiler.shift_maps[native.shift](base)
    image_string = _signed_letters(image)
    if image_string[1:] != native.pauli or (image.sign == -1) != native.sign:
        raise ValueError("A native measurement disagrees with its source shift.")
    inverse = compiler.inverse_shifts[native.shift]
    operations = []
    if inverse != compiler.identity_shift:
        operations.append({"type": "Shift", "x": inverse[0], "y": inverse[1]})
    operations.append({"type": "Measure", "p1": native.p1, "p7": native.p7})
    if native.shift != compiler.identity_shift:
        operations.append({"type": "Shift", "x": native.shift[0], "y": native.shift[1]})
    chronology = " -> ".join(
        _shift_label((op["x"], op["y"])) if op["type"] == "Shift"
        else f"Measure({format_logical_pauli(base)})" for op in operations)
    return {
        "native_id": native_id,
        "pauli": format_logical_pauli(native.pauli),
        "pauli_string": _signed_letters(native.pauli),
        "basic_measurement": format_logical_pauli(base),
        "basic_pauli_string": _signed_letters(base),
        "p1": native.p1, "p7": native.p7,
        "shift": _shift_label(native.shift),
        "shift_x": native.shift[0], "shift_y": native.shift[1],
        "inverse_shift": _shift_label(inverse),
        "inverse_x": inverse[0], "inverse_y": inverse[1],
        "conjugated_pauli": format_logical_pauli(image),
        "conjugated_pauli_string": image_string,
        "conjugation_sign": -1 if native.sign else 1,
        "outcome_xor": int(native.sign),
        "chronology": chronology,
        "operations": operations,
    }


def _native_records(compiler):
    return [_native_record(compiler, measurement, i)
            for i, (_, measurement) in enumerate(sorted(compiler.native_measurements.items()), start=1)]


def export_shift_and_native_catalogs(compiler: BBPPMCompiler, code, root) -> dict:
    """Save the logical shifts and their native measurement catalogue.

    ``root`` is the experiment artifact directory.  The returned ``rows`` has
    ``shifts`` and ``native_measurements`` lists; ``paths`` contains absolute
    CSV/JSON paths.  Physical displacements use (l,m), not the logical period.
    """
    root = Path(root).expanduser().resolve()
    shift_rows = []
    for (x, y), tableau in sorted(compiler.shift_maps.items()):
        recipes = decompose_logical_shift(code, x, y)
        physical = [{"family": recipe.family, "i": recipe.i, "j": recipe.j,
                     "delta": list(recipe.delta)} for recipe in recipes]
        shift_rows.append({
            "shift": _shift_label((x, y)), "x": x, "y": y,
            "inverse_shift": _shift_label(compiler.inverse_shifts[x, y]),
            "is_identity": (x, y) == compiler.identity_shift,
            "physical_steps": len(recipes),
            "physical_displacement": [sum(r.delta[0] for r in recipes) % code.l,
                                      sum(r.delta[1] for r in recipes) % code.m],
            "physical_recipes": physical,
            "logical_x_images": [format_logical_pauli(tableau.x_output(i))
                                 for i in range(compiler.num_logicals)],
            "logical_z_images": [format_logical_pauli(tableau.z_output(i))
                                 for i in range(compiler.num_logicals)],
            "logical_x_image_strings": [_signed_letters(tableau.x_output(i))
                                        for i in range(compiler.num_logicals)],
            "logical_z_image_strings": [_signed_letters(tableau.z_output(i))
                                        for i in range(compiler.num_logicals)],
        })
    native_rows = _native_records(compiler)
    metadata = {
        "schema": "lightstim.bb_ppm.catalog.v1",
        "code": "gross", "logical_indices": "one-based",
        "conjugation": "W P0 W_dagger",
        "chronology": "inverse shift, basic measurement, forward shift",
        "physical_period": [int(code.l), int(code.m)],
        "measurement_result_bits": "0 means +1; 1 means -1",
        "native_outcome": "corrected_bit = measured_bit XOR outcome_xor",
    }
    paths = {
        "shifts_json": _write_json(root / "shifts" / "shifts.json", {**metadata, "rows": shift_rows}),
        "shifts_csv": _write_csv(root / "shifts" / "shifts.csv", shift_rows),
        "native_json": _write_json(root / "native_measurements" / "native_measurements.json",
                                   {**metadata, "rows": native_rows}),
        "native_csv": _write_csv(root / "native_measurements" / "native_measurements.csv", native_rows),
    }
    return {"rows": {"shifts": shift_rows, "native_measurements": native_rows},
            "paths": paths,
            "counts": {"logical_shifts": len(shift_rows), "native_measurements": len(native_rows)}}


def export_rotation_catalog(compiler: BBPPMCompiler, root) -> dict:
    """Save every nontrivial data axis and every valid native Q1 A witness.

    The gross-code table contains 245 axes and 253 witnesses.  A row's chosen
    witness is copied from ``compiler.native_rotations``; reporting never
    changes the compiler's choice or assumes the other pivot bases are native.
    """
    root = Path(root).expanduser().resolve()
    by_axis = {}
    pivot = compiler.pivot - 1
    for native in _native_records(compiler):
        letters = native["pauli_string"][1:]
        q = letters[pivot]
        axis = letters[:pivot] + "I" + letters[pivot + 1:]
        if q == "I" or set(axis) == {"I"}:
            continue
        prepare, readout = {"X": ("Z", "Y"), "Y": ("X", "Z"), "Z": ("Y", "X")}[q]
        chosen = compiler.native_rotations.get(axis)
        is_chosen = chosen is not None and (
            chosen.pauli == letters and chosen.shift == (native["shift_x"], native["shift_y"])
            and chosen.p1 == native["p1"] and chosen.p7 == native["p7"])
        row = {**native, "axis": format_logical_pauli(axis), "axis_string": "+" + axis,
               "q": f"{q}{compiler.pivot}", "prepare": f"{prepare}{compiler.pivot}",
               "readout": f"{readout}{compiler.pivot}", "chosen": is_chosen,
               "forward_correction": "r*m*s = +1: apply/track A",
               "inverse_correction": "r*m*s = -1: apply/track A",
               "forward_bit_condition": "a XOR m_bit XOR s_bit XOR 1 = 1",
               "inverse_bit_condition": "a XOR m_bit XOR s_bit = 1"}
        by_axis.setdefault(axis, []).append(row)
    axes, implementations = [], []
    for axis_id, (axis, choices) in enumerate(sorted(by_axis.items()), start=1):
        selected = [r for r in choices if r["chosen"]]
        if len(selected) != 1:
            raise ValueError("Each reported axis must have exactly one compiler-selected witness.")
        chosen = selected[0]
        for choice in choices:
            choice["axis_id"] = axis_id
        implementations.extend(choices)
        axes.append({
            "axis_id": axis_id, "axis": format_logical_pauli(axis), "axis_string": "+" + axis,
            "implementation_count": len(choices), "chosen_joint": chosen["pauli"],
            "chosen_joint_string": chosen["pauli_string"], "chosen_shift": chosen["shift"],
            "chosen_basic": chosen["basic_measurement"], "q": chosen["q"],
            "prepare": chosen["prepare"], "readout": chosen["readout"],
            "forward_correction": chosen["forward_correction"],
            "inverse_correction": chosen["inverse_correction"],
            "chosen_native_id": chosen["native_id"], "implementations": choices,
        })
    metadata = {
        "schema": "lightstim.bb_ppm.rotation_catalog.v1",
        "unitary": "exp(+i*pi*A/4)", "pivot": compiler.pivot,
        "axis_count": len(axes), "implementation_count": len(implementations),
        "selection": "nonidentity pivot Q and nonidentity data A; deduplicate by A",
        "eigenvalue_results": "r,m,s are +/-1 for preparation, corrected joint measurement, readout",
        "bit_results": "a,m_bit,s_bit are 0 for +1 and 1 for -1; m_bit includes native outcome_xor",
    }
    directory = root / "rotation_axes"
    paths = {
        "axes_json": _write_json(directory / "rotation_axes.json", {**metadata, "rows": axes}),
        "axes_csv": _write_csv(directory / "rotation_axes.csv", axes),
        "implementations_json": _write_json(directory / "rotation_implementations.json",
                                             {**metadata, "rows": implementations}),
        "implementations_csv": _write_csv(directory / "rotation_implementations.csv", implementations),
    }
    return {"rows": {"axes": axes, "implementations": implementations}, "paths": paths,
            "counts": {"axes": len(axes), "implementations": len(implementations)}}


_GROUP = re.compile(r"(?:^|\.)(pre\d+|post\d+)\.(?:prepare|joint|readout)$")


def _record_group(key: str) -> str:
    match = _GROUP.search(key)
    return match.group(1) if match else "central"


def _operation_rows(compiler, plan, step):
    rows = []
    active = None
    for order, operation in enumerate(plan.operations):
        if isinstance(operation, Measure):
            active = _record_group(operation.key)
        elif isinstance(operation, PauliFrameUpdate):
            groups = {_record_group(k) for k in operation.parity.keys}
            if len(groups) == 1:
                active = next(iter(groups))
        if active is None:
            active = next((_record_group(op.key) for op in plan.operations[order:]
                           if isinstance(op, Measure)), "central")
        group = active
        direction = 1 if group.startswith("pre") else -1 if group.startswith("post") else None
        row = {"step": step, "order": order, "group": group, "direction": direction,
               "operation": type(operation).__name__, "condition": ""}
        if isinstance(operation, Shift):
            row.update({"x": operation.x, "y": operation.y,
                        "detail": _shift_label((operation.x, operation.y))})
        elif isinstance(operation, Measure):
            pauli = _basic_pauli(compiler, operation.p1, operation.p7)
            row.update({"p1": operation.p1, "p7": operation.p7, "key": operation.key,
                        "pauli_string": _signed_letters(pauli),
                        "detail": f"Measure {format_logical_pauli(pauli)} -> {operation.key}"})
        elif isinstance(operation, PauliFrameUpdate):
            condition = format_record_parity(operation.parity) + " = 1"
            row.update({"pauli_string": _signed_letters(operation.pauli),
                        "parity_keys": list(operation.parity.keys),
                        "parity_constant": operation.parity.constant, "condition": condition,
                        "detail": f"Apply/track {format_logical_pauli(operation.pauli)} if ({condition})"})
            active = None
        else:
            raise TypeError(f"Unknown PPM operation: {type(operation).__name__}")
        rows.append(row)
    return rows


def render_operation_table(rows: Iterable[dict]) -> str:
    """Render one time-ordered table with a right brace for each operation group.

    This is a presentation-only view of exported operation records. Rowspans
    align the braces with consecutive ``(step, group)`` ranges, including a
    one-row central measurement. Content is escaped and input rows are never
    modified. The renderer inherits the notebook's light or dark theme.
    """
    rows = list(rows)
    if not rows:
        return '<p class="bb-ppm-operations-empty">No operations in this plan.</p>'
    style = """<style>
.bb-ppm-operations { overflow-x: auto; color: inherit; }
.bb-ppm-operations table { width: 100%; min-width: 1000px; table-layout: fixed;
  border-collapse: collapse; color: inherit; font: inherit; font-size: 13px;
  line-height: 1.45; text-align: left; }
.bb-ppm-operations th, .bb-ppm-operations td { padding: 9px 10px;
  border: 0; border-bottom: 1px solid rgba(128,128,128,.18);
  text-align: left; vertical-align: middle; overflow-wrap: anywhere; }
.bb-ppm-operations thead th { background: rgba(128,128,128,.13); font-weight: 600; }
.bb-ppm-operations tbody tr:nth-child(even) td:not(.bb-ppm-phase) {
  background: rgba(128,128,128,.07); }
.bb-ppm-operations .bb-ppm-order { text-align: center; }
.bb-ppm-operations .bb-ppm-phase { position: relative; padding-left: 38px;
  border-bottom: 0; background: transparent; font-weight: 600; }
.bb-ppm-operations .bb-ppm-phase svg { position: absolute; left: 8px; top: 5px;
  width: 23px; height: calc(100% - 10px); overflow: visible; }
.bb-ppm-operations .bb-ppm-phase-label { display: block; white-space: nowrap; }
.bb-ppm-operations .bb-ppm-phase-head { background: transparent; border: 0; }
</style>"""
    pieces = [style, '<div class="bb-ppm-operations"><table aria-label="PPM operations in execution order">',
              '<colgroup><col style="width:60px"><col style="width:170px">'
              '<col style="width:35%"><col><col style="width:205px"></colgroup>',
              '<thead><tr><th class="bb-ppm-order" scope="col">order</th>'
              '<th scope="col">operation</th><th scope="col">detail</th>'
              '<th scope="col">condition</th><th class="bb-ppm-phase-head" '
              'scope="col" aria-label="Operation phase"></th></tr></thead><tbody>']
    for (step, group), iterator in groupby(rows, key=lambda row: (row["step"], row["group"])):
        members = list(iterator)
        for index, row in enumerate(members):
            detail = str(row.get("detail", ""))
            condition = str(row.get("condition") or "")
            if row["operation"] == "PauliFrameUpdate" and condition:
                # The condition already has its own column; keep the action concise.
                detail = detail.partition(" if (")[0]
            pieces.append('<tr>')
            for key, value in (("order", row["order"]), ("operation", row["operation"]),
                               ("detail", detail), ("condition", condition)):
                css = ' class="bb-ppm-order"' if key == "order" else ""
                pieces.append(f'<td{css}>{escape(str(value))}</td>')
            if index == 0:
                label = escape(f"PPM {step} — {group}")
                pieces.append(
                    f'<td class="bb-ppm-phase" rowspan="{len(members)}">'
                    '<svg viewBox="0 0 24 100" preserveAspectRatio="none" aria-hidden="true">'
                    '<path d="M 2 1 C 14 1 14 10 14 22 L 14 39 '
                    'C 14 46 18 49 22 50 C 18 51 14 54 14 61 '
                    'L 14 78 C 14 90 14 99 2 99" fill="none" stroke="currentColor" '
                    'stroke-width="1.4" vector-effect="non-scaling-stroke"/></svg>'
                    f'<span class="bb-ppm-phase-label">{label}</span></td>')
            pieces.append('</tr>')
    pieces.append('</tbody></table></div>')
    return "".join(pieces)


def render_operation_table_png(rows, *, row_height=40, scale=2) -> bytes:
    """Draw records on a fixed grid and return a self-contained PNG.

    Every operation occupies exactly ``row_height`` logical pixels, independent
    of its group or text. Braces are painted inside that already-sized canvas.
    A group's bounds are exactly its first row's top and last row's bottom;
    its label is centered on their midpoint. The PNG is emitted at ``scale``
    pixels per logical pixel so notebook previews can display crisp text.

    Display this with IPython's Image (native image/png), not an HTML img. GitHub
    can then scale the whole image without reflowing individual rows or braces.
    ``bb_ppm_geometry`` in the PNG text metadata records the logical coordinates.
    Rows are read only; this function never searches, samples or decodes.
    """
    from pathlib import Path

    import matplotlib
    from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

    rows = list(rows)
    if not isinstance(row_height, int) or row_height < 40:
        raise ValueError("row_height must be an integer of at least 40 pixels")
    if not isinstance(scale, int) or scale < 1:
        raise ValueError("scale must be a positive integer")

    font_dir = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    font = ImageFont.truetype(str(font_dir / "DejaVuSans.ttf"), 14 * scale)
    bold = ImageFont.truetype(str(font_dir / "DejaVuSans-Bold.ttf"), 14 * scale)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    line_height, padding = 17, 12
    max_lines = (row_height - 6) // line_height

    def wrap(text, width):
        # Preserve all words. Width expands globally if any cell would need
        # more lines than the fixed row can hold; row heights never expand.
        result, current = [], ""
        for word in str(text).split():
            candidate = f"{current} {word}" if current else word
            if current and probe.textlength(candidate, font=font) > width * scale:
                result.append(current)
                current = word
            else:
                current = candidate
        if current:
            result.append(current)
        return result or [""]

    values = []
    for row in rows:
        detail, condition = str(row.get("detail", "")), str(row.get("condition") or "")
        if row["operation"] == "PauliFrameUpdate" and condition:
            detail = detail.partition(" if (")[0]
        values.append([str(row["order"]), str(row["operation"]), detail, condition])

    # Column widths are shared by all rows. Long record identifiers can widen
    # a column, but can never change a single operation's height.
    widths = [56, 160, 350, 430]
    for column in range(4):
        texts = [value[column] for value in values]
        for text in texts:
            for word in text.split():
                widths[column] = max(widths[column], int(probe.textlength(word, font=font) / scale) + 2 * padding + 1)
        while any(len(wrap(text, widths[column] - 2 * padding)) > max_lines for text in texts):
            widths[column] += 20

    groups, offset = [], 0
    header_height = 40
    for (step, group), iterator in groupby(rows, key=lambda row: (row["step"], row["group"])):
        members = list(iterator)
        top = header_height + offset * row_height
        bottom = top + len(members) * row_height
        groups.append({"step": step, "group": group, "rows": len(members),
                       "top": top, "bottom": bottom, "height": bottom - top,
                       "label_center_y": (top + bottom) / 2})
        offset += len(members)
    labels = [f"PPM {group['step']} — {group['group']}" for group in groups]
    phase_width = max([180] + [int(probe.textlength(label, font=bold) / scale) + 48 for label in labels])
    table_width = sum(widths)
    width = table_width + phase_width
    # A one-pixel bottom margin includes the final rule; it is not part of a row.
    height = header_height + len(rows) * row_height + 1
    image = Image.new("RGB", (width * scale, height * scale), "white")
    draw = ImageDraw.Draw(image)
    ink, muted, rule = "#18212f", "#34445a", "#d8dee8"

    def rectangle(bounds, fill):
        draw.rectangle(tuple(round(value * scale) for value in bounds), fill=fill)

    def centered_text(x, y, text, *, center=False, strong=False):
        chosen = bold if strong else font
        left, top, right, bottom = draw.textbbox((0, 0), text, font=chosen)
        x_px = x * scale - ((right - left) / 2 if center else 0) - left
        y_px = y * scale - (bottom - top) / 2 - top
        draw.text((round(x_px), round(y_px)), text, font=chosen, fill=ink)

    rectangle((0, 0, table_width, header_height), "#e9edf4")
    x = 0
    for i, label in enumerate(("order", "operation", "detail", "condition")):
        centered_text(x + (widths[i] / 2 if i == 0 else padding), header_height / 2,
                      label, center=i == 0, strong=True)
        x += widths[i]
    centered_text(table_width + 44, header_height / 2, "group", strong=True)

    for index, value in enumerate(values):
        top = header_height + index * row_height
        rectangle((0, top, table_width, top + row_height), "#f6f8fb" if index % 2 else "white")
        x = 0
        for column, text in enumerate(value):
            lines = wrap(text, widths[column] - 2 * padding)
            middle = top + row_height / 2
            for line_index, line in enumerate(lines):
                y = middle + (line_index - (len(lines) - 1) / 2) * line_height
                centered_text(x + (widths[column] / 2 if column == 0 else padding), y,
                              line, center=column == 0)
            x += widths[column]
        draw.line([(0, (top + row_height) * scale), (table_width * scale, (top + row_height) * scale)],
                  fill=rule, width=scale)

    # A right-facing brace consists only of points in the group's bounds. All
    # groups, including a one-row central measurement, use the same x geometry.
    for group, label in zip(groups, labels):
        x, top, span = table_width + 10, group["top"], group["height"]
        points = []
        segments = [
            ((0, 0), (12, 0), (12, .10), (12, .22)),
            ((12, .22), (12, .39), (12, .46), (22, .50)),
            ((22, .50), (12, .54), (12, .61), (12, .78)),
            ((12, .78), (12, .90), (12, 1), (0, 1)),
        ]
        for segment in segments:
            for i in range(33):
                t, u = i / 32, 1 - i / 32
                weights = (u ** 3, 3 * u * u * t, 3 * u * t * t, t ** 3)
                px = x + sum(weight * point[0] for weight, point in zip(weights, segment))
                py = top + span * sum(weight * point[1] for weight, point in zip(weights, segment))
                points.append((round(px * scale), round(py * scale)))
        draw.line(points, fill=muted, width=max(1, round(1.4 * scale)), joint="curve")
        centered_text(table_width + 44, group["label_center_y"], label, strong=True)

    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("bb_ppm_geometry", json.dumps({
        "row_height": row_height, "header_height": header_height, "scale": scale,
        "width": width, "height": height, "table_width": table_width,
        "row_count": len(rows), "groups": groups,
    }, ensure_ascii=False))
    metadata.add_text("Description", "PPM operation table; every operation has the same fixed height.")
    output = BytesIO()
    image.save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


def operation_table_plain_text(rows):
    """A complete, copyable fallback for the notebook's image/png output."""
    lines = ["order | operation | detail | condition | group"]
    for row in rows:
        condition = str(row.get("condition") or "")
        detail = str(row.get("detail", ""))
        if row["operation"] == "PauliFrameUpdate" and condition:
            detail = detail.partition(" if (")[0]
        lines.append(f"{row['order']} | {row['operation']} | {detail} | {condition} | "
                     f"PPM {row['step']} — {row['group']}")
    return "\n".join(lines)

def export_plan_report(compiler: BBPPMCompiler, plans: Iterable[BBPPMPlan], root) -> dict:
    """Export selected signed paths and existing operations, without searching.

    ``summary``, ``path`` and ``operations`` are ready for notebook tables.  The
    path is exactly ``plan.path`` recorded by the compiler, not a replay or BFS
    trace.  All groups and operation order refer to the supplied plans.
    """
    root = Path(root).expanduser().resolve()
    plans = tuple(plans)
    if not plans:
        raise ValueError("At least one compiled plan is required.")
    summaries, paths, operations, payloads, searches = [], [], [], [], []
    for step, plan in enumerate(plans):
        search_rows = []
        for record in plan.search_trace:
            row = {"step": step, "target": format_logical_pauli(plan.target),
                   "search_status": plan.search_status, **asdict(record)}
            if record.meeting_pauli is not None:
                row["meeting_pauli"] = format_logical_pauli(record.meeting_pauli)
            search_rows.append(row)
        searches.extend(search_rows)
        if len(plan.path) != len(plan.rotations):
            raise ValueError("The plan must include its compiler-recorded signed path.")
        for rotation, edge in enumerate(plan.path):
            native = _native_record(compiler, compiler.native_rotations[_signed_letters(edge.axis)[1:]])
            paths.append({
                "step": step, "rotation": rotation, "before": format_logical_pauli(edge.before),
                "before_string": _signed_letters(edge.before), "axis": format_logical_pauli(edge.axis),
                "axis_string": _signed_letters(edge.axis), "direction": edge.direction,
                "after": format_logical_pauli(edge.after), "after_string": _signed_letters(edge.after),
                "joint_measurement": native["pauli"], "shift": native["shift"],
                "basic_measurement": native["basic_measurement"],
            })
        op_rows = _operation_rows(compiler, plan, step)
        operations.extend(op_rows)
        counts = Counter(row["operation"] for row in op_rows)
        base = _native_record(compiler, compiler.native_measurements[plan.base_measurement]) \
            if plan.base_measurement else None
        central_method = None
        if base is not None:
            central_method = "prepared_pivot" if (
                plan.consumes_pivot and plan.base_measurement[compiler.pivot - 1] != "I"
            ) else "direct"
        data_base = None
        if plan.consumes_pivot and base is not None:
            data = stim.PauliString(plan.base_measurement)
            data[compiler.pivot - 1] = "I"
            data_base = format_logical_pauli(data)
        output = format_record_parity(plan.output)
        summaries.append({
            "step": step, "target": format_logical_pauli(plan.target),
            "target_string": _signed_letters(plan.target), "rotations": len(plan.rotations),
            "signed_endpoint": format_logical_pauli(plan.path[-1].after if plan.path else plan.target),
            "data_base": data_base,
            "central_measurement": base["pauli"] if base else None,
            "central_method": central_method,
            "native_joint": base["pauli"] if central_method == "prepared_pivot" else None,
            "basic_measurement": base["basic_measurement"] if base else None,
            "shift": base["shift"] if base else None, "consumes_pivot": plan.consumes_pivot,
            "operation_count": len(op_rows), "measurements": counts["Measure"],
            "shifts": counts["Shift"], "frame_updates": counts["PauliFrameUpdate"],
            "output": output, "output_eigenvalue": f"(-1)^({output})",
        })
        payloads.append({
            "step": step, "target": _signed_letters(plan.target),
            "search_status": plan.search_status,
            "search_trace": [asdict(record) for record in plan.search_trace],
            "rotations": [_signed_letters(a) for a in plan.rotations],
            "path": [{"before": _signed_letters(e.before), "axis": _signed_letters(e.axis),
                      "after": _signed_letters(e.after), "direction": e.direction} for e in plan.path],
            "base_measurement": base, "central_method": central_method,
            "consumes_pivot": plan.consumes_pivot,
            "operations": op_rows,
            "output": {"keys": list(plan.output.keys), "constant": plan.output.constant},
        })
    directory = root / "searches"
    saved = {
        "plans_json": _write_json(directory / "plans.json", {
            "schema": "lightstim.bb_ppm.plan_report.v1", "operation_order": "time order",
            "result_bits": "0 means +1; 1 means -1; XOR expressions act on bits",
            "correction": "apply/track the listed Pauli when its bit parity is 1",
            "path_convention": "after = exp(+i*direction*pi*axis/4) before exp(-i*direction*pi*axis/4)",
            "search_convention": {
                "round": "0 is initialization; later rows follow one full frontier expansion",
                "expanded_side": "the smaller frontier is expanded; ties select target",
                "new": "distinct nodes first discovered on that side in this round; initial seeds count as new",
                "total": "all distinct nodes discovered on that side, including its initial seeds",
                "frontier": "nodes awaiting expansion on that side after this round",
                "cached": "the saved original search trace is reused, not a newly executed search",
                "native_initialization": "may include a native frontier cached from earlier targets",
            },
            "plans": payloads,
        }),
        "summary_csv": _write_csv(directory / "plan_summary.csv", summaries),
        "search_csv": _write_csv(directory / "search_progress.csv", searches),
        "path_csv": _write_csv(directory / "selected_paths.csv", paths),
        "operations_csv": _write_csv(directory / "operations.csv", operations),
    }
    return {"rows": {"summary": summaries, "search": searches,
                     "path": paths, "operations": operations}, "paths": saved}


__all__ = ["format_logical_pauli", "format_record_parity", "export_shift_and_native_catalogs",
           "export_rotation_catalog", "export_plan_report", "render_operation_table",
           "render_operation_table_png", "operation_table_plain_text"]
