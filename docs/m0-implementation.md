# M0 implementation sequence

1. **M0.1 (initial slice):** immutable generic structures, nested references,
   absolute/anchor/previous pitch relationships, explicit beat timing, separate
   note interpretation, and per-instance transpose/time scaling. Behavioral
   tests cover the four-element harmonic structure and explicit motifs.
2. **M0.2:** shared harmonic context, scheduler, seeded instance state and
   generation, timestamped mock recording/replay and a reproducible CLI demo.
   Implemented for fixed-tempo ahead-of-time mock composition; see [M0.2](m0-2.md).
3. **M0.3:** versioned validated project/control files and semantic keyboard actions.
   Implemented for next-generation mock session settings; see [M0.3](m0-3.md).
   Timed live control of an audio transport remains pending.
4. **M0.4:** provisional SuperCollider adapter, native synth/effects and rendering.
   Implemented with tested headless NRT rendering; see [M0.4](m0-4.md).
   Live hardware timing and listening remain Fedora validation tasks.
5. **M0.5:** Fedora Toolbox setup, playback/render and device validation instructions.

## Current API example

```python
from dinad.objects import AnchorPitch, Element, MusicalObject, ObjectInstance
from dinad.interpreters import interpret_notes

harmony = MusicalObject(tuple(
    Element(AnchorPitch(interval), start=i * 4, duration=4)
    for i, interval in enumerate((0, 5, 9, 7))
))
notes = interpret_notes(ObjectInstance(harmony, anchor=48))
assert [note.pitch for note in notes] == [48, 53, 57, 55]
```

Definitions have no instrument or chord/motif role. This interpreter produces
immutable note values with semitone pitch numbers (MIDI convention, C4 = 60)
and beat onsets/durations; it does not schedule or emit backend events.
Anchor intervals are chromatic semitone offsets, with zero meaning unison.
Named/diatonic intervals and pitch spelling are deferred.

`ObjectReference` offsets a child in local beats. References share immutable
objects. Depth-first tuple order defines previous-relative pitch, including
across child boundaries; it is deliberately independent of onset ordering.
A leading previous-relative pitch raises an error. The interpreter preserves
traversal order, including simultaneous onsets. M0.2 must define scheduler
ordering explicitly when it turns these values into timed events.

`ObjectInstance` holds anchor and transform settings. Transpose shifts all
resolved pitches once, including absolute pitches; time scaling multiplies
onsets, reference offsets, and durations. Reuse or `dataclasses.replace` an
instance to change settings without changing the definition.

## Limits and next decisions

This is the smallest M0.1 slice, not the full representation described by the
frozen architecture. Beat timing is supported; absolute seconds, proportional
and event-relative timing need explicit types and resolution contexts in a
follow-up. Metadata, harmonic/structural interpreters, mutable running state,
and named references/project serialization remain pending in the object model.
Seeded demo generation and resolved-history serialization are provided by M0.2.
No GUI, audio engine or new runtime dependencies have been added.

The baseline package installs on Python 3.12 and its scaffold test passes.
Python 3.11 is declared as the minimum but has not been exercised here. No
architectural inconsistency or missing prerequisite blocks this slice; pytest
is the only development extra. There is no CI workflow in the bootstrap.

Before extending timing, agree how proportional values obtain their enclosing
span and how event-relative references identify events. M0.2 defines
shared-context boundaries and simultaneous-event ordering in its contract.
These are implementation questions within the frozen design, not proposed
architecture changes.
