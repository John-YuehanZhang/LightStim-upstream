"""System-wide HGP product coloration: several active HGP patches in one round."""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest
import stim

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from conftest import assert_noiseless

from lightstim.ir.builder import CircuitBuilder
from lightstim.ir.qec_system import QECSystem
from lightstim.ir.tracker import SyndromeTracker
from lightstim.noise.config import NoiseConfig
from lightstim.qec_code.generic_css import GenericCSSColorationExtractionBlock
from lightstim.qec_code.HGP import (
    HGPCode,
    HGPCodeLogicalOpSet,
    HGPProductColorationExtractionBlock,
    PuncturedHGPCode,
    hgp_13_1_3,
    hgp_18_2_3,
    hgp_225_9_4,
    information_bits,
)
from lightstim.qec_code.surface_code.unrotated import UnrotatedSurfaceCode


NOISE = NoiseConfig(p_1q=1e-3, p_2q=1e-3, p_meas=1e-3, p_reset=1e-3, p_idle=1e-3)
HAMMING = np.array(
    [[1, 0, 1, 0, 1, 0, 1], [0, 1, 1, 0, 0, 1, 1], [0, 0, 0, 1, 1, 1, 1]], dtype=np.uint8
)


def _system(*patches_with_offsets):
    system = QECSystem()
    registered = []
    for name, patch, offset in patches_with_offsets:
        registered.append(system.add_patch(patch, offset=offset, name=name))
    return system, registered


def _builder(system, basis="Z"):
    tracker = SyndromeTracker(num_qubits=system.num_qubits, expected_num_logicals=system.num_logicals)
    builder = CircuitBuilder(tracker=tracker, system_config=system, if_detector=True)
    system.register_tracker(tracker)
    system.register_builder(builder)
    builder.write_coordinates()
    data = sorted(system.data_indices)
    builder.initialize({q: basis for q in data}, system.num_qubits)
    system.active_qubit_indices.update(data)
    return builder


def _single_patch_pairs(patch_factory, offset, name):
    """The single-patch schedule of ``patch`` expressed in local indices, per (basis, direction, color)."""
    system, (patch,) = _system((name, patch_factory(), offset))
    block = HGPProductColorationExtractionBlock(system)
    inverse = {g: l for l, g in block.local_to_global.items()}
    return {
        (layer.basis, layer.direction, layer.color): tuple(sorted((inverse[a], inverse[b]) for a, b in layer.cnot_pairs))
        for layer in block.layers
    }


# ---------------------------------------------------------------------------
# Single patch: unchanged
# ---------------------------------------------------------------------------

def test_single_patch_view_is_unchanged():
    system, (patch,) = _system(("hgp225", hgp_225_9_4(), (0, 0)))
    block = HGPProductColorationExtractionBlock(system)
    assert block.patch_names == ("hgp225",)
    assert block.patch_name == "hgp225" and block.patch is not None
    assert block.local_to_global is block.local_to_global_maps["hgp225"]
    assert block.horizontal_seed_colors is not None and block.vertical_seed_colors is not None
    assert (block.depth_x, block.depth_z, block.cnot_depth) == (8, 8, 16)
    assert all(layer.patch_name == "hgp225" and layer.parts == () for layer in block.layers)
    assert all(layer.seed_edges for layer in block.layers)
    # Explicit patch_name selects the same schedule.
    named = HGPProductColorationExtractionBlock(system, patch_name="hgp225")
    assert named.circuit == block.circuit


