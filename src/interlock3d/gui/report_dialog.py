"""Read-only dialog showing the human-readable compensation report after
an export."""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextEdit, QVBoxLayout


class ReportDialog(QDialog):
    def __init__(self, parent, report_text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Compensation Report")
        self.resize(600, 400)

        layout = QVBoxLayout(self)

        text_edit = QTextEdit(self)
        text_edit.setReadOnly(True)
        text_edit.setFont(QFont("monospace"))
        text_edit.setPlainText(report_text)
        layout.addWidget(text_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok, self)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
