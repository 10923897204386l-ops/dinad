import pytest

from dinad.editor import Document, EditError, EditorSession, stage_edit
from dinad.transport import SilentOutput, Transport


def text(anchor=48, tempo=120):
    return f"schema_version = 1\n[composition]\nanchor = {anchor}\n[transport]\ntempo_bpm = {tempo}\n"


def model(tmp_path, output=None, modules=False, profile=False):
    path = tmp_path / "project.toml"
    path.write_text(text())
    documents = {"project": Document.read("project", path)}
    if modules:
        path = tmp_path / "modules.toml"
        path.write_text("schema_version = 1\n")
        documents["modules"] = Document.read("modules", path)
    if profile:
        path = tmp_path / "controls.toml"
        path.write_text('schema_version = 1\n[bindings]\n"+" = "adjust composition.anchor 1"\n')
        documents["profile"] = Document.read("profile", path)
    return EditorSession(documents, lambda p, f, m: Transport(p, f, output or SilentOutput(), lambda: 100., lead=0.))


def test_project_save_preserves_running_clock_and_active_gates(tmp_path):
    session = model(tmp_path)
    transport = session.transport
    transport.start()
    transport.advance()
    original = transport.origin, transport.tick, dict(transport.active)
    edit = stage_edit(session.documents["project"], text(anchor=56, tempo=90))
    session.apply(edit)
    assert transport.running and session.transport is transport
    assert (transport.origin, transport.tick, transport.active) == original
    assert transport.session.project.anchor == 56
    assert transport.session.project.tempo == 90
    assert edit.replacement.path.read_text() == text(56, 90)
    assert not edit.temporary.exists()
    assert [action["kind"] for action in transport.actions] == ["config", "config"]
    transport.stop()


@pytest.mark.parametrize(("source", "line"), [
    ("schema_version = 1\nanchor = ]\n", 2),
    (text(anchor=999), 3),
])
def test_invalid_toml_keeps_file_and_gives_location(tmp_path, source, line):
    session = model(tmp_path)
    document = session.documents["project"]
    with pytest.raises(EditError) as caught:
        stage_edit(document, source)
    assert caught.value.location[0] == line
    assert document.path.read_text() == text()
    assert session.transport.session.project.anchor == 48
    assert list(tmp_path.iterdir()) == [document.path]


def test_runtime_validation_failure_rolls_back(tmp_path):
    class Output(SilentOutput):
        def validate(self, events):
            if max(event.pitch for event in events) > 100:
                raise ValueError("pitch exceeds backend limit")

    session = model(tmp_path, Output())
    session.transport.start()
    document = session.documents["project"]
    edit = stage_edit(document, text(anchor=120))
    with pytest.raises(EditError, match="pitch exceeds") as caught:
        session.apply(edit)
    assert caught.value.location == (3, 1)
    assert document.path.read_text() == text()
    assert session.transport.running and session.transport.session.project.anchor == 48
    assert not edit.temporary.exists()
    session.transport.stop()


def test_rename_failure_rolls_back(tmp_path, monkeypatch):
    session = model(tmp_path)
    document = session.documents["project"]
    edit = stage_edit(document, text(anchor=60))

    def fail(*args):
        raise OSError("rename failed")

    monkeypatch.setattr("dinad.editor.os.replace", fail)
    with pytest.raises(OSError, match="rename failed"):
        session.apply(edit)
    assert document.path.read_text() == text()
    assert session.transport.session.project.anchor == 48
    assert not edit.temporary.exists()


def test_external_change_requires_reload(tmp_path):
    session = model(tmp_path)
    document = session.documents["project"]
    edit = stage_edit(document, text(anchor=60))
    document.path.write_text(text(anchor=50))
    with pytest.raises(EditError, match="changed on disk"):
        session.apply(edit)
    assert document.path.read_text() == text(anchor=50)
    assert session.transport.session.project.anchor == 48


def test_live_provider_change_requires_stop(tmp_path):
    session = model(tmp_path, modules=True)
    original = session.transport
    original.start()
    document = session.documents["modules"]
    source = """
schema_version = 1
[operations.live]
module = "dinad.mock"
version = "mock-v1"
"""
    edit = stage_edit(document, source)
    with pytest.raises(EditError, match="Stop playback"):
        session.apply(edit)
    assert document.path.read_bytes() == document.content
    assert original.running and session.transport is original
    original.stop()
    assert "rebuilt" in session.apply(stage_edit(document, source))
    assert session.transport is not original and not session.transport.running
    assert document.path.read_text() == source


def test_controller_edit_validates_and_applies_only_when_stopped(tmp_path):
    original = model(tmp_path)
    path = tmp_path / "controller.toml"
    path.write_text("schema_version = 1\ncontroller = []\n")
    documents = {**original.documents, "controller": Document.read("controller", path)}
    session = EditorSession(documents, original.factory)
    source = """schema_version = 1
[[controller]]
beat = 0
parameter = "transport.tempo_bpm"
value = 90
"""
    document = documents["controller"]
    with pytest.raises(EditError, match="must be between"):
        stage_edit(document, source.replace("90", "0"))
    session.transport.start()
    with pytest.raises(EditError, match="Stop playback"):
        session.apply(stage_edit(document, source))
    assert path.read_bytes() == document.content
    assert session.transport.controller == ()
    session.transport.stop()
    session.apply(stage_edit(document, source))
    assert path.read_text() == source
    session.transport.start()
    session.transport.advance()
    assert session.transport.session.project.tempo == 90
    session.transport.stop()
