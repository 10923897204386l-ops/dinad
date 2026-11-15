# Private DINAD Flatpak

The build follows Net Runner's model. GitHub Actions builds Python
wheels in the Freedesktop 25.08 SDK, installs those wheels offline
during the Flatpak build, and uploads a private bundle with its Git
revision. DINAD retains PySide6 and its in-window TOML editor.

The desktop launcher seeds an editable demo in the app's XDG data
directory. Global settings are stored in the app's XDG config directory.
The default launcher uses the mock backend. An audio-capable migration
requires packaging the native ePiano and SuperCollider components and
verifying live stereo routing and take replay inside the sandbox.
Keep using the Fedora development launch for those workflows.

When the workflow publishes an artifact, extract it and install it with
`flatpak install --user ./dinad.flatpak`. Launch with
`flatpak run io.github.dinad.Dinad`.

## ePiano audio preview

The audio build includes the native ePiano helper, the MDA LV2 bundle
and its discovery libraries. Sources are checksum-pinned and built
against the Flatpak SDK. The app can access the host PipeWire socket
for JACK playback. The app launcher defaults to the mock project.

To use ePiano, pass the included module config:

```sh
flatpak run io.github.dinad.Dinad --modules /app/share/dinad/first_environment/epiano.toml
```

Connect the two JACK outputs `DINAD-EPiano:left` and
`DINAD-EPiano:right` to the desired sink using PipeWire, or provide
left and right destinations in an editable copy of the module TOML.
Layered audio still requires SuperCollider packaging.
