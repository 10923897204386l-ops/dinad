# Private DINAD Flatpak

The build follows Net Runner's model. GitHub Actions builds Python
wheels in the Freedesktop 25.08 SDK, installs those wheels offline
during the Flatpak build, and uploads a private bundle with its Git
revision. DINAD retains PySide6 and its in-window TOML editor.

The desktop launcher seeds an editable demo in the app's XDG data
directory. Global settings are stored in the app's XDG config directory.
The packaging preview runs the mock backend. An audio-capable migration
requires packaging the native ePiano and SuperCollider components and
verifying live stereo routing and take replay inside the sandbox.
Keep using the Fedora development launch for those workflows.

When the workflow publishes an artifact, extract it and install it with
`flatpak install --user ./dinad.flatpak`. Launch with
`flatpak run io.github.dinad.Dinad`.
