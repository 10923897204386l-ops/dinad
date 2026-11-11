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
        QTest.keyClick(window, Qt.Key.Key_F12)
        assert window.overlay.isVisible() and window.overlay.window() is window
        assert app.focusWidget() is window.editor
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
        QTest.keyClick(window.profile_key, Qt.Key.Key_F12)
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