# ---------------------------------------------------------------------------
# Several patches in one round
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "specs",
    [
        [("a", hgp_13_1_3, (0, 0)), ("b", hgp_13_1_3, (20, 0))],
        [("a", hgp_13_1_3, (0, 0)), ("b", hgp_18_2_3, (20, 0))],
        [("data", hgp_225_9_4, (0, 0)), ("anc", lambda: PuncturedHGPCode(hgp_225_9_4(), "horizontal", information_bits(hgp_225_9_4().h2)[1:]), (40, 0))],
        [("a", hgp_225_9_4, (0, 0)), ("b", lambda: HGPCode(HAMMING), (40, 0)), ("c", hgp_13_1_3, (60, 0))],
    ],
    ids=["two_13_1_3", "13_1_3+18_2_3", "225+punctured", "225+hamming2+13_1_3"],
)
def test_multi_patch_block_merges_the_single_patch_schedules(specs):
    system, patches = _system(*((name, factory(), offset) for name, factory, offset in specs))
    block = HGPProductColorationExtractionBlock(system)
    names = [name for name, _, _ in specs]
    assert block.patch_names == tuple(names)
    assert block.patch_name is None and block.patch is None and block.local_to_global is None
    assert set(block.patch_layers) == set(names)

    # Depth per basis = max vertical colors + max horizontal colors over the patches.
    for basis, merged in (("X", block.x_layers), ("Z", block.z_layers)):
        index = 0 if basis == "X" else 1
        max_vertical = max(sum(1 for l in block.patch_layers[n][index] if l.direction == "vertical") for n in names)
        max_horizontal = max(sum(1 for l in block.patch_layers[n][index] if l.direction == "horizontal") for n in names)
        assert len(merged) == max_vertical + max_horizontal
        for layer in merged:
            assert layer.patch_name is None and layer.seed_edges == () and layer.parts
            assert {part.patch_name for part in layer.parts} <= set(names)
            assert all((part.basis, part.direction, part.color) == (layer.basis, layer.direction, layer.color) for part in layer.parts)
            assert layer.cnot_pairs == tuple(sorted(pair for part in layer.parts for pair in part.cnot_pairs))
            qubits = [q for pair in layer.cnot_pairs for q in pair]
            assert len(qubits) == len(set(qubits))

    # Every patch's per-patch layers equal its stand-alone single-patch schedule.
    for name, factory, offset in specs:
        alone = _single_patch_pairs(factory, offset, name)
        inverse = {g: l for l, g in block.local_to_global_maps[name].items()}
        x_layers, z_layers = block.patch_layers[name]
        here = {
            (layer.basis, layer.direction, layer.color): tuple(sorted((inverse[a], inverse[b]) for a, b in layer.cnot_pairs))
            for layer in x_layers + z_layers
        }
        assert here == alone

    # Every active stabilizer is measured exactly once per round.
    total = sum(len(p.stabilizers) for p in patches)
    assert sum(b.num_measurements for b in block.measurement_blocks) == total
    assert block.measurement_blocks[0].num_measurements == len(system.active_syndrome_indices_x)
    assert block.measurement_blocks[1].num_measurements == len(system.active_syndrome_indices_z)


def test_multi_patch_round_emits_one_detector_per_stabilizer_and_is_noiseless():
    parent = hgp_225_9_4()
    ancilla = PuncturedHGPCode(hgp_225_9_4(), "horizontal", information_bits(parent.h2)[1:])
    system, (data, anc) = _system(("data", parent, (0, 0)), ("anc", ancilla, (40, 0)))
    builder = _builder(system, "Z")
    block = HGPProductColorationExtractionBlock(system)
    rounds = 3
    builder.apply_syndrome_extraction(block.circuit, rounds=rounds, measurement_blocks=block.measurement_blocks)
    first = builder.circuit.num_detectors
    builder.apply_syndrome_extraction(block.circuit, rounds=rounds, measurement_blocks=block.measurement_blocks)
    steady = builder.circuit.num_detectors - first
    assert steady == rounds * (len(data.stabilizers) + len(anc.stabilizers))
    builder.apply_data_readout({q: "Z" for q in sorted(system.data_indices)})
    assert builder.circuit.num_observables == data.num_logicals + anc.num_logicals
    assert_noiseless(builder.circuit)


def test_multi_patch_round_depth_equals_single_patch_depth_for_equal_seeds():
    system, _ = _system(("a", hgp_225_9_4(), (0, 0)), ("b", hgp_225_9_4(), (40, 0)))
    block = HGPProductColorationExtractionBlock(system)
    assert (block.depth_x, block.depth_z, block.cnot_depth) == (8, 8, 16)
    ticks = sum(1 for inst in block.circuit.flattened() if inst.name == "TICK")
    single_system, _ = _system(("a", hgp_225_9_4(), (0, 0)))
    single_ticks = sum(1 for inst in HGPProductColorationExtractionBlock(single_system).circuit.flattened() if inst.name == "TICK")
    assert ticks == single_ticks


def test_multi_patch_block_agrees_with_generic_css_on_the_edge_set():
    system, _ = _system(("a", hgp_13_1_3(), (0, 0)), ("b", hgp_18_2_3(), (20, 0)))
    product = HGPProductColorationExtractionBlock(system)
    generic = GenericCSSColorationExtractionBlock(system)
    generic_edges = {("X", edge) for layer in generic.x_layers for edge in layer} | {
        ("Z", (data, syndrome)) for layer in generic.z_layers for syndrome, data in layer
    }
    product_edges = {(layer.basis, pair) for layer in product.layers for pair in layer.cnot_pairs}
    assert generic_edges == product_edges


