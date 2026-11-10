"""Small M0 composition: shared harmony, drone, pad and two arp clocks."""

from dataclasses import dataclass, field
from random import Random

from dinad.engine import Event, HarmonicContext, Part, schedule
from dinad.interpreters import Note
from dinad.objects import AnchorPitch, Element, MusicalObject, ObjectInstance, _finite


@dataclass
class VariationState:
    """Per-part random state, isolated from global random and other parts."""

    seed: int
    random: Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.seed) is not int:
            raise ValueError("seed must be an integer")
        self.random = Random(self.seed)

    def interval(self) -> int:
        return self.random.choice((0, 4, 7, 12))


def compose(seed: int = 0, anchor: float = 48, tempo: float = 120) -> tuple[Event, ...]:
    _finite(anchor, "anchor")
    harmony = MusicalObject(tuple(
        Element(AnchorPitch(interval), start=index * 4, duration=4)
        for index, interval in enumerate((0, 5, 9, 7))
    ))
    context = HarmonicContext.from_instance(ObjectInstance(harmony, anchor=anchor))
    drone = Part("drone", (Note(anchor - 12, 0, 16),))
    pad = Part("pad", tuple(
        Note(span.pitch + interval, span.start, span.duration)
        for span in context.spans for interval in (0, 4, 7)
    ))
    state = VariationState(seed)
    arps = []
    # Explicit integer tick indices prevent accumulating clock drift.
    for name, step, intervals in (
        ("arp.slow", 1, (0, 7, 12, 7)),
        ("arp.fast", 0.5, None),
    ):
        notes = []
        for tick in range(int(16 / step)):
            beat = tick * step
            interval = intervals[tick % len(intervals)] if intervals else state.interval()
            notes.append(Note(context.pitch_at(beat) + interval + 12, beat, step * 0.8))
        arps.append(Part(name, tuple(notes)))
    return schedule((drone, pad, *arps), tempo)
