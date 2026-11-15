import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from dinad.gui import Window
from dinad.panel_worker import PanelWorker
from test_editor import model, text


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


def test_f12_save_validation_focus_and_reload(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    session = model(tmp_path, profile=True)
    window = Window(PanelWorker(session))
    window.show()
    window.activateWindow()
    QTest.qWait(50)
    try:
        QTest.keyClick(window, Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier)
        wait_for(lambda: window.state.running)
        window.stop_button.setFocus()
        QTest.keyClick(window.stop_button, Qt.Key.Key_QuoteLeft)
        assert window.overlay.isVisible() and window.overlay.window() is window
        assert app.focusWidget() is window.editor
        QTest.keyClick(window.editor, Qt.Key.Key_QuoteLeft)
        assert window.overlay.isVisible() and "`" in window.editor.toPlainText()
        QTest.keyClick(window.editor, Qt.Key.Key_Escape)
        assert not window.overlay.isVisible() and window.state.running
        assert app.focusWidget() is window.stop_button
        QTest.keyClick(window.stop_button, Qt.Key.Key_QuoteLeft)
        assert window.overlay.isVisible() and window.dirty
        assert "`" in window.editor.toPlainText()
        window.editor.setPlainText("schema_version = 1\nanchor = ]\n")
        QTest.keyClick(window.editor, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        wait_for(lambda: not window.busy)
        assert "Line 2, column" in window.diagnostic.text()
        assert window.dirty and window.state.running
        window.editor.setPlainText(text(anchor=55))
        QTest.keyClick(window.editor, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        wait_for(lambda: not window.busy)
        assert window.state.running and window.state.project.anchor == 55 and not window.dirty
        assert (tmp_path / "project.toml").read_text() == text(anchor=55)
        window.editor.insertPlainText("# temporary draft")
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
        window.reload_button.click()
        assert window.dirty
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Discard)
        window.reload_button.click()
        wait_for(lambda: not window.busy)
        assert not window.dirty
        QTest.keyClick(window.editor, Qt.Key.Key_F12)
        assert not window.overlay.isVisible()
        window.profile_key.setText("+")
        window.profile_key.setFocus()
        QTest.keyClick(window.profile_key, Qt.Key.Key_Return)
        wait_for(lambda: not window.busy)
        assert window.state.running and window.state.project.anchor == 56
        window.stop_button.setFocus()
        QTest.keyClick(window.stop_button, Qt.Key.Key_Plus, Qt.KeyboardModifier.ShiftModifier)
        wait_for(lambda: window.state.project.anchor == 57)
        window.profile_key.setFocus()
        QTest.keyClick(window.profile_key, Qt.Key.Key_Plus, Qt.KeyboardModifier.ShiftModifier)
        assert window.profile_key.text() == "+" and not window.busy
        assert window.state.project.anchor == 57
        QTest.keyClick(window.profile_key, Qt.Key.Key_QuoteLeft)
        assert not window.overlay.isVisible() and window.profile_key.text() == "+`"
        window.stop_button.setFocus()
        QTest.keyClick(window.stop_button, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
        seed = window.knobs["knob7"][1]
        assert app.focusWidget() is seed
        QTest.keyClick(seed, Qt.Key.Key_Up, Qt.KeyboardModifier.ShiftModifier)
        wait_for(lambda: not window.busy)
        assert window.state.project.seed == 10 and window.state.running
        assert "Pending seed 10" in window.seed_status.text()
        QTest.keyClick(window, Qt.Key.Key_F12)
        window.editor.moveCursor(window.editor.textCursor().MoveOperation.End)
        QTest.keyClick(window.editor, Qt.Key.Key_Plus, Qt.KeyboardModifier.ShiftModifier)
        assert window.editor.toPlainText().endswith("+") and not window.busy
        assert window.state.project.anchor == 57
        assert (tmp_path / "project.toml").read_text() == text(anchor=55)
    finally:
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Discard)
        window.close()
        wait_for(lambda: not window.worker.thread.is_alive())
        QTest.qWait(30)


def test_performance_panel_saves_take_and_reports_incompatible_replay(tmp_path):
    from dinad.recording import decode

    app = QApplication.instance() or QApplication([])
    session = model(tmp_path)
    session.transport.clock = time.monotonic
    window = Window(PanelWorker(session))
    window.show()
    try:
        window.start_button.click()
        wait_for(lambda: window.state.active_seed is not None)
        QTest.qWait(150)
        window.stop_button.click()
        wait_for(lambda: window.state.can_save)
        take = tmp_path / "performance.json"
        window.take_path.setText(str(take))
        window.capture_button.click()
        wait_for(lambda: not window.busy)
        assert decode(take.read_text())
        before = take.read_bytes()
        window.capture_button.click()
        wait_for(lambda: not window.busy)
        assert take.read_bytes() == before
        window.replay_button.click()
        wait_for(lambda: not window.busy)
        assert "select the DINAD ePiano" in window.status.text()
        assert not window.state.running
    finally:
        window.close()
        wait_for(lambda: not window.isVisible())


def test_text_scale_applies_to_panel_and_editor_and_persists(tmp_path):
    from dinad.editor import Document, EditorSession, stage_edit

    app = QApplication.instance() or QApplication([])
    original = model(tmp_path)
    path = tmp_path / "settings.toml"
    source = "schema_version = 1\n[ui]\ntext_scale = 1.0\n"
    path.write_text(source)
    document = Document.read("settings", path)
    session = EditorSession({**original.documents, "settings": document}, original.factory)
    window = Window(PanelWorker(session))
    window.show()
    try:
        widgets = (window.start_button, window.editor)
        initial = [widget.font().pointSizeF() for widget in widgets]
        window._submit("save", lambda model: model.apply(stage_edit(document, source.replace("1.0", "1.25"))))
        wait_for(lambda: not window.busy)
        assert [widget.font().pointSizeF() for widget in widgets] == [size * 1.25 for size in initial]
        assert "text_scale = 1.25" in path.read_text()
    finally:
        window.close()
        wait_for(lambda: not window.isVisible())


def test_fixed_controls_follow_profile_remap(tmp_path):
    from dinad.editor import stage_edit

    app = QApplication.instance() or QApplication([])
    session = model(tmp_path, profile=True)
    window = Window(PanelWorker(session))
    window.show()
    try:
        original = window.knobs["knob1"]
        source = session.documents["profile"].text + """
[knobs]
knob1 = "composition.tension"
[pads]
pad1 = "set composition.tension 2"
"""
        edit = stage_edit(session.documents["profile"], source)
        window._submit("save", lambda model: model.apply(edit))
        wait_for(lambda: not window.busy)
        assert window.knobs["knob1"] == original
        label, dial, display = original
        assert label.text() == "Tension"
        assert not window.knobs["knob2"][1].isEnabled()
        window.pads["pad1"].click()
        wait_for(lambda: not window.busy)
        assert dial.value() == 2 and display.value() == 2
        dial.setFocus()
        QTest.keyClick(dial, Qt.Key.Key_Down)
        wait_for(lambda: not window.busy)
        assert window.state.project.tension == 1
    finally:
        window.close()
        wait_for(lambda: not window.isVisible())
