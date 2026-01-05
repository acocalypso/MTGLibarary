from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


@dataclass(frozen=True)
class ImportMapping:
    quantity: str | None
    name: str | None
    set_code: str | None
    collector_number: str | None
    scryfall_id: str | None


@dataclass(frozen=True)
class ImportPlan:
    mode: str  # "csv" or "decklist"
    mapping: ImportMapping


def _guess_mode(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".csv", ".tsv"}:
        return "csv"

    # Heuristic: if many non-empty lines start with a number, it's decklist-like.
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return "csv"

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "csv"

    sample = lines[:50]
    qty_like = 0
    for ln in sample:
        head = ln.split(" ", 1)[0]
        if head.isdigit():
            qty_like += 1
    return "decklist" if qty_like >= max(3, len(sample) // 3) else "csv"


class ImportMapperDialog(QDialog):
    def __init__(self, *, parent, path: Path):
        super().__init__(parent)
        self._path = path
        self.setWindowTitle("Import file")
        self.resize(900, 520)

        self._mode = _guess_mode(path)

        root = QVBoxLayout(self)
        root.addWidget(QLabel(f"File: {path.name}"))

        self._preview = QTableWidget()
        self._preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._preview.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        root.addWidget(self._preview, 1)

        form = QFormLayout()
        self._cmb_qty = QComboBox()
        self._cmb_name = QComboBox()
        self._cmb_set = QComboBox()
        self._cmb_collector = QComboBox()
        self._cmb_scryfall = QComboBox()

        form.addRow("Quantity", self._cmb_qty)
        form.addRow("Name", self._cmb_name)
        form.addRow("Set code", self._cmb_set)
        form.addRow("Collector number", self._cmb_collector)
        form.addRow("Scryfall ID", self._cmb_scryfall)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._columns: list[str] = []
        self._load_preview()
        self._init_mapping_defaults()

    def plan(self) -> ImportPlan:
        return ImportPlan(
            mode=self._mode,
            mapping=ImportMapping(
                quantity=_none_if_empty(self._cmb_qty.currentText()),
                name=_none_if_empty(self._cmb_name.currentText()),
                set_code=_none_if_empty(self._cmb_set.currentText()),
                collector_number=_none_if_empty(self._cmb_collector.currentText()),
                scryfall_id=_none_if_empty(self._cmb_scryfall.currentText()),
            ),
        )

    def _load_preview(self) -> None:
        if self._mode == "decklist":
            from mtg_cards.deck import parse_decklist

            text = self._path.read_text(encoding="utf-8", errors="ignore")
            entries = parse_decklist(text)
            rows = entries[:50]
            self._columns = ["qty", "name", "set", "collector"]

            self._preview.setColumnCount(len(self._columns))
            self._preview.setHorizontalHeaderLabels(self._columns)
            self._preview.setRowCount(len(rows))
            for r, e in enumerate(rows):
                self._preview.setItem(r, 0, QTableWidgetItem(str(e.qty)))
                self._preview.setItem(r, 1, QTableWidgetItem(str(e.name)))
                self._preview.setItem(r, 2, QTableWidgetItem(str(e.set_code or "")))
                self._preview.setItem(r, 3, QTableWidgetItem(str(e.collector_number or "")))
        else:
            # CSV/TSV/"best effort" delimited
            with self._path.open("r", encoding="utf-8-sig", newline="") as f:
                sample = f.read(32 * 1024)
                f.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=[",", "\t", ";", "|"])
                except Exception:
                    dialect = csv.excel

                reader = csv.DictReader(f, dialect=dialect)
                self._columns = list(reader.fieldnames or [])

                rows: list[dict[str, str]] = []
                for i, row in enumerate(reader):
                    if i >= 50:
                        break
                    rows.append({k: ("" if v is None else str(v)) for k, v in row.items()})

            if not self._columns:
                # Fallback: show raw lines.
                self._mode = "decklist"
                self._load_preview()
                return

            self._preview.setColumnCount(len(self._columns))
            self._preview.setHorizontalHeaderLabels(self._columns)
            self._preview.setRowCount(len(rows))
            for r, row in enumerate(rows):
                for c, col in enumerate(self._columns):
                    self._preview.setItem(r, c, QTableWidgetItem(row.get(col, "")))

        self._preview.resizeColumnsToContents()

        # Populate comboboxes
        opts = ["(none)"] + self._columns
        for cmb in [self._cmb_qty, self._cmb_name, self._cmb_set, self._cmb_collector, self._cmb_scryfall]:
            cmb.clear()
            cmb.addItems(opts)

    def _init_mapping_defaults(self) -> None:
        if self._mode == "decklist":
            _select(self._cmb_qty, "qty")
            _select(self._cmb_name, "name")
            _select(self._cmb_set, "set")
            _select(self._cmb_collector, "collector")
            _select(self._cmb_scryfall, "(none)")
            return

        # CSV defaults by common headers
        _select_any(self._cmb_qty, ["Quantity", "Qty", "Count", "Owned"])
        _select_any(self._cmb_name, ["Name", "Card", "Card Name", "card_name"])
        _select_any(self._cmb_set, ["Set code", "Set", "set_code"])
        _select_any(self._cmb_collector, ["Collector number", "collector_number", "Collector", "Number", "CN"])
        _select_any(self._cmb_scryfall, ["Scryfall ID", "scryfall_id", "Scryfall UUID"])


def _select(combo: QComboBox, text: str) -> None:
    idx = combo.findText(text, Qt.MatchFlag.MatchFixedString)
    if idx >= 0:
        combo.setCurrentIndex(idx)


def _select_any(combo: QComboBox, candidates: Iterable[str]) -> None:
    for cand in candidates:
        idx = combo.findText(cand, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            combo.setCurrentIndex(idx)
            return

    # Try case-insensitive
    for cand in candidates:
        for i in range(combo.count()):
            if combo.itemText(i).casefold() == cand.casefold():
                combo.setCurrentIndex(i)
                return


def _none_if_empty(text: str) -> str | None:
    text = (text or "").strip()
    if not text or text == "(none)":
        return None
    return text
