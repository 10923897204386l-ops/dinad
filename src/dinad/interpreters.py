"""Late-bound note interpretation, independent of scheduling and audio."""

from dataclasses import dataclass

from dinad.objects import (
    AbsolutePitch, AnchorPitch, Element, MusicalObject, ObjectInstance,
)


@dataclass(frozen=True)
class Note:
    pitch: float
    start: float
    duration: float


def interpret_notes(instance: ObjectInstance) -> tuple[Note, ...]:
    """Resolve depth-first definition order, retaining explicit beat onsets.

    PreviousPitch refers to the previous leaf across reference boundaries, not
    the chronologically previous onset. Output retains traversal order so equal
    onsets have a stable order. A leading PreviousPitch is an error.
    Transpose applies once to every resolved pitch, including absolute pitches.
    """
    notes: list[Note] = []
    previous: float | None = None

    def visit(definition: MusicalObject, offset: float) -> None:
        nonlocal previous
        for item in definition.elements:
            if not isinstance(item, Element):
                visit(item.target, offset + item.start)
                continue
            if isinstance(item.pitch, AbsolutePitch):
                pitch = item.pitch.semitones
            elif isinstance(item.pitch, AnchorPitch):
                pitch = instance.anchor + item.pitch.semitones
            else:
                if previous is None:
                    raise ValueError("previous-relative pitch requires a preceding element")
                pitch = previous + item.pitch.semitones
            previous = pitch
            notes.append(Note(
                pitch + instance.transpose,
                (offset + item.start) * instance.time_scale,
                item.duration * instance.time_scale,
            ))

    visit(instance.definition, 0)
    return tuple(notes)
