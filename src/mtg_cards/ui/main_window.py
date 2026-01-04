from __future__ import annotations

import json
import sys
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QThreadPool, Qt, QTimer, QStringListModel
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QFileDialog,
    QCompleter,
    QInputDialog,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from mtg_cards import __version__
from mtg_cards.db import connect, init_db, json_loads
from mtg_cards.decks import create_deck, delete_deck, get_deck, list_decks, update_deck
from mtg_cards.deck import parse_decklist, validate_deck
from mtg_cards.images import fetch_image_to_cache
from mtg_cards.paths import db_path, imports_dirs
from mtg_cards.startup import check_app_update, ensure_scryfall_up_to_date, import_all_csvs, scryfall_update_available
from mtg_cards.workers import Worker, WorkerRequest


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"MTG Cards v{__version__}")
        self.resize(1200, 800)

        self._build_menus()

        self._pool = QThreadPool.globalInstance()
        self._conn = connect(db_path())
        init_db(self._conn)

        # Deck search/autocomplete state must be initialized before building tabs,
        # because the Decks tab wires completers during construction.
        self._deck_search_debounce = QTimer(self)
        self._deck_search_debounce.setSingleShot(True)
        self._deck_search_debounce.setInterval(200)
        self._deck_search_debounce.timeout.connect(self._update_deck_name_completer)

        self._deck_name_model = QStringListModel(self)
        self._deck_name_completer = QCompleter(self._deck_name_model, self)
        self._deck_name_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._deck_name_completer.setFilterMode(Qt.MatchFlag.MatchContains)

        self._progress: QProgressDialog | None = None
        self._image_progress: QProgressDialog | None = None

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_collection_tab(), "Collection")
        self._tabs.addTab(self._build_stats_tab(), "Stats")
        self._tab_index_decks = self._tabs.addTab(self._build_decks_tab(), "Decks")
        self._tab_index_validator = self._tabs.addTab(self._build_deck_tab(), "Deck Validator")
        self.setCentralWidget(self._tabs)
        self.setStatusBar(QStatusBar(self))

        self.refresh_stats()

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self.refresh_search)

        self._startup_has_run = False
        self._startup_needs_initial_scryfall = (
            self._conn.execute("SELECT COUNT(*) FROM printings").fetchone()[0] == 0
        )
        self._startup_begin()

        # Used to decide whether to show a detailed import-complete popup.
        self._csv_import_show_summary = False

        # Keep a reference to short-lived workers (prevents premature GC).
        self._deck_worker: Worker | None = None
        self._workers_keepalive: list[Worker] = []

        self._active_deck_id: int | None = None

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("File")

        import_action = file_menu.addAction("Import CSV…")
        import_action.setShortcut("Ctrl+I")
        import_action.triggered.connect(self._import_csv_via_dialog)

        file_menu.addSeparator()
        exit_action = file_menu.addAction("Exit")
        exit_action.setShortcut("Alt+F4")
        exit_action.triggered.connect(self.close)

    # -------------------------
    # UI: Collection
    # -------------------------
    def _build_collection_tab(self) -> QWidget:
        root = QWidget()
        layout = QHBoxLayout(root)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # Left: search + list
        left = QWidget()
        left_layout = QVBoxLayout(left)

        form = QFormLayout()
        self._q_name = QLineEdit()
        self._q_type = QLineEdit()
        self._q_set = QLineEdit()

        self._q_color = QComboBox()
        self._q_color.addItems(["Any", "W", "U", "B", "R", "G", "C"])

        form.addRow("Name", self._q_name)
        form.addRow("Type", self._q_type)
        form.addRow("Color", self._q_color)
        form.addRow("Set", self._q_set)
        left_layout.addLayout(form)

        btn_row = QHBoxLayout()
        self._btn_search = QPushButton("Search")
        self._btn_import = QPushButton("Import CSVs")
        self._btn_refresh_scry = QPushButton("Update Scryfall")
        btn_row.addWidget(self._btn_search)
        btn_row.addWidget(self._btn_import)
        btn_row.addWidget(self._btn_refresh_scry)
        left_layout.addLayout(btn_row)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Card", "Set", "Owned"])
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        left_layout.addWidget(self._table, 1)

        splitter.addWidget(left)

        # Right: details
        right = QWidget()
        right_layout = QVBoxLayout(right)

        self._img = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self._img.setMinimumHeight(340)
        right_layout.addWidget(self._img)

        self._detail = QTextEdit()
        self._detail.setReadOnly(True)
        right_layout.addWidget(self._detail, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        # Signals
        self._btn_search.clicked.connect(self.refresh_search)
        self._btn_import.clicked.connect(lambda: self._run_csv_import(show_summary=True))
        self._btn_refresh_scry.clicked.connect(lambda: self._run_scryfall_sync(show_dialog=True))

        self._q_name.textChanged.connect(lambda: self._debounce.start())
        self._q_type.textChanged.connect(lambda: self._debounce.start())
        self._q_set.textChanged.connect(lambda: self._debounce.start())
        self._q_color.currentIndexChanged.connect(lambda: self._debounce.start())

        self._table.itemSelectionChanged.connect(self._on_select_row)

        return root

    # -------------------------
    # UI: Stats
    # -------------------------
    def _build_stats_tab(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)

        top = QHBoxLayout()
        self._stats_summary = QLabel("", wordWrap=True)
        self._btn_refresh_stats = QPushButton("Refresh")
        top.addWidget(self._stats_summary, 1)
        top.addWidget(self._btn_refresh_stats)
        layout.addLayout(top)

        self._stats_table = QTableWidget(0, 3)
        self._stats_table.setHorizontalHeaderLabels(["Set", "Distinct printings", "Total copies"])
        self._stats_table.verticalHeader().setVisible(False)
        self._stats_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._stats_table, 1)

        self._btn_refresh_stats.clicked.connect(self.refresh_stats)
        return root

    def refresh_stats(self) -> None:
        if self._conn.execute("SELECT COUNT(*) FROM printings").fetchone()[0] == 0:
            self._stats_table.setRowCount(0)
            self._stats_summary.setText("Stats unavailable until Scryfall import completes.")
            return

        rows = self._conn.execute(
            """
            SELECT p.set_code AS set_code,
                   COUNT(*) AS distinct_printings,
                   SUM(op.quantity) AS total_copies
            FROM owned_printings op
            JOIN printings p ON p.scryfall_id = op.scryfall_id
            GROUP BY p.set_code
            ORDER BY total_copies DESC, p.set_code ASC
            """
        ).fetchall()

        self._stats_table.setRowCount(len(rows))
        total_sets = len(rows)
        total_copies = 0
        total_distinct = 0

        for i, r in enumerate(rows):
            set_code = str(r["set_code"]).upper()
            distinct = int(r["distinct_printings"]) if r["distinct_printings"] is not None else 0
            copies = int(r["total_copies"]) if r["total_copies"] is not None else 0
            total_copies += copies
            total_distinct += distinct

            self._stats_table.setItem(i, 0, QTableWidgetItem(set_code))
            self._stats_table.setItem(i, 1, QTableWidgetItem(str(distinct)))
            self._stats_table.setItem(i, 2, QTableWidgetItem(str(copies)))

        self._stats_summary.setText(
            f"Sets: {total_sets}   Distinct printings: {total_distinct}   Total copies: {total_copies}"
        )

    def refresh_search(self) -> None:
        name = self._q_name.text().strip()
        type_text = self._q_type.text().strip()
        set_code = self._q_set.text().strip().lower()
        color = self._q_color.currentText().strip()

        where = ["(p.lang IS NULL OR p.lang = 'en')", "p.is_token = 0", "p.is_digital = 0"]
        params: list[object] = []

        if name:
            where.append("p.name LIKE ?")
            params.append(f"%{name}%")
        if type_text:
            where.append("p.type_line LIKE ?")
            params.append(f"%{type_text}%")
        if set_code:
            where.append("lower(p.set_code) LIKE ?")
            params.append(f"%{set_code}%")
        if color and color != "Any":
            if color == "C":
                where.append("(p.colors IS NULL OR p.colors = 'null' OR p.colors = '[]')")
            else:
                where.append("p.colors LIKE ?")
                params.append(f"%\"{color}\"%")

        sql = (
            "SELECT p.scryfall_id, p.name, p.set_code, COALESCE(op.quantity, 0) AS qty "
            "FROM printings p "
            "LEFT JOIN owned_printings op ON op.scryfall_id = p.scryfall_id "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY qty DESC, p.name ASC "
            "LIMIT 500"
        )

        rows = self._conn.execute(sql, params).fetchall()
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            item0 = QTableWidgetItem(str(r["name"]))
            item0.setData(Qt.ItemDataRole.UserRole, str(r["scryfall_id"]))
            self._table.setItem(i, 0, item0)
            self._table.setItem(i, 1, QTableWidgetItem(str(r["set_code"]).upper()))
            self._table.setItem(i, 2, QTableWidgetItem(str(r["qty"])))

        self.statusBar().showMessage(f"Found {len(rows)} cards (showing up to 500)")

        if len(rows) > 0:
            self._table.selectRow(0)
        else:
            self._img.clear()
            self._detail.clear()

    def _on_select_row(self) -> None:
        items = self._table.selectedItems()
        if not items:
            return
        scryfall_id = items[0].data(Qt.ItemDataRole.UserRole)
        if not scryfall_id:
            return

        card = self._conn.execute(
            "SELECT * FROM printings WHERE scryfall_id = ?", (str(scryfall_id),)
        ).fetchone()
        if card is None:
            return

        image_url = _best_image_url(card)
        owned = self._conn.execute(
            "SELECT COALESCE(quantity, 0) FROM owned_printings WHERE scryfall_id = ?",
            (str(scryfall_id),),
        ).fetchone()
        owned_qty = int(owned[0]) if owned else 0

        detail_lines = [
            f"{card['name']}  ({card['set_code'].upper()} {card['collector_number']})",
            f"Owned: {owned_qty}",
            "",
            card["mana_cost"] or "",
            card["type_line"] or "",
            f"Rarity: {card['rarity'] or ''}",
            "",
            card["oracle_text"] or "",
        ]
        self._detail.setPlainText("\n".join([l for l in detail_lines if l is not None]))

        if image_url:
            self._load_image_async(image_url)
        else:
            self._img.setText("No image")

    def _load_image_async(self, url: str) -> None:
        self._img.setText("Loading image…")

        req = WorkerRequest(fn=_fetch_image_worker, kwargs={"url": url})
        w = Worker(req)
        w.signals.failed.connect(lambda e: self._img.setText("Image load failed"))
        w.signals.finished.connect(self._on_image_ready)
        self._pool.start(w)

    def _on_image_ready(self, path: object) -> None:
        if not isinstance(path, (str, Path)):
            self._img.setText("Image load failed")
            return
        p = Path(path)
        if not p.exists():
            self._img.setText("Image not found")
            return
        pix = QPixmap(str(p))
        if pix.isNull():
            self._img.setText("Image decode failed")
            return
        self._img.setPixmap(pix.scaledToHeight(520, Qt.TransformationMode.SmoothTransformation))

    # -------------------------
    # UI: Deck validation
    # -------------------------
    def _build_deck_tab(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)

        self._deck_in = QTextEdit()
        self._deck_in.setPlaceholderText("Paste deck list here…")
        layout.addWidget(self._deck_in, 2)

        row = QHBoxLayout()
        self._btn_validate = QPushButton("Validate")
        self._btn_save_deck = QPushButton("Save as Deck…")
        self._deck_summary = QLabel("", wordWrap=True)
        row.addWidget(self._btn_validate)
        row.addWidget(self._btn_save_deck)
        row.addWidget(self._deck_summary, 1)
        layout.addLayout(row)

        self._deck_table = QTableWidget(0, 5)
        self._deck_table.setHorizontalHeaderLabels(["Card", "Set", "Collector", "Requested", "Owned"])
        self._deck_table.verticalHeader().setVisible(False)
        self._deck_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._deck_table, 2)

        self._btn_validate.clicked.connect(self._run_deck_validation)
        self._btn_save_deck.clicked.connect(self._save_validator_as_deck)
        return root

    def _save_validator_as_deck(self) -> None:
        deck_text = self._deck_in.toPlainText()
        if not deck_text.strip():
            QMessageBox.information(self, "Save deck", "Paste a deck list first.")
            return

        name, ok = QInputDialog.getText(self, "Save deck", "Deck name:")
        if not ok:
            return

        deck_id = create_deck(self._conn, name=name.strip() or "New Deck", deck_text=deck_text)
        self.statusBar().showMessage("Deck saved")

        # Refresh Decks tab and select the newly created deck.
        self._refresh_decks_list()
        for r in range(self._decks_table.rowCount()):
            it = self._decks_table.item(r, 0)
            if it is not None and int(it.data(Qt.ItemDataRole.UserRole)) == deck_id:
                self._decks_table.selectRow(r)
                break

        # Jump to Decks tab so the user can edit further.
        self._tabs.setCurrentIndex(self._tab_index_decks)

    # -------------------------
    # UI: Decks
    # -------------------------
    def _build_decks_tab(self) -> QWidget:
        root = QWidget()
        layout = QHBoxLayout(root)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # Left: deck list
        left = QWidget()
        left_layout = QVBoxLayout(left)

        btns = QHBoxLayout()
        self._btn_deck_new = QPushButton("New")
        self._btn_deck_delete = QPushButton("Delete")
        btns.addWidget(self._btn_deck_new)
        btns.addWidget(self._btn_deck_delete)
        btns.addStretch(1)
        left_layout.addLayout(btns)

        self._decks_table = QTableWidget(0, 2)
        self._decks_table.setHorizontalHeaderLabels(["Name", "Updated"])
        self._decks_table.verticalHeader().setVisible(False)
        self._decks_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._decks_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._decks_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        left_layout.addWidget(self._decks_table, 1)

        splitter.addWidget(left)

        # Right: editor + validation view
        right = QWidget()
        right_layout = QVBoxLayout(right)

        form = QFormLayout()
        self._deck_name = QLineEdit()
        form.addRow("Name", self._deck_name)
        right_layout.addLayout(form)

        self._deck_edit = QTextEdit()
        self._deck_edit.setPlaceholderText("Paste / edit deck list here…")
        right_layout.addWidget(self._deck_edit, 2)

        # Card search / add/remove
        search_form = QFormLayout()
        self._deck_search_name = QLineEdit()
        self._deck_search_name.setPlaceholderText("Card name…")
        self._deck_search_name.setCompleter(self._deck_name_completer)
        self._deck_search_type = QLineEdit()
        self._deck_search_type.setPlaceholderText("Type (creature, instant, sorcery…)…")
        self._deck_search_set = QLineEdit()
        self._deck_search_set.setPlaceholderText("Set (optional)…")
        self._deck_search_color = QComboBox()
        self._deck_search_color.addItems(["Any", "W", "U", "B", "R", "G", "C"])
        search_form.addRow("Name", self._deck_search_name)
        search_form.addRow("Type", self._deck_search_type)
        search_form.addRow("Color", self._deck_search_color)
        search_form.addRow("Set", self._deck_search_set)
        right_layout.addLayout(search_form)

        search_btns = QHBoxLayout()
        self._deck_qty = QSpinBox()
        self._deck_qty.setRange(1, 99)
        self._deck_qty.setValue(1)
        self._btn_deck_search = QPushButton("Search")
        self._btn_deck_add = QPushButton("Add")
        self._btn_deck_remove = QPushButton("Remove selected")
        search_btns.addWidget(QLabel("Qty"))
        search_btns.addWidget(self._deck_qty)
        search_btns.addWidget(self._btn_deck_search)
        search_btns.addWidget(self._btn_deck_add)
        search_btns.addWidget(self._btn_deck_remove)
        search_btns.addStretch(1)
        right_layout.addLayout(search_btns)

        self._deck_search_results = QTableWidget(0, 4)
        self._deck_search_results.setHorizontalHeaderLabels(["Card", "Set", "Collector", "Type"])
        self._deck_search_results.verticalHeader().setVisible(False)
        self._deck_search_results.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._deck_search_results.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._deck_search_results.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        right_layout.addWidget(self._deck_search_results, 1)

        row = QHBoxLayout()
        self._btn_deck_save = QPushButton("Save")
        self._btn_deck_validate = QPushButton("Validate")
        self._deck_edit_summary = QLabel("", wordWrap=True)
        row.addWidget(self._btn_deck_save)
        row.addWidget(self._btn_deck_validate)
        row.addWidget(self._deck_edit_summary, 1)
        right_layout.addLayout(row)

        self._deck_cards_table = QTableWidget(0, 6)
        self._deck_cards_table.setHorizontalHeaderLabels(["Card", "Set", "Collector", "Requested", "Owned", "Status"])
        self._deck_cards_table.verticalHeader().setVisible(False)
        self._deck_cards_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        right_layout.addWidget(self._deck_cards_table, 2)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        # Signals
        self._btn_deck_new.clicked.connect(self._decks_new)
        self._btn_deck_delete.clicked.connect(self._decks_delete)
        self._btn_deck_save.clicked.connect(self._decks_save)
        self._btn_deck_validate.clicked.connect(self._decks_validate)
        self._decks_table.itemSelectionChanged.connect(self._decks_selection_changed)

        self._btn_deck_search.clicked.connect(self._deck_search)
        self._btn_deck_add.clicked.connect(self._deck_add_selected)
        self._btn_deck_remove.clicked.connect(self._deck_remove_selected)
        self._deck_search_name.textChanged.connect(lambda: self._deck_search_debounce.start())

        self._refresh_decks_list()
        self._set_decks_editor_enabled(False)
        return root

    def _set_decks_editor_enabled(self, enabled: bool) -> None:
        self._deck_name.setEnabled(enabled)
        self._deck_edit.setEnabled(enabled)
        self._btn_deck_save.setEnabled(enabled)
        self._btn_deck_validate.setEnabled(enabled)
        self._btn_deck_delete.setEnabled(enabled and self._active_deck_id is not None)
        self._deck_search_name.setEnabled(enabled)
        self._deck_search_type.setEnabled(enabled)
        self._deck_search_set.setEnabled(enabled)
        self._deck_search_color.setEnabled(enabled)
        self._btn_deck_search.setEnabled(enabled)
        self._btn_deck_add.setEnabled(enabled)
        self._btn_deck_remove.setEnabled(enabled)
        self._deck_search_results.setEnabled(enabled)

    def _refresh_decks_list(self) -> None:
        decks = list_decks(self._conn)
        self._decks_table.setRowCount(len(decks))
        for i, d in enumerate(decks):
            it_name = QTableWidgetItem(d.name)
            it_name.setData(Qt.ItemDataRole.UserRole, d.id)
            self._decks_table.setItem(i, 0, it_name)
            self._decks_table.setItem(i, 1, QTableWidgetItem(d.updated_at))

    def _decks_selection_changed(self) -> None:
        items = self._decks_table.selectedItems()
        if not items:
            self._active_deck_id = None
            self._deck_name.setText("")
            self._deck_edit.setPlainText("")
            self._deck_cards_table.setRowCount(0)
            self._deck_edit_summary.setText("")
            self._set_decks_editor_enabled(False)
            return

        deck_id = items[0].data(Qt.ItemDataRole.UserRole)
        if deck_id is None:
            return
        self._active_deck_id = int(deck_id)
        d = get_deck(self._conn, self._active_deck_id)
        self._deck_name.setText(str(d.get("name") or ""))
        self._deck_edit.setPlainText(str(d.get("deck_text") or ""))
        self._set_decks_editor_enabled(True)
        self._deck_search_results.setRowCount(0)
        self._decks_validate()

    def _update_deck_name_completer(self) -> None:
        # Keep autocomplete lightweight: only suggest for 2+ chars.
        prefix = self._deck_search_name.text().strip()
        if len(prefix) < 2:
            self._deck_name_model.setStringList([])
            return

        rows = self._conn.execute(
            """
            SELECT DISTINCT name
            FROM printings
            WHERE name LIKE ? COLLATE NOCASE
              AND (lang IS NULL OR lang = 'en')
              AND is_token = 0 AND is_digital = 0
            ORDER BY name ASC
            LIMIT 50
            """,
            (f"%{prefix}%",),
        ).fetchall()
        self._deck_name_model.setStringList([str(r[0]) for r in rows])

    def _deck_search(self) -> None:
        if self._conn.execute("SELECT COUNT(*) FROM printings").fetchone()[0] == 0:
            return

        name = self._deck_search_name.text().strip()
        type_text = self._deck_search_type.text().strip()
        set_code = self._deck_search_set.text().strip().lower()
        color = self._deck_search_color.currentText().strip()

        where = ["(p.lang IS NULL OR p.lang = 'en')", "p.is_token = 0", "p.is_digital = 0"]
        params: list[object] = []

        if name:
            where.append("p.name LIKE ?")
            params.append(f"%{name}%")
        if type_text:
            where.append("p.type_line LIKE ?")
            params.append(f"%{type_text}%")
        if set_code:
            where.append("lower(p.set_code) LIKE ?")
            params.append(f"%{set_code}%")
        if color and color != "Any":
            if color == "C":
                where.append("(p.colors IS NULL OR p.colors = 'null' OR p.colors = '[]')")
            else:
                where.append("p.colors LIKE ?")
                params.append(f"%\"{color}\"%")

        sql = (
            "SELECT p.scryfall_id, p.name, p.set_code, p.collector_number, p.type_line "
            "FROM printings p "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY p.released_at DESC, p.name ASC "
            "LIMIT 200"
        )
        rows = self._conn.execute(sql, params).fetchall()
        self._deck_search_results.setRowCount(len(rows))
        for i, r in enumerate(rows):
            item0 = QTableWidgetItem(str(r["name"]))
            item0.setData(Qt.ItemDataRole.UserRole, str(r["scryfall_id"]))
            self._deck_search_results.setItem(i, 0, item0)
            self._deck_search_results.setItem(i, 1, QTableWidgetItem(str(r["set_code"]).upper()))
            self._deck_search_results.setItem(i, 2, QTableWidgetItem(str(r["collector_number"]) or ""))
            self._deck_search_results.setItem(i, 3, QTableWidgetItem(str(r["type_line"]) or ""))

        if len(rows) > 0:
            self._deck_search_results.selectRow(0)
        self.statusBar().showMessage(f"Found {len(rows)} cards (showing up to 200)")

    def _deck_add_selected(self) -> None:
        if self._active_deck_id is None:
            return
        items = self._deck_search_results.selectedItems()
        if not items:
            QMessageBox.information(self, "Add card", "Select a card from the search results first.")
            return

        row = items[0].row()
        name_item = self._deck_search_results.item(row, 0)
        set_item = self._deck_search_results.item(row, 1)
        collector_item = self._deck_search_results.item(row, 2)
        if name_item is None:
            return

        qty = int(self._deck_qty.value())
        name = name_item.text().strip()
        set_code = (set_item.text().strip() if set_item else "").upper() or None
        collector = (collector_item.text().strip() if collector_item else "") or None

        self._apply_deck_edit_delta(name=name, set_code=set_code, collector=collector, delta=qty)

    def _deck_remove_selected(self) -> None:
        if self._active_deck_id is None:
            return
        items = self._deck_cards_table.selectedItems()
        if not items:
            QMessageBox.information(self, "Remove card", "Select a card from the deck table first.")
            return

        row = items[0].row()
        name_item = self._deck_cards_table.item(row, 0)
        set_item = self._deck_cards_table.item(row, 1)
        collector_item = self._deck_cards_table.item(row, 2)
        if name_item is None:
            return

        qty = int(self._deck_qty.value())
        name = name_item.text().strip()
        set_code = (set_item.text().strip() if set_item else "").upper() or None
        collector = (collector_item.text().strip() if collector_item else "") or None

        self._apply_deck_edit_delta(name=name, set_code=set_code, collector=collector, delta=-qty)

    def _apply_deck_edit_delta(self, *, name: str, set_code: str | None, collector: str | None, delta: int) -> None:
        # Edit the deck by rewriting the deck list text (source of truth).
        text = self._deck_edit.toPlainText()
        entries = parse_decklist(text)

        key = (name.casefold(), (set_code or "").casefold(), collector or "")
        agg: dict[tuple[str, str | None, str | None], int] = {}
        for e in entries:
            k = (e.name.casefold(), (e.set_code or "").casefold(), e.collector_number or "")
            agg[(e.name, e.set_code, e.collector_number)] = agg.get((e.name, e.set_code, e.collector_number), 0) + int(e.qty)

        # Find best matching existing line (prefer exact set/collector if provided).
        target_keys = [
            (name, set_code, collector),
            (name, None, None),
        ]

        applied = False
        for tk in target_keys:
            if tk in agg:
                new_qty = agg[tk] + delta
                if new_qty <= 0:
                    del agg[tk]
                else:
                    agg[tk] = new_qty
                applied = True
                break

        if not applied and delta > 0:
            agg[(name, set_code, collector)] = delta

        # Rewrite deck text.
        def fmt(n: str, s: str | None, c: str | None, q: int) -> str:
            if s and c:
                return f"{q} {n} ({s}) {c}"
            return f"{q} {n}"

        lines = [fmt(n, s, c, q) for (n, s, c), q in sorted(agg.items(), key=lambda x: (x[0][0].casefold(), (x[0][1] or ""), (x[0][2] or "")))]
        self._deck_edit.setPlainText("\n".join(lines))
        self._decks_validate()

    def _decks_new(self) -> None:
        name, ok = QInputDialog.getText(self, "New deck", "Deck name:")
        if not ok:
            return
        deck_id = create_deck(self._conn, name=name.strip() or "New Deck", deck_text="")
        self._refresh_decks_list()
        # Select the new deck
        for r in range(self._decks_table.rowCount()):
            it = self._decks_table.item(r, 0)
            if it is not None and int(it.data(Qt.ItemDataRole.UserRole)) == deck_id:
                self._decks_table.selectRow(r)
                break

    def _decks_delete(self) -> None:
        if self._active_deck_id is None:
            return
        resp = QMessageBox.question(
            self,
            "Delete deck",
            "Delete this deck?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        delete_deck(self._conn, deck_id=self._active_deck_id)
        self._active_deck_id = None
        self._refresh_decks_list()
        self._decks_table.clearSelection()

    def _decks_save(self) -> None:
        if self._active_deck_id is None:
            return
        update_deck(
            self._conn,
            deck_id=self._active_deck_id,
            name=self._deck_name.text(),
            deck_text=self._deck_edit.toPlainText(),
        )
        self.statusBar().showMessage("Deck saved")
        self._refresh_decks_list()

    def _decks_validate(self) -> None:
        if self._active_deck_id is None:
            return
        if self._conn.execute("SELECT COUNT(*) FROM printings").fetchone()[0] == 0:
            self._deck_edit_summary.setText("Scryfall data not ready")
            return
        deck_text = self._deck_edit.toPlainText()
        if not deck_text.strip():
            self._deck_cards_table.setRowCount(0)
            self._deck_edit_summary.setText("Empty deck")
            return

        self._btn_deck_validate.setEnabled(False)
        self._deck_edit_summary.setText("Validating…")
        self._start_deck_validation(deck_text, target="decks")

    def _start_deck_validation(self, deck_text: str, *, target: str) -> None:
        req = WorkerRequest(fn=_deck_validate_worker, kwargs={"db_file": str(db_path()), "deck_text": deck_text})
        w = Worker(req)

        def _cleanup() -> None:
            try:
                self._workers_keepalive.remove(w)
            except ValueError:
                pass

        def _done(rows: object) -> None:
            _cleanup()
            if target == "validator":
                self._on_deck_validation_done(rows)
            else:
                self._on_decks_validation_done(rows)

        def _failed(err: str) -> None:
            _cleanup()
            if target == "validator":
                self._on_deck_validation_failed(err)
            else:
                self._on_decks_validation_failed(err)

        w.signals.status.connect(self.statusBar().showMessage)
        w.signals.finished.connect(_done)
        w.signals.failed.connect(_failed)

        self._workers_keepalive.append(w)
        self._pool.start(w)

    def _run_deck_validation(self) -> None:
        # Run validation off the UI thread so large decks don't freeze the app.
        if self._conn.execute("SELECT COUNT(*) FROM printings").fetchone()[0] == 0:
            QMessageBox.information(
                self,
                "Deck Validator",
                "Scryfall card data is not ready yet. Please finish the Scryfall download/import first.",
            )
            return

        deck_text = self._deck_in.toPlainText()
        if not deck_text.strip():
            self._deck_table.setRowCount(0)
            self._deck_summary.setText("Paste a deck list to validate")
            return

        self._btn_validate.setEnabled(False)
        self.statusBar().showMessage("Validating deck…")

        self._start_deck_validation(deck_text, target="validator")

    def _on_deck_validation_failed(self, err: str) -> None:
        print("[deck-ui] validation failed signal received", file=sys.stderr)
        self._btn_validate.setEnabled(True)
        self._deck_worker = None
        self.statusBar().showMessage("Deck validation failed")
        # Reuse the standard error presenter for details.
        self._on_worker_failed(err)

    def _on_decks_validation_failed(self, err: str) -> None:
        self._btn_deck_validate.setEnabled(True)
        self._deck_edit_summary.setText("Validation failed")
        self._on_worker_failed(err)

    def _on_deck_validation_done(self, rows: object) -> None:
        print(f"[deck-ui] validation finished signal received: {type(rows)}", file=sys.stderr)
        self._btn_validate.setEnabled(True)
        self._deck_worker = None

        if not isinstance(rows, list):
            self.statusBar().showMessage("Deck validation failed")
            return

        self.statusBar().showMessage(f"Deck validation complete — {len(rows)} line(s)")
        self._deck_table.setRowCount(len(rows))
        missing = 0
        partial = 0
        for i, r in enumerate(rows):
            self._deck_table.setItem(i, 0, QTableWidgetItem(getattr(r, "name", "")))
            self._deck_table.setItem(i, 1, QTableWidgetItem(getattr(r, "set_code", "") or ""))
            self._deck_table.setItem(i, 2, QTableWidgetItem(getattr(r, "collector_number", "") or ""))
            self._deck_table.setItem(i, 3, QTableWidgetItem(str(getattr(r, "requested_qty", 0))))
            self._deck_table.setItem(i, 4, QTableWidgetItem(str(getattr(r, "owned_qty", 0))))

            status = getattr(r, "status", "")
            req_qty = int(getattr(r, "requested_qty", 0) or 0)
            owned_qty = int(getattr(r, "owned_qty", 0) or 0)
            if status == "Missing":
                missing += req_qty
            elif status == "Partial":
                partial += max(req_qty - owned_qty, 0)

        buildable = (missing + partial) == 0
        if buildable:
            self._deck_summary.setText("Deck is buildable")
        else:
            self._deck_summary.setText(
                f"Deck is not buildable — missing {missing + partial} card(s) total "
                f"({missing} missing, {partial} short)"
            )

    def _on_decks_validation_done(self, rows: object) -> None:
        self._btn_deck_validate.setEnabled(True)

        if not isinstance(rows, list):
            self._deck_edit_summary.setText("Validation failed")
            return

        self._deck_cards_table.setRowCount(len(rows))
        missing = 0
        partial = 0
        for i, r in enumerate(rows):
            name = getattr(r, "name", "")
            set_code = getattr(r, "set_code", "") or ""
            collector = getattr(r, "collector_number", "") or ""
            req_qty = int(getattr(r, "requested_qty", 0) or 0)
            owned_qty = int(getattr(r, "owned_qty", 0) or 0)
            status = getattr(r, "status", "")

            self._deck_cards_table.setItem(i, 0, QTableWidgetItem(name))
            self._deck_cards_table.setItem(i, 1, QTableWidgetItem(set_code))
            self._deck_cards_table.setItem(i, 2, QTableWidgetItem(collector))
            self._deck_cards_table.setItem(i, 3, QTableWidgetItem(str(req_qty)))
            self._deck_cards_table.setItem(i, 4, QTableWidgetItem(str(owned_qty)))
            self._deck_cards_table.setItem(i, 5, QTableWidgetItem(status))

            if status == "Missing":
                missing += req_qty
            elif status == "Partial":
                partial += max(req_qty - owned_qty, 0)

        buildable = (missing + partial) == 0
        if buildable:
            self._deck_edit_summary.setText("Deck is buildable")
        else:
            self._deck_edit_summary.setText(
                f"Deck is not buildable — missing {missing + partial} card(s) total ({missing} missing, {partial} short)"
            )

    # -------------------------
    # Startup jobs
    # -------------------------
    def _has_pending_csvs(self) -> bool:
        for folder in imports_dirs():
            if folder.exists() and any(folder.glob("*.csv")):
                return True
        return False

    def _startup_begin(self) -> None:
        if self._startup_has_run:
            return
        self._startup_has_run = True

        self.statusBar().showMessage("Starting…")

        # Self-update check never blocks UI and doesn't need a modal.
        self._run_update_check()

        # First run: prompt because the DB is required.
        if self._startup_needs_initial_scryfall:
            self._prompt_scryfall_update(is_required=True)
            return

        # Normal run: check-only first. Prompt only if an update is actually available.
        self._run_scryfall_check_only()

    def _prompt_scryfall_update(self, *, is_required: bool) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Scryfall update")
        if is_required:
            box.setText(
                "No Scryfall database is installed yet.\n\n"
                "Download + import it now? (Required for searching cards and importing CSVs.)"
            )
        else:
            box.setText("A newer Scryfall database is available. Update now?")

        update_now = box.addButton("Update now", QMessageBox.ButtonRole.AcceptRole)
        later = box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(update_now)
        box.exec()

        if box.clickedButton() == update_now:
            self._run_scryfall_sync(show_dialog=is_required)
            return

        if is_required:
            self.statusBar().showMessage("Scryfall setup deferred — search and CSV import are unavailable")
            return

        # Not required: app is usable. Still import pending CSVs if any.
        if self._has_pending_csvs():
            self._run_csv_import(show_summary=False)
        else:
            self.statusBar().showMessage("Ready")

    def _run_scryfall_check_only(self) -> None:
        self.statusBar().showMessage("Checking Scryfall updates…")
        req = WorkerRequest(fn=_scryfall_check_worker, kwargs={"db_file": str(db_path())})
        w = Worker(req)
        w.signals.failed.connect(lambda err: self._on_scryfall_check_done(False))
        w.signals.finished.connect(lambda available: self._on_scryfall_check_done(bool(available)))
        self._pool.start(w)

    def _on_scryfall_check_done(self, available: bool) -> None:
        if available:
            self._prompt_scryfall_update(is_required=False)
            return

        if self._has_pending_csvs():
            self._run_csv_import(show_summary=False)
        else:
            self.statusBar().showMessage("Ready")

    def _show_progress(self, title: str) -> None:
        if self._progress is not None:
            self._progress.close()
        dlg = QProgressDialog(title, None, 0, 0, self)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(True)
        dlg.setAutoReset(True)
        self._progress = dlg
        dlg.show()

    def _close_progress(self) -> None:
        if self._progress is None:
            return
        self._progress.close()
        self._progress = None

    def _run_scryfall_sync(self, *, show_dialog: bool) -> None:
        # If show_dialog is False, we still run the worker, but we avoid popping a modal
        # unless we detect we are doing a real download (handled via status callback).
        if show_dialog:
            self._show_progress("Syncing Scryfall…")
        else:
            if self._progress is not None:
                self._progress.close()
                self._progress = None

        req = WorkerRequest(fn=_scryfall_worker, kwargs={"db_file": str(db_path())})
        w = Worker(req)
        w.signals.status.connect(lambda s: self._on_scryfall_status(s, show_dialog=show_dialog))
        w.signals.progress.connect(self._on_progress)
        w.signals.failed.connect(self._on_worker_failed)
        w.signals.finished.connect(lambda _: self._on_scryfall_done())
        self._pool.start(w)

    def _on_scryfall_status(self, text: str, *, show_dialog: bool) -> None:
        # Pop the modal lazily only if a download/import is actually happening.
        if self._progress is None and not show_dialog:
            if text.startswith("Downloading Scryfall") or text.startswith("Importing Scryfall"):
                self._show_progress("Syncing Scryfall…")

        if self._progress is not None:
            self._progress.setLabelText(text)
        self.statusBar().showMessage(text)

    def _on_scryfall_done(self) -> None:
        self._close_progress()
        self.statusBar().showMessage("Scryfall sync complete")
        self.refresh_search()
        self.refresh_stats()

        # After Scryfall is ready, import CSVs once per startup.
        self._run_csv_import(show_summary=False)

    def _run_csv_import(self, *, show_summary: bool) -> None:
        # If Scryfall is still missing (shouldn't happen with chained startup), don't import.
        if self._conn.execute("SELECT COUNT(*) FROM printings").fetchone()[0] == 0:
            QMessageBox.information(
                self,
                "CSV import",
                "Scryfall card data is not ready yet. Please finish the initial Scryfall download/import first.",
            )
            return

        self._csv_import_show_summary = show_summary

        self._show_progress("Importing CSVs…")
        req = WorkerRequest(fn=_csv_worker, kwargs={"db_file": str(db_path())})
        w = Worker(req)
        w.signals.status.connect(self._progress.setLabelText)  # type: ignore[union-attr]
        w.signals.progress.connect(self._on_progress)
        w.signals.failed.connect(self._on_worker_failed)
        w.signals.finished.connect(lambda res: self._on_csv_done(res))
        self._pool.start(w)

    def _on_csv_done(self, result: object) -> None:
        self._close_progress()
        self.refresh_search()
        self.refresh_stats()

        summary = "CSV import complete"
        details = ""
        if isinstance(result, list):
            imported_files = [r for r in result if getattr(r, "skipped_already_imported", False) is False]
            skipped_files = [r for r in result if getattr(r, "skipped_already_imported", False) is True]
            qty_added = sum(int(getattr(r, "quantity_added", 0) or 0) for r in imported_files)
            matched_distinct = sum(int(getattr(r, "distinct_printings_matched", 0) or 0) for r in imported_files)
            unmatched_rows = sum(int(getattr(r, "rows_unmatched", 0) or 0) for r in imported_files)
            summary = (
                f"CSV import complete — {len(imported_files)} imported, {len(skipped_files)} skipped, "
                f"{qty_added} total copies added"
            )

            # Per-file details (keep it compact).
            lines: list[str] = []
            for r in result:
                name = getattr(r, "file").name if getattr(r, "file", None) is not None else "(unknown)"
                if getattr(r, "skipped_already_imported", False):
                    lines.append(f"- {name}: skipped (already imported)")
                else:
                    lines.append(
                        f"- {name}: +{int(getattr(r, 'quantity_added', 0) or 0)} copies, "
                        f"{int(getattr(r, 'distinct_printings_matched', 0) or 0)} printings matched, "
                        f"{int(getattr(r, 'rows_unmatched', 0) or 0)} unmatched rows"
                    )
            details = "\n".join(lines)

        self.statusBar().showMessage(summary)

        if self._csv_import_show_summary:
            msg = summary
            if details:
                msg += "\n\n" + details
            QMessageBox.information(self, "CSV import", msg)
        self._csv_import_show_summary = False

    def _import_csv_via_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select CSV file(s) to import",
            "",
            "CSV files (*.csv);;All files (*.*)",
        )
        if not files:
            return

        # Copy into the preferred imports folder so the app stays consistent with its
        # 'imports folder' model, and so dedupe-by-hash works across runs.
        target_dir = None
        for d in imports_dirs():
            if d.name.lower() == "imports":
                target_dir = d
                break
        if target_dir is None:
            target_dir = imports_dirs()[0]
        target_dir.mkdir(parents=True, exist_ok=True)

        copied = 0
        for src in files:
            src_path = Path(src)
            if not src_path.exists():
                continue
            dest = target_dir / src_path.name
            if dest.exists():
                stem = dest.stem
                suffix = dest.suffix
                dest = target_dir / f"{stem}_{int(time.time())}{suffix}"
            try:
                shutil.copy2(src_path, dest)
                copied += 1
            except Exception:
                continue

        if copied == 0:
            QMessageBox.warning(self, "CSV import", "No files could be copied into the imports folder.")
            return

        self.statusBar().showMessage(f"Copied {copied} CSV file(s) into {target_dir}")
        self._run_csv_import(show_summary=True)

    def _run_update_check(self) -> None:
        req = WorkerRequest(fn=_update_worker, kwargs={})
        w = Worker(req)
        w.signals.failed.connect(lambda _: None)
        w.signals.finished.connect(self._on_update_result)
        self._pool.start(w)

    def _on_update_result(self, result: object) -> None:
        if result is None:
            return
        try:
            latest = getattr(result, "latest_version")
            url = getattr(result, "download_url")
            notes = getattr(result, "notes")
        except Exception:
            return

        msg = f"A newer version is available: {latest} (you are on {__version__})."
        if notes:
            msg += f"\n\nNotes:\n{notes}"

        box = QMessageBox(self)
        box.setWindowTitle("Update available")
        box.setText(msg)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

        if url:
            self.statusBar().showMessage(f"Update URL: {url}")

    def _on_progress(self, current: int, total: int) -> None:
        if self._progress is None:
            return
        if total <= 0:
            self._progress.setRange(0, 0)
        else:
            self._progress.setRange(0, total)
            self._progress.setValue(current)

    def _on_worker_failed(self, err: str) -> None:
        self._close_progress()
        print(err, file=sys.stderr)
        QMessageBox.critical(self, "Error", err)


