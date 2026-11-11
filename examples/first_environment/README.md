# First environment

`project.toml` configures the built-in four-element harmonic structure, drone,
pad and two-clock arpeggiator. `controls.toml` maps command-session keys to stable
parameter IDs. See [M0.3 usage](../../docs/m0-3.md) for schemas and limits.

```sh
python -m dinad validate examples/first_environment/project.toml \
  --profile examples/first_environment/controls.toml
python -m dinad session --project examples/first_environment/project.toml \
  --profile examples/first_environment/controls.toml
```

Enter `key +`, `inspect`, `undo`, `generate`, or `quit`. This is a headless mock
session, with no audio output. The project format configures the built-in demo;
arbitrary musical-object serialization is not implemented yet.

`performance.toml` is an M0.6 timed-control example. Compile it with
`python -m dinad perform examples/first_environment/performance.toml recordings/take.json`
(after creating `recordings/`), then replay or render the resolved history.
