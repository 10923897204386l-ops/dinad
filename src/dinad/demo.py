"""Small M0 composition: shared harmony, drone, pad and two arp clocks."""

from dataclasses import dataclass, field
from random import Random

from dinad.config import Project
from dinad.engine import Event, HarmonicContext, Part, schedule
from dinad.interpreters import Note
from dinad.objects import AnchorPitch, Element, MusicalObject, ObjectInstance


@dataclass
class VariationState:
    """Per-part random state, isolated from global random and other parts."""

    seed: int
    random: Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.seed) is not int:
            raise ValueError("seed must be an integer")
        self.random = Random(self.seed)

    def interval(self, intervals: tuple[int, ...] = (0, 4, 7, 12)) -> int:
        return self.random.choice(intervals)


def compose(seed: int = 0, anchor: float = 48, tempo: float = 120, *, tension: int = 0,
            motif: int = 0) -> tuple[Event, ...]:
    settings = Project(anchor=anchor, tempo=tempo, seed=seed, tension=tension, motif=motif)
    anchor, tempo, seed = settings.anchor, settings.tempo, settings.seed
    harmony = MusicalObject(tuple(
        Element(AnchorPitch(interval), start=index * 4, duration=4)
        for index, interval in enumerate((0, 5, 9, 7))
    ))
    context = HarmonicContext.from_instance(ObjectInstance(harmony, anchor=anchor))
    drone = Part("drone", (Note(anchor - 12, 0, 16),))
    chord = ((0, 4, 7), (0, 4, 7, 10), (0, 4, 6, 10))[settings.tension]
    slow_arp = (0, chord[2], 12, chord[-1])
    fast_arp = (*chord, 12)
    pad = Part("pad", tuple(
        Note(span.pitch + interval, span.start, span.duration)
        for span in context.spans for interval in chord
    ))
    state = VariationState(seed)
    fast_intervals = None
    if settings.motif:
        phrase = tuple(state.interval(fast_arp) for _ in range(8))
        fast_intervals = phrase
        if settings.motif == 2:
            fast_intervals = phrase * 3 + phrase[::-1]
    arps = []
    # Explicit integer tick indices prevent accumulating clock drift.
    for name, step, intervals in (
        ("arp.slow", 1, slow_arp),
        ("arp.fast", 0.5, fast_intervals),
    ):
        notes = []
        for tick in range(int(16 / step)):
            beat = tick * step
            interval = intervals[tick % len(intervals)] if intervals else state.interval(fast_arp)
            notes.append(Note(context.pitch_at(beat) + interval + 12, beat, step * 0.8))
        arps.append(Part(name, tuple(notes)))
    return schedule((drone, pad, *arps), tempo)