def test_homomorphic_cnot_between_blocks_scheduled_by_product_coloration():
    parent = hgp_225_9_4()
    ancilla = PuncturedHGPCode(hgp_225_9_4(), "horizontal", information_bits(parent.h2)[1:])
    system, (data, anc) = _system(("data", parent, (0, 0)), ("anc", ancilla, (40, 0)))
    builder = _builder(system, "Z")
    block = HGPProductColorationExtractionBlock(system)
    builder.apply_syndrome_extraction(block.circuit, rounds=2, measurement_blocks=block.measurement_blocks)
    before = builder.circuit.num_detectors
    HGPCodeLogicalOpSet().homomorphic_cnot(builder, data, anc)
    builder.apply_syndrome_extraction(block.circuit, rounds=2, measurement_blocks=block.measurement_blocks)
    assert builder.circuit.num_detectors - before == 2 * (len(data.stabilizers) + len(anc.stabilizers))
    builder.apply_data_readout({q: "Z" for q in sorted(system.data_indices)})
    assert_noiseless(builder.circuit)
    noisy = builder.build_noisy_circuit(NOISE, "circuit_level")
    assert noisy.detector_error_model(decompose_errors=False).num_detectors == noisy.num_detectors


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def test_inactive_patches_are_skipped_and_named_selection_requires_exclusivity():
    system = QECSystem()
    system.add_patch(hgp_13_1_3(), name="active")
    system.add_patch(hgp_13_1_3(), offset=(20, 0), name="inactive", is_active=False)
    block = HGPProductColorationExtractionBlock(system)
    assert block.patch_names == ("active",) and block.patch_name == "active"

    system, _ = _system(("a", hgp_13_1_3(), (0, 0)), ("b", hgp_13_1_3(), (20, 0)))
    with pytest.raises(ValueError, match="restricts the block to one patch"):
        HGPProductColorationExtractionBlock(system, patch_name="a")
    with pytest.raises(ValueError, match="no active HGP patch named 'zzz'"):
        HGPProductColorationExtractionBlock(system, patch_name="zzz")
    with pytest.raises(ValueError, match="align must be"):
        HGPProductColorationExtractionBlock(system, align="diagonal")


def test_index_alignment_gives_the_shortest_round_for_unequal_seeds():
    """HGP(Hamming, rep3) has (2 vertical, 4 horizontal) colors, HGP(rep3, Hamming)
    the reverse: direction alignment needs 4+4 layers per basis, index alignment 6."""
    rep3 = np.array([[1, 1, 0], [0, 1, 1]], dtype=np.uint8)
    system, patches = _system(("hr", HGPCode(HAMMING, rep3), (0, 0)), ("rh", HGPCode(rep3, HAMMING), (30, 0)))
    by_direction = HGPProductColorationExtractionBlock(system)
    by_index = HGPProductColorationExtractionBlock(system, align="index")
    per_patch = {
        name: (sum(1 for l in by_direction.patch_layers[name][0] if l.direction == "vertical"),
               sum(1 for l in by_direction.patch_layers[name][0] if l.direction == "horizontal"))
        for name in ("hr", "rh")
    }
    assert per_patch == {"hr": (2, 4), "rh": (4, 2)}
    assert by_direction.depth_x == 8 and by_index.depth_x == 6
    assert by_direction.depth_z == 8 and by_index.depth_z == 6
    # Same CNOT edge set, every layer conflict-free, every stabilizer measured once.
    edges = lambda block: {(l.basis, pair) for l in block.layers for pair in l.cnot_pairs}
    assert edges(by_direction) == edges(by_index)
    for layer in by_index.layers:
        qubits = [q for pair in layer.cnot_pairs for q in pair]
        assert len(qubits) == len(set(qubits))
    assert sum(b.num_measurements for b in by_index.measurement_blocks) == sum(len(p.stabilizers) for p in patches)
    # End to end: full detector count per round and noiseless.
    for block in (by_direction, by_index):
        sys2, (a, b) = _system(("hr", HGPCode(HAMMING, rep3), (0, 0)), ("rh", HGPCode(rep3, HAMMING), (30, 0)))
        builder = _builder(sys2, "Z")
        blk = HGPProductColorationExtractionBlock(sys2, align=block.align)
        builder.apply_syndrome_extraction(blk.circuit, rounds=2, measurement_blocks=blk.measurement_blocks)
        first = builder.circuit.num_detectors
        builder.apply_syndrome_extraction(blk.circuit, rounds=2, measurement_blocks=blk.measurement_blocks)
        assert builder.circuit.num_detectors - first == 2 * (len(a.stabilizers) + len(b.stabilizers))
        builder.apply_data_readout({q: "Z" for q in sorted(sys2.data_indices)})
        assert_noiseless(builder.circuit)


def test_mixed_code_families_are_rejected_with_a_pointer_to_the_generic_block():
    system, _ = _system(("hgp", hgp_13_1_3(), (0, 0)), ("usc", UnrotatedSurfaceCode(distance=3), (20, 0)))
    with pytest.raises(ValueError, match="owned by \\['usc'\\].*generic CSS"):
        HGPProductColorationExtractionBlock(system)


def test_no_active_hgp_patch_is_rejected():
    system, _ = _system(("usc", UnrotatedSurfaceCode(distance=3), (0, 0)))
    with pytest.raises(ValueError, match="at least one active HGP patch"):
        HGPProductColorationExtractionBlock(system)
