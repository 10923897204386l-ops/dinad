from dataclasses import FrozenInstanceError, replace

import pytest

from dinad.interpreters import Note, interpret_notes
from dinad.objects import (
    AbsolutePitch, AnchorPitch, Element, MusicalObject, ObjectInstance,
    ObjectReference, PreviousPitch,
)


def test_four_element_harmony_follows_anchor():
    harmony = MusicalObject(tuple(
        Element(AnchorPitch(interval), start=index * 4, duration=4)
        for index, interval in enumerate((0, 5, 9, 7))
    ))
    original = ObjectInstance(harmony, anchor=48)
    assert interpret_notes(original) == tuple(
        Note(pitch, index * 4, 4) for index, pitch in enumerate((48, 53, 57, 55))
    )
    assert [n.pitch for n in interpret_notes(replace(original, anchor=60))] == [60, 65, 69, 67]
    assert original.anchor == 48


def test_explicit_motif_distinguishes_pitch_relationships():
    motif = MusicalObject((
        Element(AbsolutePitch(72)),
        Element(AnchorPitch(7), start=1),
        Element(PreviousPitch(-2), start=2),
    ))
    assert interpret_notes(ObjectInstance(motif, anchor=60)) == (
        Note(72, 0, 1), Note(67, 1, 1), Note(65, 2, 1),
    )
    assert [n.pitch for n in interpret_notes(ObjectInstance(motif, anchor=48))] == [72, 55, 53]


def test_nested_reuse_transforms_without_mutating_definition():
    child = MusicalObject((Element(AnchorPitch()), Element(PreviousPitch(7), start=0.5)))
    parent = MusicalObject((ObjectReference(child, start=2), ObjectReference(child, start=4)))
    instance = ObjectInstance(parent, transpose=12, time_scale=2)
    expected = (Note(72, 4, 2), Note(79, 5, 2), Note(72, 8, 2), Note(79, 9, 2))
    assert interpret_notes(instance) == expected
    assert interpret_notes(instance) == expected
    assert child.elements[0].start == 0
    with pytest.raises(FrozenInstanceError):
        instance.anchor = 50


def test_previous_pitch_crosses_reference_boundaries_in_definition_order():
    child = MusicalObject((Element(PreviousPitch(3)),))
    parent = MusicalObject((Element(AbsolutePitch(60), start=4), ObjectReference(child)))
    assert interpret_notes(ObjectInstance(parent)) == (Note(60, 4, 1), Note(63, 0, 1))


def test_missing_previous_pitch_fails_clearly():
    with pytest.raises(ValueError, match="preceding element"):
        interpret_notes(ObjectInstance(MusicalObject((Element(PreviousPitch()),))))


def test_empty_object_and_defensive_tuple_conversion():
    source = [Element(AnchorPitch())]
    definition = MusicalObject(source)
    source.clear()
    assert len(definition.elements) == 1
    assert interpret_notes(ObjectInstance(MusicalObject(()))) == ()


@pytest.mark.parametrize("pitch_type", [AbsolutePitch, AnchorPitch, PreviousPitch])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "60"])
def test_invalid_pitch_values(pitch_type, value):
    with pytest.raises(ValueError, match="finite number"):
        pitch_type(value)


@pytest.mark.parametrize("kwargs", [{"start": -1}, {"duration": 0}, {"duration": -1}, {"start": float("inf")}])
def test_invalid_timing(kwargs):
    with pytest.raises(ValueError):
        Element(AnchorPitch(), **kwargs)


@pytest.mark.parametrize("kwargs", [{"time_scale": 0}, {"time_scale": -1}, {"anchor": float("nan")}, {"transpose": float("inf")}])
def test_invalid_instance_context(kwargs):
    with pytest.raises(ValueError):
        ObjectInstance(MusicalObject(()), **kwargs)


def test_reject_ambiguous_or_invalid_structure():
    with pytest.raises(TypeError, match="explicit pitch"):
        Element(60)
    with pytest.raises(TypeError, match="object elements"):
        MusicalObject((60,))
    with pytest.raises(TypeError, match="target"):
        ObjectReference("missing")
    with pytest.raises(ValueError, match="non-negative"):
        ObjectReference(MusicalObject(()), start=-1)
