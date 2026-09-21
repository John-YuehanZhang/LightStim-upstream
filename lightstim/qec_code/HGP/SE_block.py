"""Product-coloration syndrome extraction for hypergraph-product codes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import stim

from lightstim.qec_code.generic_css import color_bipartite_edges

from .code_patch import HGPCode


Basis = Literal["X", "Z"]
Direction = Literal["horizontal", "vertical"]
SeedEdge = tuple[int, int]
QubitPair = tuple[int, int]


@dataclass(frozen=True)
class HGPProductColorLayer:
    """One product-color layer and its stable semantic label.

    ``patch_name`` names the HGP patch a per-patch layer belongs to.  When
    several HGP patches are scheduled together, the layers of the same
    ``(basis, direction, color)`` are merged into one layer whose
    ``cnot_pairs`` is the union of the per-patch pairs; the merged layer has
    ``patch_name=None``, empty ``seed_edges`` and keeps the per-patch layers
    in ``parts``.
    """

    basis: Basis
    direction: Direction
    color: int
    seed: Literal["H1", "H2"]
    seed_edges: tuple[SeedEdge, ...]
    cnot_pairs: tuple[QubitPair, ...]
    patch_name: str | None = None
    parts: tuple["HGPProductColorLayer", ...] = ()

    @property
    def label(self) -> str:
        return f"HGP:{self.basis}:{self.direction}:color={self.color}"


class HGPProductColorationExtractionBlock:
    """Algorithm-2 product coloration, expressed with CNOT gates.

    The first seed ``H1`` is horizontal and the second seed ``H2`` is
    vertical, matching :class:`HGPCode`.  Each seed Tanner graph is colored
    once and its colors are lifted across every independent product row or
    column.  Consequently, labels such as ``X/horizontal/color=2`` are stable
    properties of the seed code instead of incidental colors of the flattened
    quantum Tanner graph.

    X and Z checks are separate measurement blocks.  This is the CNOT
    equivalent of Algorithm 2 in arXiv:2308.08648: X ancillas are prepared and
    measured in the X basis, while Z ancillas use the Z basis.

    The block is system-wide, like the other extraction blocks: every active
    HGP patch of the system is scheduled, each with the coloring of its own
    seeds, and the per-patch layers are merged into the layers of one round
    (the patches act on disjoint qubits).  Two merge policies:

    * ``align="direction"`` (default): per-patch layers of equal
      ``(basis, direction, color)`` are merged, so every merged layer keeps
      one semantic label.  Depth per basis is
      ``max_p(vertical colors) + max_p(horizontal colors)``; this is the
      depth of the deepest patch when all patches have the same color counts
      (e.g. a code and an ancilla punctured from it).
    * ``align="index"``: the ``i``-th layer of every patch is merged into
      merged layer ``i`` regardless of direction, giving the shortest round,
      ``max_p(vertical + horizontal colors)`` per basis.  Merged layers then
      carry the label of their first part; use ``parts`` for the per-patch
      semantics.  Legal because the CNOTs inside one basis block commute
      and the tracker analyses each block as a whole.

    Every active stabilizer of the system must belong to one of the
    scheduled HGP patches; a system that also holds a non-HGP patch is
    rejected (use the generic CSS extraction block there).

    With one active HGP patch the schedule, labels and circuit are the
    single-patch product coloration unchanged; ``patch_name``, ``patch``,
    ``local_to_global``, ``horizontal_seed_colors`` and
    ``vertical_seed_colors`` then refer to that patch and ``_g`` maps its
    local indices.  With several patches those five are ``None`` and ``_g``
    raises; use ``patch_names``, ``patches``, ``local_to_global_maps``,
    ``seed_colors`` and ``patch_layers`` instead.  Passing ``patch_name``
    restricts the block to that patch, which must then be the only active
    HGP patch.
    """

    def __init__(self, system: Any, patch_name: str | None = None,
                 align: Literal["direction", "index"] = "direction"):
        if align not in ("direction", "index"):
            raise ValueError(f"align must be 'direction' or 'index', got {align!r}.")
        self.system = system
        self.align = align
        self.requested_patch_name = patch_name
        self.patch_names, self.patches = self._find_patches(patch_name)
        self.local_to_global_maps = {
            name: self.system.local_to_global_map[name] for name in self.patch_names
        }

        # Single-patch view (backwards compatible); None when several patches.
        single = len(self.patch_names) == 1
        self.patch_name = self.patch_names[0] if single else None
        self.patch = self.patches[self.patch_name] if single else None
        self.local_to_global = self.local_to_global_maps[self.patch_name] if single else None

        self.seed_colors = {
            name: (self._color_seed(patch.h1), self._color_seed(patch.h2))
            for name, patch in self.patches.items()
        }
        if single:
            self.horizontal_seed_colors, self.vertical_seed_colors = self.seed_colors[self.patch_name]
        else:
            self.horizontal_seed_colors = None
            self.vertical_seed_colors = None

        # Per patch: preserve the established H2-then-H1 gate order while
        # giving each layer the direction label of the product-block display.
        self.patch_layers: dict[str, tuple[tuple[HGPProductColorLayer, ...], tuple[HGPProductColorLayer, ...]]] = {}
        for name in self.patch_names:
            x_layers = tuple(
                self._lift_layers(name, "X", "vertical")
                + self._lift_layers(name, "X", "horizontal")
            )
            z_layers = tuple(
                self._lift_layers(name, "Z", "vertical")
                + self._lift_layers(name, "Z", "horizontal")
            )
            self.patch_layers[name] = (x_layers, z_layers)

        self.x_layers = self._merge_layers("X")
        self.z_layers = self._merge_layers("Z")
        self.layers = self.x_layers + self.z_layers
        self.depth_x = len(self.x_layers)
        self.depth_z = len(self.z_layers)
        self.cnot_depth = len(self.layers)

        self._validate_active_stabilizers()
        self.measurement_blocks = self._build_measurement_blocks()
        self.circuit = stim.Circuit()
        for block in self.measurement_blocks:
            self.circuit += block

    def _find_patches(self, requested_name: str | None) -> tuple[tuple[str, ...], dict[str, HGPCode]]:
        active_patch_names = {
            stabilizer.get("patch_name")
            for stabilizer in self.system.active_stabilizers
        }
        candidates = [
            (name, patch)
            for name, (patch, _) in self.system.patches.items()
            if isinstance(patch, HGPCode) and name in active_patch_names
        ]
        if requested_name is not None:
            all_names = [name for name, _ in candidates]
            candidates = [item for item in candidates if item[0] == requested_name]
            if len(candidates) != 1:
                raise ValueError(
                    "HGP product coloration found no active HGP patch named "
                    f"{requested_name!r}; active HGP patches: {all_names}."
                )
            if len(all_names) > 1:
                raise ValueError(
                    f"patch_name={requested_name!r} restricts the block to one patch, but "
                    f"the active HGP patches are {all_names}; omit patch_name to "
                    "schedule them all in one round."
                )
        if not candidates:
            raise ValueError(
                "HGP product coloration requires at least one active HGP patch; found none."
            )
        return tuple(name for name, _ in candidates), dict(candidates)

    def _merge_layers(self, basis: Basis) -> tuple[HGPProductColorLayer, ...]:
        """Merge the per-patch layers of ``basis`` into one round (class docstring).

        With one patch the per-patch layers are returned unchanged.  Per-patch
        layers without CNOTs (fully masked stabilizers) are left out of
        ``parts``.
        """
        index = 0 if basis == "X" else 1
        if len(self.patch_names) == 1:
            return self.patch_layers[self.patch_names[0]][index]

        # groups: list of (direction label or None, {patch: [layers]})
        if self.align == "direction":
            groups = [
                (direction, {
                    name: [layer for layer in self.patch_layers[name][index] if layer.direction == direction]
                    for name in self.patch_names
                })
                for direction in ("vertical", "horizontal")
            ]
        else:
            groups = [(None, {name: list(self.patch_layers[name][index]) for name in self.patch_names})]

        merged: list[HGPProductColorLayer] = []
        for direction, per_patch in groups:
            depth = max(len(layers) for layers in per_patch.values())
            for position in range(depth):
                parts = tuple(
                    per_patch[name][position]
                    for name in self.patch_names
                    if position < len(per_patch[name]) and per_patch[name][position].cnot_pairs
                )
                pairs = sorted(pair for part in parts for pair in part.cnot_pairs)
                flat = [qubit for pair in pairs for qubit in pair]
                if len(flat) != len(set(flat)):
                    raise RuntimeError(
                        f"Merged product layer {basis}/{direction or 'index'}/{position} is not "
                        "conflict-free across patches."
                    )
                first = parts[0] if parts else per_patch[self.patch_names[0]][0]
                merged.append(
                    HGPProductColorLayer(
                        basis=basis,
                        direction=direction if direction is not None else first.direction,
                        color=position if direction is not None else first.color,
                        seed=first.seed,
                        seed_edges=(),
                        cnot_pairs=tuple(pairs),
                        patch_name=None,
                        parts=parts,
                    )
                )
        return tuple(merged)

    @staticmethod
    def _color_seed(seed: Any) -> tuple[tuple[SeedEdge, ...], ...]:
        edges = [
            (check, bit)
            for check, row in enumerate(seed.row_supports)
            for bit in row
        ]
        return tuple(tuple(color) for color in color_bipartite_edges(edges))

    def _g(self, local_index: int) -> int:
        if self.local_to_global is None:
            raise ValueError("_g needs the single-patch view; use local_to_global_maps[name].")
        return self.local_to_global[local_index]

    def _lift_layers(
        self,
        patch_name: str,
        basis: Basis,
        direction: Direction,
    ) -> list[HGPProductColorLayer]:
        patch = self.patches[patch_name]
        local_to_global = self.local_to_global_maps[patch_name]
        active_syndromes = (
            set(self.system.active_syndrome_indices_x)
            if basis == "X"
            else set(self.system.active_syndrome_indices_z)
        )
        seed = "H1" if direction == "horizontal" else "H2"
        horizontal_colors, vertical_colors = self.seed_colors[patch_name]
        colors = horizontal_colors if direction == "horizontal" else vertical_colors
        result = []

        for color_index, seed_edges in enumerate(colors):
            pairs: list[QubitPair] = []
            if basis == "X" and direction == "vertical":
                # H2[check_2, bit_2]: X(bit_1, check_2) -> VV(bit_1, bit_2)
                for check_2, bit_2 in seed_edges:
                    for bit_1 in range(patch.h1.num_bits):
                        syndrome = local_to_global[patch.x_check_qubits[(bit_1, check_2)]]
                        if syndrome in active_syndromes:
                            data = local_to_global[patch.vv_qubits[(bit_1, bit_2)]]
                            pairs.append((syndrome, data))
            elif basis == "X" and direction == "horizontal":
                # H1[check_1, bit_1]: X(bit_1, check_2) -> CC(check_1, check_2)
                for check_1, bit_1 in seed_edges:
                    for check_2 in range(patch.h2.num_checks):
                        syndrome = local_to_global[patch.x_check_qubits[(bit_1, check_2)]]
                        if syndrome in active_syndromes:
                            data = local_to_global[patch.cc_qubits[(check_1, check_2)]]
                            pairs.append((syndrome, data))
            elif basis == "Z" and direction == "vertical":
                # H2[check_2, bit_2]: CC(check_1, check_2) -> Z(check_1, bit_2)
                for check_2, bit_2 in seed_edges:
                    for check_1 in range(patch.h1.num_checks):
                        syndrome = local_to_global[patch.z_check_qubits[(check_1, bit_2)]]
                        if syndrome in active_syndromes:
                            data = local_to_global[patch.cc_qubits[(check_1, check_2)]]
                            pairs.append((data, syndrome))
            else:
                # H1[check_1, bit_1]: VV(bit_1, bit_2) -> Z(check_1, bit_2)
                for check_1, bit_1 in seed_edges:
                    for bit_2 in range(patch.h2.num_bits):
                        syndrome = local_to_global[patch.z_check_qubits[(check_1, bit_2)]]
                        if syndrome in active_syndromes:
                            data = local_to_global[patch.vv_qubits[(bit_1, bit_2)]]
                            pairs.append((data, syndrome))

            pairs.sort()
            flat_qubits = [qubit for pair in pairs for qubit in pair]
            if len(flat_qubits) != len(set(flat_qubits)):
                raise RuntimeError(
                    f"Product color {basis}/{direction}/{color_index} is not conflict-free."
                )
            result.append(
                HGPProductColorLayer(
                    basis=basis,
                    direction=direction,
                    color=color_index,
                    seed=seed,
                    seed_edges=tuple(seed_edges),
                    cnot_pairs=tuple(pairs),
                    patch_name=patch_name,
                )
            )
        return result

    def _validate_active_stabilizers(self) -> None:
        # Every active syndrome qubit of the system must be a check qubit of
        # one of the scheduled HGP patches: the round must measure every
        # active stabilizer, and the tracker does not check that by itself.
        scheduled: set[int] = set()
        for name, patch in self.patches.items():
            local_to_global = self.local_to_global_maps[name]
            scheduled |= {local_to_global[index] for index in patch.x_check_qubits.values()}
            scheduled |= {local_to_global[index] for index in patch.z_check_qubits.values()}
        active_all = set(self.system.active_syndrome_indices)
        unexpected = active_all - scheduled
        if unexpected:
            owners = sorted({
                str(self.system.index_to_owner_map.get(qubit, "?")) for qubit in unexpected
            })
            raise ValueError(
                "HGP product coloration cannot schedule active syndrome qubits "
                f"outside the active HGP patches {list(self.patch_names)}: "
                f"{sorted(unexpected)} (owned by {owners}); use the generic CSS "
                "extraction block for a system that mixes code families."
            )

        for basis, layers, stabilizers in (
            ("X", self.x_layers, self.system.active_stabilizers_x),
            ("Z", self.z_layers, self.system.active_stabilizers_z),
        ):
            expected: dict[int, set[int]] = {}
            for layer in layers:
                for control, target in layer.cnot_pairs:
                    syndrome, data = (
                        (control, target) if basis == "X" else (target, control)
                    )
                    expected.setdefault(syndrome, set()).add(data)

            actual: dict[int, set[int]] = {}
            for stabilizer in stabilizers:
                syndrome = stabilizer.get("syn_idx")
                if syndrome in actual:
                    raise ValueError(
                        f"Multiple active {basis} stabilizers use syndrome qubit {syndrome}."
                    )
                actual[syndrome] = set(stabilizer.get("data_indices", ()))

            if expected != actual:
                raise ValueError(
                    f"Active {basis} stabilizers do not match the unmodified HGP "
                    "product structure; use the generic CSS extraction block for "
                    "a deformed or coupled patch."
                )

    @staticmethod
    def _append_layer(circuit: stim.Circuit, layer: HGPProductColorLayer) -> None:
        targets = [qubit for pair in layer.cnot_pairs for qubit in pair]
        if targets:
            circuit.append("CNOT", targets, tag=layer.label)
        circuit.append("TICK", tag=layer.label)

    def _build_measurement_blocks(self) -> tuple[stim.Circuit, stim.Circuit]:
        x_syndromes = sorted(self.system.active_syndrome_indices_x)
        z_syndromes = sorted(self.system.active_syndrome_indices_z)

        x_block = stim.Circuit()
        x_block.append("RX", x_syndromes)
        x_block.append("TICK", tag="SE_start")
        for layer in self.x_layers:
            self._append_layer(x_block, layer)
        x_block.append("MX", x_syndromes)

        z_block = stim.Circuit()
        z_block.append("R", z_syndromes)
        z_block.append("TICK", tag="HGP:Z:start")
        for layer in self.z_layers:
            self._append_layer(z_block, layer)
        z_block.append("M", z_syndromes)

        return x_block, z_block


HGPCodeExtractionBlock = HGPProductColorationExtractionBlock


__all__ = [
    "HGPCodeExtractionBlock",
    "HGPProductColorLayer",
    "HGPProductColorationExtractionBlock",
]
