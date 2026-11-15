# Private DINAD Flatpak

GitHub Actions builds Python wheels in the Freedesktop 25.08 SDK,
installs them offline, builds headless SuperCollider and MDA ePiano
from checksum-pinned sources, and exports a private bundle and revision.
DINAD keeps its PySide6 panel and in-window TOML editor.

Install the bundle with `flatpak install --user ./dinad.flatpak`, then
launch DINAD from the app menu or run `flatpak run io.github.dinad.Dinad`.
The launcher starts a private loopback SuperCollider server within the
sandbox. It routes the drone and pad through that server and the arps
through ePiano, with both stereo outputs connected to the PipeWire
default sink. Set both ePiano output destinations in the modules TOML
to override the automatic ePiano route. The default sink needs FL and FR
inputs and the audio graph must run at 48 kHz.

The app seeds editable copies of the demo project, controls and modules
in its XDG data directory. Global settings stay in its XDG config
directory. For CLI use, the packaged interpreter is available through
`flatpak run --command=/app/venv/bin/python io.github.dinad.Dinad -m dinad`.