def _scryfall_worker(*, db_file: str, on_status, on_progress) -> object:
    from mtg_cards.db import connect, init_db

    conn = connect(Path(db_file))
    init_db(conn)
    changed = ensure_scryfall_up_to_date(conn=conn, on_status=on_status, on_progress=on_progress)
    conn.close()
    return changed


def _scryfall_check_worker(*, db_file: str, on_status, on_progress) -> object:
    from mtg_cards.db import connect, init_db

    conn = connect(Path(db_file))
    init_db(conn)
    available = scryfall_update_available(conn=conn)
    conn.close()
    return bool(available)


def _csv_worker(*, db_file: str, on_status, on_progress) -> object:
    from mtg_cards.db import connect, init_db

    conn = connect(Path(db_file))
    init_db(conn)
    results = import_all_csvs(conn=conn, on_status=on_status, on_progress=on_progress)
    conn.close()
    return results


def _deck_validate_worker(*, db_file: str, deck_text: str, on_status, on_progress) -> object:
    from mtg_cards.db import connect

    import sys
    import time

    def log(msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        print(f"[deck {ts}] {msg}", file=sys.stderr)

    log("worker start")
    on_status("Parsing deck…")
    conn = connect(Path(db_file))
    # Log executed SQL to help diagnose stalls/locks. This is intentionally noisy only
    # for deck validation.
    try:
        conn.set_trace_callback(lambda s: log(f"SQL: {s}"))
    except Exception:
        pass
    entries = parse_decklist(deck_text)
    log(f"parsed {len(entries)} entries")
    on_status(f"Validating… {len(entries)} line(s)")
    t0 = time.perf_counter()
    rows = validate_deck(conn, entries, on_status=on_status)
    dt = time.perf_counter() - t0
    log(f"validate_deck finished in {dt:.3f}s")
    conn.close()
    log("worker done")
    return rows


def _update_worker(*, on_status, on_progress) -> object:
    return check_app_update(on_status=on_status, on_progress=on_progress)


def _fetch_image_worker(*, url: str, on_status, on_progress) -> object:
    return str(fetch_image_to_cache(url))


def _best_image_url(card_row) -> str | None:
    img = json_loads(card_row["image_uris"]) or {}
    if isinstance(img, dict):
        for k in ("large", "normal", "png", "small"):
            if img.get(k):
                return str(img[k])

    faces = json_loads(card_row["card_faces"]) or []
    if isinstance(faces, list) and faces:
        face0 = faces[0] or {}
        if isinstance(face0, dict):
            img2 = face0.get("image_uris") or {}
            if isinstance(img2, dict):
                for k in ("large", "normal", "png", "small"):
                    if img2.get(k):
                        return str(img2[k])

    return None
