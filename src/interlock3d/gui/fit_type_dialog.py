"""Small modal dialog to pick a fit type when confirming a feature pair."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from interlock3d.core.pairing import FIT_TYPE_LABELS, FitType


class FitTypeDialog(QDialog):
    def __init__(self, parent=None, initial: FitType = "sliding") -> None:
        super().__init__(parent)
        self.setWindowTitle("Confirm Feature Pair")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Fit type for this pair:"))

        self._fit_types: list[FitType] = list(FIT_TYPE_LABELS.keys())
        self._combo = QComboBox(self)
        for key in self._fit_types:
            self._combo.addItem(FIT_TYPE_LABELS[key], userData=key)
        self._combo.setCurrentIndex(self._fit_types.index(initial))
        layout.addWidget(self._combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_fit_type(self) -> FitType:
        return self._combo.currentData()
