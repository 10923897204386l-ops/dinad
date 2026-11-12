"""Optional DINAD Qt Widgets presentation layer."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty
import re
import shlex

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFontDatabase, QKeySequence, QShortcut, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from dinad.config import PARAMETERS
from dinad.editor import Document, EditorSession, stage_edit
from dinad.execution import live_transport, load_take, play_take
from dinad.panel_worker import PanelWorker
from dinad.recording import save_take


class TomlHighlight(QSyntaxHighlighter):
    tokens = re.compile(
        r'(?P<string>"(?:\\.|[^"\\])*"|\x27[^\x27]*\x27)|(?P<comment>\#.*$)'
        r'|(?P<table>^\s*\[+[^\]]+\]+)|(?P<value>\b(?:true|false|\d[\d_.eE+-]*)\b)'
        r'|(?P<key>[\w.-]+)(?=\s*=)'
    )
    colors = {"string": "#26774c", "comment": "#747474", "table": "#6045a4",
              "value": "#955022", "key": "#205b9b"}

    def highlightBlock(self, text):
        for match in self.tokens.finditer(text):
            style = QTextCharFormat()
            style.setForeground(QColor(self.colors[match.lastgroup]))
            start = len(text[:match.start()].encode("utf-16-le")) // 2
            length = len(match[0].encode("utf-16-le")) // 2
            self.setFormat(start, length, style)


class Window(QMainWindow):
    def __init__(self, worker: PanelWorker):
        super().__init__()
        self.worker = worker
        self.state = worker.initial
        self.file_jobs = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dinad-file")
        self.future = None
        self.pending = None
        self.file_operation = None
        self.closing = False
        self.previous_focus = None
        self.kind = "project"
        self.setWindowTitle("DINAD")
        self.resize(760, 490)
        self.panel = QWidget()
        self.setCentralWidget(self.panel)
        layout = QVBoxLayout(self.panel)
        self.project_name = QLabel()
        self.transport_status = QLabel()
        layout.addWidget(self.project_name)
        layout.addWidget(self.transport_status)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")
        self.editor_button = QPushButton("Configuration  ` / F12")
        for button in (self.start_button, self.stop_button, self.editor_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        fields = QFormLayout()
        self.fields = {}
        for identifier, descriptor in PARAMETERS.items():
            field = QLineEdit()
            field.returnPressed.connect(lambda key=identifier: self._control(key))
            label = "Seed (Ctrl+R)" if identifier == "generation.seed" else descriptor.label
            fields.addRow(label, field)
            self.fields[identifier] = field
        self.seed_status = QLabel()
        fields.addRow(self.seed_status)
        self.profile_key = QLineEdit()
        self.profile_key.setPlaceholderText("Enter a key from the loaded profile")
        self.profile_key.returnPressed.connect(self._profile_control)
        fields.addRow("Profile key", self.profile_key)
        layout.addLayout(fields)
        take = QHBoxLayout()
        self.take_path = QLineEdit()
        self.take_path.setPlaceholderText("Path to take.json")
        self.take_path.setAccessibleName("Take path")
        self.capture_button = QPushButton("Save take  Ctrl+Shift+S")
        self.replay_button = QPushButton("Replay take  Ctrl+Shift+P")
        take.addWidget(self.take_path)
        take.addWidget(self.capture_button)
        take.addWidget(self.replay_button)
        layout.addLayout(take)
        self.audio_diagnostics = QLabel()
        self.audio_diagnostics.setTextFormat(Qt.TextFormat.PlainText)
        self.audio_diagnostics.setWordWrap(True)
        layout.addWidget(self.audio_diagnostics)
        layout.addStretch()
        self.status = QLabel("Ready")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.overlay = QFrame(self.panel)
        self.overlay.setFrameShape(QFrame.Shape.StyledPanel)
        self.overlay.setAutoFillBackground(True)
        editing = QVBoxLayout(self.overlay)
        toolbar = QHBoxLayout()
        self.files = QComboBox()
        self.files.addItems(self.state.documents)
        self.save_button = QPushButton("Save & apply")
        self.reload_button = QPushButton("Reload")
        for widget in (self.files, self.save_button, self.reload_button):
            toolbar.addWidget(widget)
        close_editor = QPushButton("Close  Esc")
        close_editor.clicked.connect(self.toggle_editor)
        toolbar.addWidget(close_editor)
        editing.addLayout(toolbar)
        self.path = QLabel()
        self.path.setWordWrap(True)
        editing.addWidget(self.path)
        self.editor = QPlainTextEdit()
        self.editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.highlighter = TomlHighlight(self.editor.document())
        editing.addWidget(self.editor)
        self.diagnostic = QLabel()
        self.diagnostic.setWordWrap(True)
        for label in (self.project_name, self.path, self.status, self.diagnostic):
            label.setTextFormat(Qt.TextFormat.PlainText)
        editing.addWidget(self.diagnostic)
        self.overlay.hide()
        self._show_document()
        self.editor.textChanged.connect(self._update_dirty)
        self.files.currentTextChanged.connect(self._switch_document)
        self.save_button.clicked.connect(self._save)
        self.reload_button.clicked.connect(self._reload)
        self.start_button.clicked.connect(self._start)
        self.stop_button.clicked.connect(self._stop)
        self.editor_button.clicked.connect(self.toggle_editor)
        self.capture_button.clicked.connect(self._capture_take)
        self.replay_button.clicked.connect(self._replay_take)
        self.shortcuts = [
            QShortcut(QKeySequence("F12"), self, activated=self.toggle_editor),
            QShortcut(QKeySequence("Esc"), self.overlay,
                      context=Qt.ShortcutContext.WidgetWithChildrenShortcut,
                      activated=self.toggle_editor),
            QShortcut(QKeySequence.StandardKey.Save, self, activated=self._save),
            QShortcut(QKeySequence("Ctrl+Space"), self, activated=self._toggle_transport),
        ]
        self.capture_shortcut = QShortcut(QKeySequence("Ctrl+Shift+S"), self, activated=self._capture_take)
        self.replay_shortcut = QShortcut(QKeySequence("Ctrl+Shift+P"), self, activated=self._replay_take)
        self.shortcuts.extend((self.capture_shortcut, self.replay_shortcut))
        self.grave_shortcut = QShortcut(QKeySequence("`"), self, activated=self.toggle_editor)
        self.grave_shortcut.setAutoRepeat(False)
        self.seed_shortcut = QShortcut(QKeySequence("Ctrl+R"), self, activated=self._focus_seed)
        self.shortcuts.extend((self.grave_shortcut, self.seed_shortcut))
        self.profile_shortcuts = []
        self.shortcut_bindings = None
        QApplication.instance().focusChanged.connect(self._enable_profile_shortcuts)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(20)
        self._update_panel()
        worker.thread.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "overlay"):
            self.overlay.setGeometry(0, 0, self.panel.width(), int(self.panel.height() * .82))

    def toggle_editor(self):
        if self.state.replaying:
            return
        if self.overlay.isVisible():
            self.overlay.hide()
            self._update_panel()
            if self.previous_focus:
                self.previous_focus.setFocus()
        else:
            self.previous_focus = self.focusWidget()
            self.overlay.setGeometry(0, 0, self.panel.width(), int(self.panel.height() * .82))
            self.overlay.show()
            self.overlay.raise_()
            self._update_panel()
            self.editor.setFocus()

    @property
    def dirty(self):
        return self.editor.toPlainText() != self.state.documents[self.kind].text

    @property
    def busy(self):
        return self.future is not None or self.pending is not None

    def _update_dirty(self):
        suffix = " • Unsaved changes" if self.dirty else ""
        self.path.setText(str(self.state.documents[self.kind].path) + suffix)

    def _show_document(self):
        self.editor.setPlainText(self.state.documents[self.kind].text)
        self.diagnostic.clear()
        self._update_dirty()

    def _discard_allowed(self):
        return not self.dirty or QMessageBox.question(
            self, "Discard edits?", "Discard the unsaved changes in this buffer?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) == QMessageBox.StandardButton.Discard

    def _switch_document(self, kind):
        if kind == self.kind:
            return
        if not self._discard_allowed():
            self.files.blockSignals(True)
            self.files.setCurrentText(self.kind)
            self.files.blockSignals(False)
            return
        self.kind = kind
        self._show_document()

    def _update_panel(self):
        self.project_name.setText(f"Loop composition · {self.state.documents['project'].path.name}")
        self.transport_status.setText("Replaying" if self.state.replaying else
                                      "Playing" if self.state.running else "Stopped")
        self.audio_diagnostics.setText(self.state.diagnostics)
        enabled = not self.busy and not self.closing
        panel_enabled = enabled and not self.overlay.isVisible()
        self.start_button.setEnabled(panel_enabled and not self.state.running)
        self.stop_button.setEnabled(panel_enabled and self.state.running)
        self.editor_button.setEnabled(panel_enabled and not self.state.replaying)
        self.take_path.setEnabled(panel_enabled and not self.state.running)
        self.capture_button.setEnabled(panel_enabled and not self.state.running and self.state.can_save)
        self.replay_button.setEnabled(panel_enabled and not self.state.running)
        self.capture_shortcut.setEnabled(self.capture_button.isEnabled())
        self.replay_shortcut.setEnabled(self.replay_button.isEnabled())
        self.files.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        self.reload_button.setEnabled(enabled)
        self.editor.setReadOnly(not enabled)
        self.profile_key.setEnabled(panel_enabled and not self.state.replaying)
        self._refresh_profile_shortcuts()
        values = self.state.project.snapshot()
        seed = self.state.project.seed
        active = self.state.active_seed
        if self.state.replaying:
            status = "Using the recorded notes and controls"
        elif self.state.running and active != seed:
            status = f"Active seed {active} · Pending seed {seed}" if active is not None else f"Pending seed {seed}"
        elif self.state.running:
            status = f"Active seed {seed}"
        else:
            status = f"Seed {seed} for next start"
        self.seed_status.setText(status)
        for identifier, field in self.fields.items():
            field.setEnabled(panel_enabled and not self.state.replaying)
            if not (field.hasFocus() and field.isModified()):
                field.setText(str(values[identifier]))

    def _refresh_profile_shortcuts(self):
        bindings = self.state.profile.bindings
        if bindings != self.shortcut_bindings:
            for shortcut in self.profile_shortcuts:
                shortcut.setEnabled(False)
                shortcut.deleteLater()
            self.profile_shortcuts.clear()
            self.shortcut_bindings = bindings
            reserved = {key.toString() for shortcut in self.shortcuts for key in shortcut.keys()}
            keys = {}
            for token, _ in bindings:
                sequence = QKeySequence.fromString(token, QKeySequence.SequenceFormat.PortableText)
                if sequence.count() == 1 and sequence[0].key() != Qt.Key.Key_unknown:
                    keys.setdefault(sequence.toString(), []).append(token)
            # Qt may report a literal plus with Shift still set.
            if "+" in keys:
                keys.setdefault("Shift++", keys["+"])
            for canonical, tokens in keys.items():
                if canonical and canonical not in reserved and len(tokens) == 1:
                    shortcut = QShortcut(QKeySequence(canonical), self,
                                         activated=lambda key=tokens[0]: self._send_profile_key(key))
                    shortcut.setAutoRepeat(False)
                    self.profile_shortcuts.append(shortcut)
        self._enable_profile_shortcuts()

    def _enable_profile_shortcuts(self, *_):
        enabled = (not self.busy and not self.closing and not self.state.replaying
                   and not self.overlay.isVisible()
                   and QApplication.activeModalWidget() is None
                   and not isinstance(QApplication.focusWidget(), QLineEdit))
        self.grave_shortcut.setEnabled(enabled)
        self.seed_shortcut.setEnabled(enabled)
        for shortcut in self.profile_shortcuts:
            shortcut.setEnabled(enabled)

    def _focus_seed(self):
        field = self.fields["generation.seed"]
        field.setFocus()
        field.selectAll()

    def _send_profile_key(self, key):
        if self.state.replaying:
            return
        self._submit("profile key", lambda model: model.transport.control(f"key {shlex.quote(key)}"))

    def _submit(self, operation, command):
        if self.busy or self.closing:
            return
        self.pending = operation
        self.worker.commands.put_nowait(command)
        self._update_panel()

    def _control(self, identifier):
        value = self.fields[identifier].text()
        def apply(model):
            model.transport.control(f"set {identifier} {value}")
            if identifier == "generation.seed" and model.transport.running:
                return "Seed change queued for the next unscheduled onset"
            return f"{PARAMETERS[identifier].label} applied"
        self._submit("control", apply)

    def _profile_control(self):
        key = self.profile_key.text().strip()
        if key:
            self._send_profile_key(key)
            self.profile_key.clear()

    def _start(self):
        def start(model):
            model.transport.start()
            model.playback = None
            return "Started"
        self._submit("start", start)

    def _stop(self):
        def stop(model):
            if model.playback is not None and model.playback.running:
                model.playback.stop()
            else:
                model.transport.stop()
            return "Stopped"
        self._submit("stop", stop)

    def _toggle_transport(self):
        self._stop() if self.state.running else self._start()

    def _capture_take(self):
        name = self.take_path.text().strip()
        if not name:
            self.status.setText("Enter a path for the take")
            return
        def capture(model):
            path = Path(name).expanduser()
            save_take(path, model.transport.recording())
            return f"Saved take to {path}"

        self._submit("capture take", capture)

    def _replay_take(self):
        name = self.take_path.text().strip()
        if not name:
            self.status.setText("Enter the path to a recorded take")
            return
        self.file_operation = "replay"
        self.future = self.file_jobs.submit(load_take, Path(name))
        self._update_panel()

    def _save(self):
        if not self.overlay.isVisible() or self.busy or self.closing:
            return
        self.diagnostic.setText("Validating…")
        self.future = self.file_jobs.submit(stage_edit, self.state.documents[self.kind], self.editor.toPlainText())
        self._update_panel()

    def _reload(self):
        if self.busy or not self._discard_allowed():
            return
        document = self.state.documents[self.kind]

        def reload(model):
            model.documents[document.kind] = Document.read(document.kind, document.path)
            return "Reloaded from disk. Ctrl+S applies the file to the session."

        self._submit("reload", reload)

    def _error(self, message, location=None):
        if location:
            line, column = location
            message = f"Line {line}, column {column} · {message}"
            block = self.editor.document().findBlockByNumber(line - 1)
            if block.isValid():
                cursor = self.editor.textCursor()
                offset = len(block.text()[:column - 1].encode("utf-16-le")) // 2
                cursor.setPosition(block.position() + offset)
                self.editor.setTextCursor(cursor)
                self.editor.centerCursor()
        self.diagnostic.setText(message)
        self.status.setText(message)

    def _poll(self):
        if self.future is not None and self.future.done():
            future, self.future = self.future, None
            operation, self.file_operation = self.file_operation, None
            try:
                edit = future.result()
            except (OSError, ValueError, RuntimeError) as exc:
                if operation == "replay":
                    self.status.setText(str(exc))
                else:
                    self._error(str(exc), getattr(exc, "location", None))
                self._update_panel()
            else:
                if operation == "replay":
                    def replay(model):
                        model.playback = play_take(model.transport, edit)
                        return "Replaying recorded notes and controls"
                    self._submit("replay", replay)
                else:
                    self._submit("save", lambda model: model.apply(edit))
        while True:
            try:
                result = self.worker.results.get_nowait()
            except Empty:
                break
            self.state = result
            self.status.setText(result.message if len(result.message) <= 500 else result.message[:497] + "…")
            if result.response:
                operation, self.pending = self.pending, None
                if operation == "profile key" and not result.error and result.message == "quit":
                    self.close()
                if operation in ("save", "reload"):
                    if result.error:
                        self._error(result.message, result.location)
                    else:
                        self._show_document()
                        self.diagnostic.setText(result.message)
            self._update_panel()
        if self.closing and not self.worker.thread.is_alive():
            self.close()

    def closeEvent(self, event):
        if self.closing and not self.worker.thread.is_alive():
            self.file_jobs.shutdown(wait=False)
            self.timer.stop()
            event.accept()
            return
        event.ignore()
        if self.busy:
            self.status.setText("An operation is in progress")
            return
        if not self.closing and self._discard_allowed():
            self.closing = True
            self.worker.closing.set()
            self._update_panel()


def launch(args) -> int:
    documents = {"project": Document.read("project", args.project)}
    for kind in ("profile", "modules"):
        path = getattr(args, kind)
        if path is not None:
            documents[kind] = Document.read(kind, path)

    def factory(project, profile, module_config):
        return live_transport(project, profile, backend=args.backend, exclusive=args.exclusive_server,
                              port=args.port, sample_rate=args.sample_rate, sclang=args.sclang,
                              library=args.library, module_config=module_config)

    worker = PanelWorker(EditorSession(documents, factory))
    app = QApplication.instance() or QApplication(["DINAD"])
    window = Window(worker)
    window.show()
    return app.exec()
