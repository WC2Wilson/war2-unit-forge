from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import threading
import tempfile
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable
from contextlib import contextmanager

BASE = Path(__file__).resolve().parent
ENGINE = BASE / "forge_engine"
sys.path.insert(0, str(BASE))

from forge_engine.cards import (
    CARD_DIRTY_RVA,
    CARD_DIRTY_SETTER_RVA,
    PORT_RECORD_SIZE,
    PORT_TABLE_EXPECTED_RVA,
    UNIT_GROUP_UPDATE_ORIGINAL,
    UNIT_GROUP_UPDATE_RVA,
    UNIT_SEQUENCE_LOOKUP_ORIGINAL,
    UNIT_SEQUENCE_LOOKUP_RVA,
    UNIT_SEQUENCE_TABLE_RVA,
    UNIT_STRING_TABLE_POINTER_RVA,
    CUSTOM_UNIT_STRING_ID,
    CardService,
    RuntimeCards,
    PRODUCER_MISMATCH_ORIGINAL,
    PRODUCER_MISMATCH_PATCH,
    TRAINING_PRODUCER_TABLE_RVA,
    TRAINING_ELIGIBILITY_TABLE_RVA,
    TRAINABLE_UNIT_LIMIT,
    UNIT_VOICE_CALLBACK_TABLE_RVA,
    UNIT_CREATE_SOUND_TABLE_RVA,
    ALWAYS_TRAINABLE_CALLBACK,
    get_field,
    set_field,
)
from forge_engine.profile import (
    KNOWN_TIMESTAMPS,
    MAX_UNITS_RVA,
    SELECTED_CARD_ID_OFFSET,
    SELECTED_UNIT_GROUP_OFFSET,
    SELECTED_OBJECT_POINTER_RVA,
    SELECTED_OWNER_OFFSET,
    SELECTED_UNIT_TYPE_OFFSET,
    UNIT_ARRAY_RVA,
    UNIT_RECORD_SIZE,
    UNITDATA_DESCRIPTOR_COUNT,
    UNITDATA_DESCRIPTOR_RVA,
    fingerprint,
    locate_data_folder,
    locate_executable,
)
from forge_engine.runtime_tables import (
    BUILD_GROUP_RUNTIME_BIAS,
    GRAPHICS_ALIAS_BIT,
    GRAPHICS_LOAD_SOURCES,
    NO_GRAPHICS_FILE,
    SOURCE_DESCRIPTOR_BINDINGS,
    UNIT_GRAPHICS_POINTER_TABLE_RVA,
    UNITDATA_DESCRIPTOR_RVAS,
    UNITDATA_DESCRIPTOR_SHAPES,
    UNMASK_LOOKUP_COUNT,
    UNMASK_LOOKUP_RVA,
    LiveTableResolver,
    TYPE_FORMATS,
    TYPE_SIZES,
)
from forge_engine.war2dat.model import DatFile
from forge_engine.war2dat.schemas import UNIT_NAMES, detect_views, field_help, option_label

APP_TITLE = "Warcraft II Remaster Unit Forge 0.5.0 Runtime"
PROJECT_FORMAT = "War2RemasterUnitForgeProject"
PROJECT_VERSION = 8
DEFAULT_GAME = r"C:\Program Files (x86)\Warcraft II Remastered\x86"
DEFAULT_PROJECT = BASE / "projects" / "Elder_Assassin"
CARD_TARGET_DEFAULT = 16
SAFE_CUSTOM_TARGETS = (16, 17)
UNUSED_CUSTOM_TARGETS = (34, 36, 37, 48, 54)
RECOMMENDED_TARGET_ORDER = SAFE_CUSTOM_TARGETS + UNUSED_CUSTOM_TARGETS
CUSTOM_STRING_BASE = 36
CUSTOM_STRING_MULTI_BASE = 500
PRODUCER_OPTIONS = {
    -1: {"name": "No producer (native test / trigger only)", "card_array": "", "train_row": 0},
    60: {"name": "Human Barracks", "card_array": "sgHBarracksCard", "train_row": 1},
    61: {"name": "Orc Barracks", "card_array": "sgOBarracksCard", "train_row": 1},
}
CARD_SOURCE_DEFAULT = 8
HUMAN_BARRACKS_CARD_ID = 60
BARRACKS_REQUIREMENT_ROWS = {
    # (card array, row).  The proof option uses the Archer card's verified
    # bf_always callback so the new producer button cannot be hidden by tech.
    "Always visible (proof)": ("sgHArcherCard", 0),
    "Human Archer availability": ("sgHBarracksCard", 1),
    "Human Ranger availability": ("sgHBarracksCard", 2),
    "Human Knight availability": ("sgHBarracksCard", 4),
    "Human Paladin availability": ("sgHBarracksCard", 5),
    "Orc Axethrower availability": ("sgOBarracksCard", 1),
    "Orc Berserker availability": ("sgOBarracksCard", 2),
    "Orc Ogre availability": ("sgOBarracksCard", 4),
    "Orc Ogre Mage availability": ("sgOBarracksCard", 5),
}

TYPE_LIMITS = {
    "u8": (0, 0xFF),
    "s8": (-0x80, 0x7F),
    "u16": (0, 0xFFFF),
    "s16": (-0x8000, 0x7FFF),
    "u32": (0, 0xFFFFFFFF),
    "s32": (-0x80000000, 0x7FFFFFFF),
}


def parse_int(text: str) -> int:
    value = text.strip().replace("_", "")
    if not value:
        raise ValueError("A numeric value is required.")
    return int(value, 0)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def format_value(value: int, kind: str) -> str:
    if kind in {"u32", "s32"}:
        return f"{value} / 0x{value & 0xFFFFFFFF:08X}"
    if kind in {"u16", "s16"}:
        return f"{value} / 0x{value & 0xFFFF:04X}"
    return f"{value} / 0x{value & 0xFF:02X}"


class UnitForge(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1460x900")
        self.minsize(1160, 720)
        self.configure(bg="#121417")

        self.card_service: CardService | None = None
        self.table_resolver: LiveTableResolver | None = None
        self.test_adapter = None
        self.test_action_deferred = None
        self.reference_dat_path: Path | None = None
        self.reference_dat: DatFile | None = None
        self.unit_fields: list[tuple[str, object, int]] = []
        self.overrides: dict[str, int] = {}
        self.unit_registry: list[dict] = []
        self.active_registry_index = 0
        self.last_backup: Path | None = None
        self.localization_backup: Path | None = None
        self.busy_count = 0

        self.game_var = tk.StringVar(value=DEFAULT_GAME)
        self.project_var = tk.StringVar(value=str(DEFAULT_PROJECT))
        self.unit_name_var = tk.StringVar(value="Elder Assassin")
        self.source_var = tk.StringVar(value=f"{CARD_SOURCE_DEFAULT} - {UNIT_NAMES[CARD_SOURCE_DEFAULT]}")
        self.graphics_var = tk.StringVar(value=f"{CARD_SOURCE_DEFAULT} - {UNIT_NAMES[CARD_SOURCE_DEFAULT]}")
        self.icon_var = tk.StringVar(value=f"{CARD_SOURCE_DEFAULT} - {UNIT_NAMES[CARD_SOURCE_DEFAULT]}")
        self.target_var = tk.StringVar(value=f"{CARD_TARGET_DEFAULT} - {UNIT_NAMES[CARD_TARGET_DEFAULT]}")
        self.slot_var = tk.IntVar(value=6)
        self.string_id_var = tk.IntVar(value=CUSTOM_STRING_BASE)
        self.producer_var = tk.StringVar(value="60 - Human Barracks")
        self.requirement_var = tk.StringVar(value="Always visible (proof)")
        self.owner_var = tk.IntVar(value=0)
        self.x_var = tk.IntVar(value=30)
        self.y_var = tk.IntVar(value=30)
        self.amount_var = tk.IntVar(value=1)
        self.status_var = tk.StringVar(value="Remaster-only forge ready. Select the game folder, then inspect or attach.")
        self.connection_var = tk.StringVar(value="Not attached")
        self.gcards_var = tk.StringVar(value="gCards: unresolved")
        self.tables_var = tk.StringVar(value="Live DAT arrays: unresolved")
        self.compat_var = tk.StringVar(value="Producer compatibility: unknown")
        self.selected_var = tk.StringVar(value="Selected object: unavailable")
        self.test_var = tk.StringVar(value="Native test engine: detached")
        self.target_warning_var = tk.StringVar(value="Choose any unit-data slot. IDs 16/17 are initialized dormant unit slots; other targets may replace existing game objects or require additional engine behavior. No producer supports targets above the native trainable-unit range.")
        self.unit_registry = [self._capture_editor_spec_raw()]

        self._configure_style()
        self._build_ui()
        self._load_reference_dat(silent=True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ UI
    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background="#23272d", foreground="#e7ebf0", fieldbackground="#171a1f")
        style.configure("TFrame", background="#23272d")
        style.configure("TLabel", background="#23272d", foreground="#e7ebf0")
        style.configure("Muted.TLabel", foreground="#9aa5b1")
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"), foreground="#ffffff")
        style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"), foreground="#ffffff")
        style.configure("TButton", padding=(9, 6))
        style.configure("Accent.TButton", padding=(11, 7), font=("Segoe UI", 9, "bold"))
        style.configure("Danger.TButton", padding=(9, 6))
        style.configure("TNotebook", background="#121417", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 8))
        style.map("TNotebook.Tab", background=[("selected", "#343a43")])
        style.configure("Treeview", rowheight=25, background="#171a1f", fieldbackground="#171a1f", foreground="#e7ebf0")
        style.configure("Treeview.Heading", background="#303640", foreground="#ffffff", font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#3f5f86")])
        style.configure("TLabelframe", background="#23272d")
        style.configure("TLabelframe.Label", background="#23272d", foreground="#ffffff", font=("Segoe UI", 10, "bold"))

        # ttk comboboxes need explicit readonly-state mappings on Windows.
        # Without these, the native theme can render white text on a white field.
        style.configure(
            "TCombobox",
            background="#303640",
            fieldbackground="#171a1f",
            foreground="#e7ebf0",
            arrowcolor="#e7ebf0",
            selectbackground="#3f5f86",
            selectforeground="#ffffff",
            bordercolor="#59616d",
            lightcolor="#59616d",
            darkcolor="#121417",
        )
        style.map(
            "TCombobox",
            background=[("readonly", "#303640"), ("active", "#3a414b")],
            fieldbackground=[("readonly", "#171a1f"), ("disabled", "#20242a")],
            foreground=[("readonly", "#e7ebf0"), ("disabled", "#87919d")],
            arrowcolor=[("readonly", "#e7ebf0"), ("disabled", "#87919d")],
            selectbackground=[("readonly", "#3f5f86")],
            selectforeground=[("readonly", "#ffffff")],
        )
        # The dropdown list is a Tk Listbox, not a ttk widget.
        self.option_add("*TCombobox*Listbox.background", "#171a1f")
        self.option_add("*TCombobox*Listbox.foreground", "#e7ebf0")
        self.option_add("*TCombobox*Listbox.selectBackground", "#3f5f86")
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.option_add("*TCombobox*Listbox.font", "Segoe UI 9")

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=(16, 12))
        top.pack(fill="x")
        ttk.Label(top, text="WARCRAFT II REMASTER UNIT FORGE", style="Title.TLabel").pack(side="left")
        status_box = ttk.Frame(top)
        status_box.pack(side="right")
        ttk.Label(status_box, textvariable=self.connection_var, style="Muted.TLabel").pack(anchor="e")
        ttk.Label(status_box, textvariable=self.status_var).pack(anchor="e")

        self.book = ttk.Notebook(self)
        self.book.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        self.registry_tab = ttk.Frame(self.book, padding=12)
        self.project_tab = ttk.Frame(self.book, padding=14)
        self.data_tab = ttk.Frame(self.book, padding=10)
        self.cards_tab = ttk.Frame(self.book, padding=14)
        self.test_tab = ttk.Frame(self.book, padding=14)
        self.log_tab = ttk.Frame(self.book, padding=8)
        self.book.add(self.registry_tab, text="Unit Registry")
        self.book.add(self.project_tab, text="Unit Editor")
        self.book.add(self.data_tab, text="Unit Data")
        self.book.add(self.cards_tab, text="Live Install")
        self.book.add(self.test_tab, text="Native Test")
        self.book.add(self.log_tab, text="Log")

        self._build_registry_tab()
        self._build_project_tab()
        self._build_data_tab()
        self._build_cards_tab()
        self._build_test_tab()

        self.log = tk.Text(
            self.log_tab,
            bg="#0f1114",
            fg="#d5dce5",
            insertbackground="#ffffff",
            font=("Consolas", 9),
            wrap="word",
        )
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("good", foreground="#86d18a")
        self.log.tag_configure("warn", foreground="#e0bf67")
        self.log.tag_configure("bad", foreground="#ef7f7f")
        self.log.tag_configure("head", foreground="#8ec7ff", font=("Consolas", 9, "bold"))

    def _build_registry_tab(self) -> None:
        info = ttk.LabelFrame(self.registry_tab, text="Custom Unit Registry", padding=10)
        info.pack(fill="x")
        ttk.Label(
            info,
            text=(
                "Each row is a separate runtime unit definition. Pick the gameplay, graphics/animation, "
                "icon/portrait, target slot, producer, and individual field values independently. "
                "The editor no longer restricts the project to one hard-coded custom unit."
            ),
            wraplength=1200,
        ).pack(anchor="w")

        toolbar = ttk.Frame(self.registry_tab)
        toolbar.pack(fill="x", pady=8)
        ttk.Button(toolbar, text="Add Next Slot", style="Accent.TButton", command=self._registry_add).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Add Human + Orc Pair", command=self._registry_add_pair).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Duplicate Selected", command=self._registry_duplicate).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Update From Editor", command=self._registry_update_current).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Remove Selected", command=self._registry_remove).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Move Up", command=lambda: self._registry_move(-1)).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Move Down", command=lambda: self._registry_move(1)).pack(side="left", padx=3)
        ttk.Button(toolbar, text="INSTALL ALL REGISTRY UNITS", style="Accent.TButton", command=self._install_all_registry).pack(side="right", padx=3)

        columns = ("target", "name", "gameplay", "graphics", "icon", "producer", "slot", "string", "mode")
        self.registry_tree = ttk.Treeview(self.registry_tab, columns=columns, show="headings", selectmode="browse")
        widths = {
            "target": 75, "name": 190, "gameplay": 150, "graphics": 150,
            "icon": 150, "producer": 145, "slot": 55, "string": 90, "mode": 150,
        }
        labels = {
            "target": "Unit ID", "name": "Custom Name", "gameplay": "Gameplay Donor",
            "graphics": "Graphics Donor", "icon": "Icon Donor", "producer": "Producer",
            "slot": "Button", "string": "String Key", "mode": "Slot Mode",
        }
        for key in columns:
            self.registry_tree.heading(key, text=labels[key])
            self.registry_tree.column(key, width=widths[key], anchor="center" if key in {"target", "slot", "string"} else "w")
        y = ttk.Scrollbar(self.registry_tab, orient="vertical", command=self.registry_tree.yview)
        self.registry_tree.configure(yscrollcommand=y.set)
        self.registry_tree.pack(side="left", fill="both", expand=True)
        y.pack(side="right", fill="y")
        self.registry_tree.bind("<<TreeviewSelect>>", self._registry_selected)
        self.registry_tree.bind("<Double-1>", lambda _event: self.book.select(self.project_tab))
        self._refresh_registry_tree()

    def _build_project_tab(self) -> None:
        path_box = ttk.LabelFrame(self.project_tab, text="Remaster Project", padding=12)
        path_box.pack(fill="x")
        ttk.Label(path_box, text="Game, x86, Data, or Warcraft II.exe path").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(path_box, textvariable=self.game_var, width=100).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(path_box, text="Browse Folder", command=self._pick_game_folder).grid(row=0, column=2, padx=3)
        ttk.Button(path_box, text="Browse EXE", command=self._pick_exe).grid(row=0, column=3, padx=3)

        ttk.Label(path_box, text="Project folder").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(path_box, textvariable=self.project_var, width=100).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Button(path_box, text="Browse", command=self._pick_project).grid(row=1, column=2, padx=3)
        ttk.Button(path_box, text="Load Project", command=self._load_project_dialog).grid(row=1, column=3, padx=3)
        path_box.columnconfigure(1, weight=1)

        unit_box = ttk.LabelFrame(self.project_tab, text="Unit Slot", padding=12)
        unit_box.pack(fill="x", pady=12)
        choices = [f"{i} - {name}" for i, name in enumerate(UNIT_NAMES)]
        ttk.Label(unit_box, text="Clone source").grid(row=0, column=0, sticky="w", pady=5)
        source = ttk.Combobox(unit_box, textvariable=self.source_var, values=choices, state="readonly", width=40)
        source.grid(row=0, column=1, sticky="w", padx=8)
        source.bind("<<ComboboxSelected>>", lambda _event: self._source_target_changed())
        ttk.Label(unit_box, text="Target slot").grid(row=0, column=2, sticky="w", padx=(18, 0))
        target = ttk.Combobox(unit_box, textvariable=self.target_var, values=choices, state="readonly", width=40)
        target.grid(row=0, column=3, sticky="w", padx=8)
        target.bind("<<ComboboxSelected>>", lambda _event: self._source_target_changed())
        ttk.Label(unit_box, text="Graphics + animation donor").grid(row=1, column=0, sticky="w", pady=5)
        graphics = ttk.Combobox(unit_box, textvariable=self.graphics_var, values=choices, state="readonly", width=40)
        graphics.grid(row=1, column=1, sticky="w", padx=8)
        graphics.bind("<<ComboboxSelected>>", lambda _event: self._source_target_changed())
        ttk.Label(unit_box, text="Icon + portrait donor").grid(row=1, column=2, sticky="w", padx=(18, 0))
        icon = ttk.Combobox(unit_box, textvariable=self.icon_var, values=choices, state="readonly", width=40)
        icon.grid(row=1, column=3, sticky="w", padx=8)
        icon.bind("<<ComboboxSelected>>", lambda _event: self._source_target_changed())
        ttk.Label(unit_box, text="Custom display name").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(unit_box, textvariable=self.unit_name_var, width=42).grid(row=2, column=1, sticky="w", padx=8)
        ttk.Label(unit_box, text="Producer building").grid(row=2, column=2, sticky="w", padx=(18, 0))
        producer_values = [f"{card_id} - {data['name']}" for card_id, data in PRODUCER_OPTIONS.items()]
        producer = ttk.Combobox(unit_box, textvariable=self.producer_var, values=producer_values, state="readonly", width=40)
        producer.grid(row=2, column=3, sticky="w", padx=8)
        producer.bind("<<ComboboxSelected>>", lambda _event: self._source_target_changed())
        ttk.Label(unit_box, text="Localization string ID").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Spinbox(unit_box, from_=36, to=65535, textvariable=self.string_id_var, width=12).grid(row=3, column=1, sticky="w", padx=8)
        ttk.Label(
            unit_box,
            text="Gameplay statistics, graphics/animation, icon/portrait, sounds, status callbacks, and producer can be selected independently for every registry row.",
            wraplength=1050,
            style="Muted.TLabel",
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(10, 0))
        ttk.Label(
            unit_box,
            textvariable=self.target_warning_var,
            wraplength=1050,
            style="Muted.TLabel",
        ).grid(row=5, column=0, columnspan=4, sticky="w", pady=(5, 0))

        actions = ttk.Frame(self.project_tab)
        actions.pack(fill="x", pady=4)
        ttk.Button(actions, text="Inspect Remaster Files", style="Accent.TButton", command=self._inspect_files).pack(side="left", padx=4)
        ttk.Button(actions, text="Save Project", command=self._save_project).pack(side="left", padx=4)
        ttk.Button(actions, text="Reload DAT Reference", command=lambda: self._load_reference_dat(silent=False)).pack(side="left", padx=4)

        report_box = ttk.LabelFrame(self.project_tab, text="Detected Profile", padding=10)
        report_box.pack(fill="both", expand=True, pady=(10, 0))
        self.profile_text = tk.Text(report_box, height=18, bg="#171a1f", fg="#dce3ec", font=("Consolas", 9), wrap="word")
        self.profile_text.pack(fill="both", expand=True)

    def _build_data_tab(self) -> None:
        toolbar = ttk.Frame(self.data_tab)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="Clone All Source Values", style="Accent.TButton", command=self._clone_all_data).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Edit Selected", command=self._edit_selected_field).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Reset Selected", command=self._reset_selected_field).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Reset All Overrides", command=self._reset_all_fields).pack(side="left", padx=3)
        ttk.Label(toolbar, text="Double-click a row to edit its final value.", style="Muted.TLabel").pack(side="right")

        pane = ttk.Panedwindow(self.data_tab, orient="horizontal")
        pane.pack(fill="both", expand=True)
        table_frame = ttk.Frame(pane)
        detail_frame = ttk.LabelFrame(pane, text="Field Details", padding=10)
        pane.add(table_frame, weight=4)
        pane.add(detail_frame, weight=2)

        columns = ("field", "source", "target", "final", "type", "live")
        self.data_tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "field": ("Field", 230),
            "source": ("Source Unit", 145),
            "target": ("DAT Target Before", 145),
            "final": ("DAT / Forge Value", 145),
            "type": ("Type", 65),
            "live": ("Live RVA", 105),
        }
        for key, (text, width) in headings.items():
            self.data_tree.heading(key, text=text)
            self.data_tree.column(key, width=width, anchor="w" if key == "field" else "center")
        y = ttk.Scrollbar(table_frame, orient="vertical", command=self.data_tree.yview)
        self.data_tree.configure(yscrollcommand=y.set)
        self.data_tree.pack(side="left", fill="both", expand=True)
        y.pack(side="right", fill="y")
        self.data_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_field_details())
        self.data_tree.bind("<Double-1>", lambda _event: self._edit_selected_field())

        self.field_title = ttk.Label(detail_frame, text="Select a field", style="Section.TLabel")
        self.field_title.pack(anchor="w")
        self.field_help_text = tk.Text(detail_frame, height=15, bg="#171a1f", fg="#dce3ec", wrap="word")
        self.field_help_text.pack(fill="both", expand=True, pady=8)
        self.field_value_var = tk.StringVar(value="")
        ttk.Entry(detail_frame, textvariable=self.field_value_var).pack(fill="x", pady=4)
        ttk.Button(detail_frame, text="Apply Value", command=self._apply_detail_value).pack(fill="x", pady=3)
        self.field_option_var = tk.StringVar(value="")
        self.field_option_combo = ttk.Combobox(detail_frame, textvariable=self.field_option_var, state="readonly")
        self.field_option_combo.pack(fill="x", pady=4)
        self.field_option_combo.bind("<<ComboboxSelected>>", lambda _event: self._apply_option_value())

    def _build_cards_tab(self) -> None:
        attach_box = ttk.LabelFrame(self.cards_tab, text="Warcraft II.exe Live Connection", padding=12)
        attach_box.pack(fill="x")
        ttk.Button(attach_box, text="Attach + Resolve gCards", style="Accent.TButton", command=self._attach_live).grid(row=0, column=0, padx=4, pady=4)
        ttk.Button(attach_box, text="Refresh Selected Unit", command=self._refresh_selected).grid(row=0, column=1, padx=4)
        ttk.Button(attach_box, text="Resolve DAT Arrays", command=self._resolve_live_tables).grid(row=0, column=2, padx=4)
        ttk.Button(attach_box, text="Detach", command=self._detach_live).grid(row=0, column=3, padx=4)
        ttk.Label(attach_box, textvariable=self.gcards_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Label(attach_box, textvariable=self.tables_var).grid(row=1, column=2, columnspan=2, sticky="w", pady=4)
        ttk.Label(attach_box, textvariable=self.compat_var).grid(row=2, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Label(attach_box, textvariable=self.selected_var).grid(row=2, column=2, columnspan=2, sticky="w", pady=4)

        install_box = ttk.LabelFrame(self.cards_tab, text="Integrated Custom Unit Installation", padding=12)
        install_box.pack(fill="x", pady=12)
        ttk.Button(install_box, text="Apply Live Unit Data", command=self._apply_live_unit_data).grid(row=0, column=0, padx=4, pady=4, sticky="ew")
        ttk.Button(install_box, text="Install Unit Command Card", command=self._install_unit_card).grid(row=0, column=1, padx=4, sticky="ew")
        ttk.Button(install_box, text="Add Producer Training Button", command=self._install_barracks_button).grid(row=0, column=2, padx=4, sticky="ew")
        ttk.Button(install_box, text="INSTALL CURRENT UNIT", style="Accent.TButton", command=self._install_complete_live).grid(row=1, column=0, columnspan=2, padx=4, pady=9, sticky="ew")
        ttk.Button(install_box, text="INSTALL ALL REGISTRY UNITS", style="Accent.TButton", command=self._install_all_registry).grid(row=1, column=2, padx=4, pady=9, sticky="ew")
        for column in range(3):
            install_box.columnconfigure(column, weight=1)

        options = ttk.LabelFrame(self.cards_tab, text="Training Button", padding=12)
        options.pack(fill="x")
        ttk.Label(options, text="Panel slot (0-based)").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(options, from_=0, to=8, textvariable=self.slot_var, width=8).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(options, text="Visibility / prerequisite source").grid(row=0, column=2, sticky="w", padx=(20, 0))
        ttk.Combobox(options, textvariable=self.requirement_var, values=list(BARRACKS_REQUIREMENT_ROWS), state="readonly", width=28).grid(row=0, column=3, sticky="w", padx=8)
        ttk.Label(
            options,
            text="The button uses the selected icon donor. When a custom name is active, its tooltip uses the dedicated custom unit-name string slot. The chosen existing Barracks callback supplies availability.",
            style="Muted.TLabel",
            wraplength=950,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(9, 0))

        compat = ttk.LabelFrame(self.cards_tab, text="Producer Compatibility Guard", padding=12)
        compat.pack(fill="x", pady=12)
        ttk.Button(compat, text="Enable Required Producer Compatibility", command=lambda: self._set_compatibility(True)).pack(side="left", padx=4)
        ttk.Button(compat, text="Restore Strict Producer Check", command=lambda: self._set_compatibility(False)).pack(side="left", padx=4)
        ttk.Label(
            compat,
            text="Complete installs clone the exact producer metadata and enable the guarded 75 29 -> 90 90 compatibility patch. Both are required for copied training buttons on remaster 1.0.2.2818.",
            style="Muted.TLabel",
        ).pack(side="left", padx=14)

        restore = ttk.Frame(self.cards_tab)
        restore.pack(fill="x", pady=6)
        ttk.Button(restore, text="Restore All Live Changes", command=self._restore_live_changes).pack(side="right", padx=4)

    def _build_test_tab(self) -> None:
        info = ttk.LabelFrame(self.test_tab, text="Native Simulation-Thread Test Engine", padding=12)
        info.pack(fill="x")
        ttk.Label(
            info,
            text="This is the proven Trigger Studio live adapter embedded inside Unit Forge. It resolves unit_create by signature and runs it through the Warcraft simulation-tick dispatcher, not a CreateRemoteThread call.",
            wraplength=1050,
        ).pack(anchor="w")
        ttk.Button(info, text="Attach Native Test Engine", style="Accent.TButton", command=self._attach_test_engine).pack(anchor="w", pady=8)
        ttk.Label(info, textvariable=self.test_var).pack(anchor="w")

        create = ttk.LabelFrame(self.test_tab, text="Create and Verify Target Unit", padding=12)
        create.pack(fill="x", pady=12)
        ttk.Label(create, text="Owner (0=P1)").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(create, from_=0, to=15, textvariable=self.owner_var, width=8).grid(row=0, column=1, padx=6)
        ttk.Label(create, text="Tile X").grid(row=0, column=2, sticky="w", padx=(20, 0))
        ttk.Spinbox(create, from_=0, to=255, textvariable=self.x_var, width=8).grid(row=0, column=3, padx=6)
        ttk.Label(create, text="Tile Y").grid(row=0, column=4, sticky="w", padx=(20, 0))
        ttk.Spinbox(create, from_=0, to=255, textvariable=self.y_var, width=8).grid(row=0, column=5, padx=6)
        ttk.Label(create, text="Amount").grid(row=0, column=6, sticky="w", padx=(20, 0))
        ttk.Spinbox(create, from_=1, to=25, textvariable=self.amount_var, width=8).grid(row=0, column=7, padx=6)
        ttk.Button(create, text="Create Target Unit", style="Accent.TButton", command=self._create_test_units).grid(row=1, column=0, columnspan=2, sticky="ew", pady=10, padx=3)
        ttk.Button(create, text="Create Enemy Test (P8)", command=self._create_enemy_test_unit).grid(row=1, column=2, columnspan=2, sticky="ew", pady=10, padx=3)
        ttk.Button(create, text="Queue in Selected Producer", command=self._queue_selected_barracks).grid(row=1, column=4, columnspan=2, sticky="ew", pady=10, padx=3)
        ttk.Button(create, text="Count Target Units", command=self._count_test_units).grid(row=1, column=6, columnspan=2, sticky="ew", pady=10, padx=3)
        ttk.Label(
            create,
            text="Queue in Selected Producer calls Warcraft's native bldg_build_start on the simulation thread. It verifies whether the target unit itself is producible independently of the command-card click path.",
            style="Muted.TLabel",
            wraplength=1050,
        ).grid(row=2, column=0, columnspan=8, sticky="w", pady=(2, 0))

        self.test_text = tk.Text(self.test_tab, bg="#171a1f", fg="#dce3ec", font=("Consolas", 9), wrap="word")
        self.test_text.pack(fill="both", expand=True)

    # ------------------------------------------------------------ Utilities
    def _log(self, text: str, tag: str | None = None) -> None:
        stamp = time.strftime("%H:%M:%S")
        def write() -> None:
            self.log.insert("end", f"[{stamp}] {text}\n", tag or "")
            self.log.see("end")
        if threading.current_thread() is threading.main_thread():
            write()
        else:
            self.after(0, write)

    def _test_log(self, text: str) -> None:
        self._log(text)
        def write() -> None:
            self.test_text.insert("end", text + "\n")
            self.test_text.see("end")
        if threading.current_thread() is threading.main_thread():
            write()
        else:
            self.after(0, write)

    def _set_status(self, text: str) -> None:
        if threading.current_thread() is threading.main_thread():
            self.status_var.set(text)
        else:
            self.after(0, lambda: self.status_var.set(text))

    def _run_job(self, label: str, work: Callable[[], object], done: Callable[[object], None] | None = None) -> None:
        self.busy_count += 1
        self._set_status(label)
        self._log(label, "head")

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:
                detail = traceback.format_exc()
                self._log(f"{label} failed: {exc}", "bad")
                self._log(detail, "bad")
                error_text = str(exc)
                self.after(0, lambda error_text=error_text: messagebox.showerror(APP_TITLE, error_text, parent=self))
            else:
                if done:
                    self.after(0, lambda: done(result))
            finally:
                def finish() -> None:
                    self.busy_count = max(0, self.busy_count - 1)
                    if self.busy_count == 0:
                        self.status_var.set("Ready")
                self.after(0, finish)

        threading.Thread(target=runner, daemon=True).start()

    def _capture_editor_spec_raw(self) -> dict:
        producer_text = self.producer_var.get() if hasattr(self, "producer_var") else "60 - Human Barracks"
        return {
            "unit_name": str(self.unit_name_var.get()),
            "source_id": int(self.source_var.get().split(" ", 1)[0]),
            "graphics_id": int(self.graphics_var.get().split(" ", 1)[0]),
            "icon_id": int(self.icon_var.get().split(" ", 1)[0]),
            "target_id": int(self.target_var.get().split(" ", 1)[0]),
            "producer_card_id": int(producer_text.split(" ", 1)[0]),
            "panel_slot": int(self.slot_var.get()),
            "string_id": int(self.string_id_var.get()) if hasattr(self, "string_id_var") else CUSTOM_STRING_BASE,
            "requirement_source": str(self.requirement_var.get()),
            "overrides": {str(k): int(v) for k, v in self.overrides.items()},
        }

    def _capture_editor_spec(self) -> dict:
        spec = self._capture_editor_spec_raw()
        spec["unit_name"] = self._clean_custom_name(spec["unit_name"])
        return spec

    def _load_editor_spec(self, spec: dict) -> None:
        source = int(spec.get("source_id", CARD_SOURCE_DEFAULT))
        graphics = int(spec.get("graphics_id", source))
        icon = int(spec.get("icon_id", source))
        target = int(spec.get("target_id", CARD_TARGET_DEFAULT))
        producer = int(spec.get("producer_card_id", HUMAN_BARRACKS_CARD_ID))
        self.source_var.set(f"{source} - {UNIT_NAMES[source]}")
        self.graphics_var.set(f"{graphics} - {UNIT_NAMES[graphics]}")
        self.icon_var.set(f"{icon} - {UNIT_NAMES[icon]}")
        self.target_var.set(f"{target} - {UNIT_NAMES[target]}")
        self.producer_var.set(f"{producer} - {PRODUCER_OPTIONS.get(producer, {'name': 'Producer'})['name']}")
        self.unit_name_var.set(str(spec.get("unit_name", f"Custom Unit {target}")))
        self.slot_var.set(int(spec.get("panel_slot", 6)))
        self.string_id_var.set(int(spec.get("string_id", CUSTOM_STRING_BASE)))
        requirement = str(spec.get("requirement_source", "Always visible (proof)"))
        self.requirement_var.set(requirement if requirement in BARRACKS_REQUIREMENT_ROWS else "Always visible (proof)")
        self.overrides = {str(k): int(v) for k, v in dict(spec.get("overrides", {})).items()}
        self._source_target_changed(save=False)

    def _current_spec(self) -> dict:
        return self._capture_editor_spec()

    def _registry_validate(self, specs: list[dict] | None = None) -> list[dict]:
        specs = [dict(item) for item in (specs if specs is not None else self.unit_registry)]
        if not specs:
            raise ValueError("Add at least one custom unit to the registry.")
        targets: set[int] = set()
        buttons: set[tuple[int, int]] = set()
        strings: set[int] = set()
        for spec in specs:
            target = int(spec["target_id"])
            producer = int(spec.get("producer_card_id", 60))
            slot = int(spec.get("panel_slot", 6))
            string_id = int(spec.get("string_id", CUSTOM_STRING_BASE))
            if target in targets:
                raise ValueError(f"Unit {target} appears more than once in the registry.")
            targets.add(target)
            for label, donor in (
                ("gameplay", int(spec.get("source_id", -1))),
                ("graphics", int(spec.get("graphics_id", spec.get("source_id", -1)))),
                ("icon", int(spec.get("icon_id", spec.get("source_id", -1)))),
            ):
                if not 0 <= donor < len(UNIT_NAMES):
                    raise ValueError(
                        f"Unit {target} {label} donor {donor} is outside the unit-data range 0-{len(UNIT_NAMES)-1}."
                    )
            if producer not in PRODUCER_OPTIONS:
                raise ValueError(f"Unit {target} uses unsupported producer card {producer}.")
            if producer >= 0:
                if not 0 <= slot <= 8:
                    raise ValueError(f"Unit {target} button slot must be 0-8.")
                key = (producer, slot)
                if key in buttons:
                    raise ValueError(f"Producer {producer} button slot {slot} is assigned twice.")
                buttons.add(key)
            if not 1 <= string_id <= 0xFFFF:
                raise ValueError(f"Unit {target} localization string ID must be 1-65535.")
            if string_id != CUSTOM_STRING_BASE and string_id < CUSTOM_STRING_MULTI_BASE:
                raise ValueError(
                    f"Unit {target} uses stat_txt_{string_id}, which may overwrite a built-in remaster string. "
                    f"Use stat_txt_{CUSTOM_STRING_BASE} for the first custom unit or {CUSTOM_STRING_MULTI_BASE}+ for additional units."
                )
            if string_id in strings:
                raise ValueError(f"Localization string ID {string_id} is assigned twice.")
            strings.add(string_id)
            if not self._clean_custom_name(str(spec.get("unit_name", ""))):
                raise ValueError(f"Unit {target} needs a custom display name.")
            if not 0 <= target < len(UNIT_NAMES):
                raise ValueError(f"Unit {target} is outside the unit-data range 0-{len(UNIT_NAMES)-1}.")
            if producer >= 0 and target >= TRAINABLE_UNIT_LIMIT:
                raise ValueError(
                    f"Unit {target} can be edited and installed at runtime, but native unit training buttons "
                    f"support IDs 0-{TRAINABLE_UNIT_LIMIT-1}. Choose No producer for this target."
                )
        return specs

    def _sync_active_registry(self) -> None:
        if not self.unit_registry:
            self.unit_registry = [self._capture_editor_spec_raw()]
            self.active_registry_index = 0
            return
        self.active_registry_index = max(0, min(self.active_registry_index, len(self.unit_registry) - 1))
        self.unit_registry[self.active_registry_index] = self._capture_editor_spec_raw()

    def _refresh_registry_tree(self) -> None:
        if not hasattr(self, "registry_tree"):
            return
        self.registry_tree.delete(*self.registry_tree.get_children())
        for index, spec in enumerate(self.unit_registry):
            target = int(spec.get("target_id", 0))
            source = int(spec.get("source_id", 0))
            graphics = int(spec.get("graphics_id", source))
            icon = int(spec.get("icon_id", source))
            producer = int(spec.get("producer_card_id", 60))
            mode = "Proven initialized" if target in SAFE_CUSTOM_TARGETS else (
                "Full bootstrap" if target in UNUSED_CUSTOM_TARGETS else "Replaces existing"
            )
            self.registry_tree.insert("", "end", iid=str(index), values=(
                f"{target} / 0x{target:02X}", spec.get("unit_name", ""),
                f"{source} {UNIT_NAMES[source]}", f"{graphics} {UNIT_NAMES[graphics]}",
                f"{icon} {UNIT_NAMES[icon]}", PRODUCER_OPTIONS.get(producer, {"name": str(producer)})["name"],
                (int(spec.get("panel_slot", 6)) if producer >= 0 else "—"),
                f"stat_txt_{int(spec.get('string_id', CUSTOM_STRING_BASE))}", mode,
            ))
        if self.unit_registry:
            iid = str(max(0, min(self.active_registry_index, len(self.unit_registry)-1)))
            self.registry_tree.selection_set(iid)
            self.registry_tree.focus(iid)

    def _registry_selected(self, _event=None) -> None:
        selected = self.registry_tree.selection() if hasattr(self, "registry_tree") else ()
        if not selected:
            return
        new_index = int(selected[0])
        if new_index == self.active_registry_index:
            return
        self._sync_active_registry()
        self.active_registry_index = new_index
        self._load_editor_spec(self.unit_registry[new_index])
        self._refresh_registry_tree()

    def _next_free_target(self) -> int:
        used = {int(spec.get("target_id", -1)) for spec in self.unit_registry}
        for target in RECOMMENDED_TARGET_ORDER:
            if target not in used:
                return target
        raise RuntimeError(
            "All recommended expansion slots are already in the registry. "
            "Choose an existing unit slot manually only when you intentionally want to replace it."
        )

    def _next_free_string_id(self) -> int:
        used = {int(spec.get("string_id", -1)) for spec in self.unit_registry}
        if CUSTOM_STRING_BASE not in used:
            return CUSTOM_STRING_BASE
        value = CUSTOM_STRING_MULTI_BASE
        while value in used:
            value += 1
        return value

    def _next_free_button_slot(self, producer: int) -> int:
        used = {int(spec.get("panel_slot", -1)) for spec in self.unit_registry if int(spec.get("producer_card_id", 60)) == producer}
        for slot in range(6, 9):
            if slot not in used:
                return slot
        raise RuntimeError(
            f"{PRODUCER_OPTIONS.get(producer, {'name': str(producer)})['name']} has no unused expansion button slot. "
            "Slots 6-8 are the three normally empty positions; choose 0-5 manually only when replacing an original button."
        )

    def _auto_producer_and_slot(self) -> tuple[int, int]:
        for producer in (60, 61):
            try:
                return producer, self._next_free_button_slot(producer)
            except RuntimeError:
                continue
        return -1, 6

    def _registry_add(self) -> None:
        self._sync_active_registry()
        target = self._next_free_target()
        source = 9 if target == 17 else 8
        if target == 17:
            producer, panel_slot = 61, self._next_free_button_slot(61)
        else:
            producer, panel_slot = self._auto_producer_and_slot()
        spec = {
            "unit_name": f"Custom {UNIT_NAMES[source]}", "source_id": source,
            "graphics_id": source, "icon_id": source, "target_id": target,
            "producer_card_id": producer, "panel_slot": panel_slot,
            "string_id": self._next_free_string_id(), "requirement_source": "Always visible (proof)",
            "overrides": {},
        }
        self.unit_registry.append(spec)
        self.active_registry_index = len(self.unit_registry) - 1
        self._load_editor_spec(spec)
        self._refresh_registry_tree()
        self.book.select(self.project_tab)

    def _registry_add_pair(self) -> None:
        """Add the proven Human/Orc expansion pair without duplicating existing rows."""
        self._sync_active_registry()
        used = {int(spec.get("target_id", -1)) for spec in self.unit_registry}
        added = []
        for target, source, producer, name in (
            (16, 8, 60, "Elder Assassin"),
            (17, 9, 61, "Shadow Raider"),
        ):
            if target in used:
                continue
            spec = {
                "unit_name": name, "source_id": source,
                "graphics_id": source, "icon_id": source, "target_id": target,
                "producer_card_id": producer, "panel_slot": self._next_free_button_slot(producer),
                "string_id": self._next_free_string_id(), "requirement_source": "Always visible (proof)",
                "overrides": {},
            }
            self.unit_registry.append(spec)
            used.add(target)
            added.append(spec)
        if not added:
            messagebox.showinfo(APP_TITLE, "Units 16 and 17 are already in the registry.", parent=self)
            return
        self.active_registry_index = len(self.unit_registry) - 1
        self._load_editor_spec(self.unit_registry[self.active_registry_index])
        self._refresh_registry_tree()
        self.book.select(self.registry_tab)

    def _registry_duplicate(self) -> None:
        self._sync_active_registry()
        source_spec = dict(self.unit_registry[self.active_registry_index])
        source_spec["overrides"] = dict(source_spec.get("overrides", {}))
        source_spec["target_id"] = self._next_free_target()
        source_spec["string_id"] = self._next_free_string_id()
        duplicate_producer = int(source_spec.get("producer_card_id", 60))
        if duplicate_producer >= 0:
            try:
                source_spec["panel_slot"] = self._next_free_button_slot(duplicate_producer)
            except RuntimeError:
                duplicate_producer, duplicate_slot = self._auto_producer_and_slot()
                source_spec["producer_card_id"] = duplicate_producer
                source_spec["panel_slot"] = duplicate_slot
        source_spec["unit_name"] = str(source_spec.get("unit_name", "Custom Unit")) + " II"
        self.unit_registry.append(source_spec)
        self.active_registry_index = len(self.unit_registry)-1
        self._load_editor_spec(source_spec)
        self._refresh_registry_tree()

    def _registry_update_current(self) -> None:
        self._sync_active_registry()
        self._registry_validate(self.unit_registry)
        self._refresh_registry_tree()
        self._save_project(silent=True)
        self._log(f"Updated registry Unit {self._target_id()} from the editor.", "good")

    def _registry_remove(self) -> None:
        if len(self.unit_registry) <= 1:
            messagebox.showerror(APP_TITLE, "The registry must contain at least one unit.", parent=self)
            return
        del self.unit_registry[self.active_registry_index]
        self.active_registry_index = min(self.active_registry_index, len(self.unit_registry)-1)
        self._load_editor_spec(self.unit_registry[self.active_registry_index])
        self._refresh_registry_tree()

    def _registry_move(self, delta: int) -> None:
        self._sync_active_registry()
        target = self.active_registry_index + int(delta)
        if not 0 <= target < len(self.unit_registry):
            return
        self.unit_registry[self.active_registry_index], self.unit_registry[target] = self.unit_registry[target], self.unit_registry[self.active_registry_index]
        self.active_registry_index = target
        self._refresh_registry_tree()

    def _producer_id(self) -> int:
        return int(self.producer_var.get().split(" ", 1)[0])

    def _current_string_id(self) -> int:
        return int(self.string_id_var.get())

    def _source_id(self) -> int:
        return int(self.source_var.get().split(" ", 1)[0])

    def _target_id(self) -> int:
        return int(self.target_var.get().split(" ", 1)[0])

    def _graphics_id(self) -> int:
        return int(self.graphics_var.get().split(" ", 1)[0])

    def _icon_id(self) -> int:
        return int(self.icon_var.get().split(" ", 1)[0])

    def _project_folder(self) -> Path:
        path = Path(self.project_var.get()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _data_folder(self) -> Path:
        result = locate_data_folder(self.game_var.get())
        if result is None:
            raise FileNotFoundError(
                "Could not find the remaster DAT files. Expected either the selected folder or "
                "an adjacent x86\\Data\\Rez folder containing unitdata.dat and unitdato.dat.\n\n"
                f"Selected path: {self.game_var.get()}"
            )
        return result

    def _game_exe(self) -> Path:
        result = locate_executable(self.game_var.get())
        if result is None:
            raise FileNotFoundError(
                "Could not find Warcraft II.exe. Select the EXE itself, the x86 folder, "
                "or its Data\\Rez folder.\n\n"
                f"Selected path: {self.game_var.get()}"
            )
        return result

    def _clean_custom_name(self, value: str | None = None) -> str:
        clean = " ".join(
            str(self.unit_name_var.get() if value is None else value).replace("\r", " ").replace("\n", " ").split()
        ).strip()
        if not clean:
            raise ValueError("Enter a custom display name.")
        encoded = clean.encode("utf-8")
        if len(encoded) > 127:
            raise ValueError("The custom display name is limited to 127 UTF-8 bytes.")
        return clean

    def _pick_game_folder(self) -> None:
        path = filedialog.askdirectory(parent=self, initialdir=self.game_var.get() or None)
        if path:
            self.game_var.set(path)
            self._load_reference_dat(silent=True)

    def _pick_exe(self) -> None:
        path = filedialog.askopenfilename(parent=self, filetypes=(("Warcraft II executable", "*.exe"), ("All files", "*.*")))
        if path:
            self.game_var.set(path)
            self._load_reference_dat(silent=True)

    def _pick_project(self) -> None:
        path = filedialog.askdirectory(parent=self, initialdir=self.project_var.get() or None)
        if path:
            self.project_var.set(path)

    # --------------------------------------------------------- Project/data
    def _inspect_files(self) -> None:
        try:
            exe = self._game_exe()
            data = self._data_folder()
            fp = fingerprint(exe)
            report = {
                "executable": str(exe),
                "profile": fp.profile,
                "supported": fp.supported,
                "timestamp": f"0x{fp.timestamp:08X}",
                "image_size": f"0x{fp.image_size:X}",
                "sha256": fp.sha256,
                "data_folder": str(data),
                "files": {},
            }
            for name in ("unitdata.dat", "unitdato.dat", "upgrades.dat"):
                path = data / name
                report["files"][name] = {
                    "exists": path.exists(),
                    "size": path.stat().st_size if path.exists() else 0,
                    "sha256": sha256(path) if path.exists() else "",
                }
            self.profile_text.delete("1.0", "end")
            self.profile_text.insert("end", json.dumps(report, indent=2))
            self._log(f"Detected {fp.profile}; PE timestamp 0x{fp.timestamp:08X}, image 0x{fp.image_size:X}", "good" if fp.supported else "warn")
            self._load_reference_dat(silent=True)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)

    def _load_reference_dat(self, silent: bool) -> None:
        try:
            data_folder = locate_data_folder(self.game_var.get())
            path = data_folder / "unitdata.dat" if data_folder else BASE / "originals" / "unitdata.dat"
            self.reference_dat_path = path
            self.reference_dat = DatFile(path)
            self.unit_fields.clear()
            for view in detect_views(path, len(self.reference_dat.data)):
                if view.name not in {"Units", "Unit Groups", "Men Right-click"}:
                    continue
                for field in view.fields:
                    self.unit_fields.append((view.name, field, len(view.rows)))
            self._refresh_data_tree()
            if not silent:
                self._log(f"Loaded DAT reference: {path} ({len(self.reference_dat.data)} bytes)", "good")
        except Exception as exc:
            if not silent:
                messagebox.showerror(APP_TITLE, str(exc), parent=self)

    def _field_values(self, field, rows: int) -> tuple[int | None, int | None, int | None]:
        if not self.reference_dat:
            return None, None, None
        source = self._source_id()
        target = self._target_id()
        if source >= rows or target >= rows:
            return None, None, None
        source_value = self.reference_dat.read(field.offset + source * field.stride, field.kind)
        target_value = self.reference_dat.read(field.offset + target * field.stride, field.kind)
        final_value = self.overrides.get(field.source, source_value)
        return source_value, target_value, final_value

    def _refresh_data_tree(self) -> None:
        if not hasattr(self, "data_tree"):
            return
        selected = self.data_tree.selection()
        selected_source = selected[0] if selected else ""
        self.data_tree.delete(*self.data_tree.get_children())
        live_tables = self.table_resolver.tables if self.table_resolver else {}
        for view_name, field, rows in self.unit_fields:
            source_value, target_value, final_value = self._field_values(field, rows)
            live = live_tables.get(field.source)
            live_text = f"0x{live.rva:08X}" if live else "—"
            self.data_tree.insert(
                "",
                "end",
                iid=field.source,
                values=(
                    f"{field.name}  [{view_name}]",
                    "—" if source_value is None else format_value(source_value, field.kind),
                    "—" if target_value is None else format_value(target_value, field.kind),
                    "—" if final_value is None else format_value(final_value, field.kind),
                    field.kind,
                    live_text,
                ),
                tags=("override",) if field.source in self.overrides else (),
            )
        self.data_tree.tag_configure("override", foreground="#ffd37a")
        if selected_source and self.data_tree.exists(selected_source):
            self.data_tree.selection_set(selected_source)
        self._show_field_details()

    def _source_target_changed(self, save: bool = True) -> None:
        target = self._target_id()
        if target in SAFE_CUSTOM_TARGETS:
            text = f"Proven expansion slot: Unit {target} already has complete land-unit initialization and is normally dormant in melee."
        elif target in {34, 36, 37}:
            text = f"Unused legacy slot Unit {target}: full bootstrap plus multi-unit group/sequence aliases will replace its carrier/minelayer leftovers."
        elif target in {48, 54}:
            text = f"Blank unused slot Unit {target}: full bootstrap will create its DAT, gPorts, sounds, sequences, cards, graphics, and producer records. Test before deployment."
        else:
            text = "This target replaces an existing game unit. Use a recommended expansion slot unless replacement is intentional."
        self.target_warning_var.set(text)
        self._refresh_data_tree()
        if save:
            self._sync_active_registry()
            self._refresh_registry_tree()
            self._save_project(silent=True)

    def _clone_all_data(self) -> None:
        self.overrides.clear()
        self._refresh_data_tree()
        self._log(f"Forge values reset to clone Unit {self._source_id()} into Unit {self._target_id()}.", "good")

    def _selected_field(self):
        selected = self.data_tree.selection()
        if not selected:
            return None
        source_name = selected[0]
        for view_name, field, rows in self.unit_fields:
            if field.source == source_name:
                return view_name, field, rows
        return None

    def _edit_selected_field(self) -> None:
        selected = self._selected_field()
        if not selected:
            return
        _view, field, rows = selected
        _source, _target, current = self._field_values(field, rows)
        if current is None:
            return
        text = simpledialog.askstring(
            APP_TITLE,
            f"New value for {field.name}\nDecimal or 0x hexadecimal:",
            initialvalue=str(current),
            parent=self,
        )
        if text is None:
            return
        self._set_field_override(field, parse_int(text))

    def _set_field_override(self, field, value: int) -> None:
        minimum, maximum = TYPE_LIMITS[field.kind]
        if not minimum <= value <= maximum:
            raise ValueError(f"{field.name} must be between {minimum} and {maximum}.")
        self.overrides[field.source] = int(value)
        self._refresh_data_tree()
        self._save_project(silent=True)

    def _reset_selected_field(self) -> None:
        selected = self._selected_field()
        if not selected:
            return
        self.overrides.pop(selected[1].source, None)
        self._refresh_data_tree()

    def _reset_all_fields(self) -> None:
        self.overrides.clear()
        self._refresh_data_tree()

    def _show_field_details(self) -> None:
        if not hasattr(self, "field_help_text"):
            return
        selected = self._selected_field()
        self.field_help_text.delete("1.0", "end")
        if not selected:
            self.field_title.configure(text="Select a field")
            self.field_value_var.set("")
            self.field_option_combo.configure(values=())
            return
        view_name, field, rows = selected
        source_value, target_value, final_value = self._field_values(field, rows)
        self.field_title.configure(text=f"{field.name} — {field.source}")
        details = [
            field_help(field),
            "",
            f"View: {view_name}",
            f"DAT offset: 0x{field.offset:X}",
            f"Type / stride: {field.kind} / {field.stride}",
            f"DAT source value: {source_value}",
            f"DAT target before: {target_value}",
            f"DAT / forge value: {final_value}",
        ]
        if self.table_resolver and field.source in self.table_resolver.tables:
            table = self.table_resolver.tables[field.source]
            details.append(f"Live table RVA: 0x{table.rva:08X}")
            details.append(f"Resolution: {table.resolution}")
            try:
                if self._source_id() < table.rows:
                    details.append(
                        f"Live source value: {self.table_resolver.read_value(field.source, self._source_id())}"
                    )
                if self._target_id() < table.rows:
                    details.append(
                        f"Live target value: {self.table_resolver.read_value(field.source, self._target_id())}"
                    )
            except Exception as exc:
                details.append(f"Live value read failed: {exc}")
            if field.source == "gwBuildGroup":
                details.append(
                    f"Runtime conversion: live value = DAT value + {BUILD_GROUP_RUNTIME_BIAS}."
                )
            elif field.source == "gUnitUnmaskTbl":
                details.append(
                    "Runtime conversion: the DAT index is replaced with the corresponding live unmask lookup value."
                )
        self.field_help_text.insert("end", "\n".join(details))
        self.field_value_var.set("" if final_value is None else str(final_value))

        # Use the schema's public label helper by scanning practical values.
        options: list[str] = []
        if final_value is not None:
            minimum, maximum = TYPE_LIMITS[field.kind]
            scan_max = min(maximum, 255)
            for value in range(max(0, minimum), scan_max + 1):
                label = option_label(field, value, self._target_id())
                if label:
                    options.append(f"{value} - {label}")
            current_label = option_label(field, final_value, self._target_id())
            self.field_option_var.set(f"{final_value} - {current_label}" if current_label else "")
        self.field_option_combo.configure(values=options)

    def _apply_detail_value(self) -> None:
        selected = self._selected_field()
        if not selected:
            return
        try:
            self._set_field_override(selected[1], parse_int(self.field_value_var.get()))
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)

    def _apply_option_value(self) -> None:
        selected = self._selected_field()
        if not selected or not self.field_option_var.get():
            return
        try:
            value = int(self.field_option_var.get().split(" ", 1)[0])
            self._set_field_override(selected[1], value)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)

    def _project_payload(self) -> dict:
        self._sync_active_registry()
        return {
            "format": PROJECT_FORMAT,
            "version": PROJECT_VERSION,
            "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
            "game_path": self.game_var.get(),
            "active_registry_index": self.active_registry_index,
            "units": self.unit_registry,
            # Legacy mirror keeps older scripts able to inspect the active unit.
            **self._capture_editor_spec_raw(),
        }

    def _save_project(self, silent: bool = False) -> None:
        try:
            project = self._project_folder()
            path = project / "unit_forge_project.json"
            path.write_text(json.dumps(self._project_payload(), indent=2), encoding="utf-8")
            if not silent:
                self._log(f"Saved multi-unit project: {path}", "good")
        except Exception as exc:
            if not silent:
                messagebox.showerror(APP_TITLE, str(exc), parent=self)

    def _load_project_dialog(self) -> None:
        initial = self.project_var.get()
        path = filedialog.askopenfilename(parent=self, initialdir=initial, filetypes=(("Unit Forge project", "*.json"), ("All files", "*.*")))
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            if payload.get("format") != PROJECT_FORMAT:
                raise ValueError("This is not a Unit Forge project file.")
            self.project_var.set(str(Path(path).parent))
            self.game_var.set(str(payload.get("game_path", self.game_var.get())))
            units = payload.get("units")
            if isinstance(units, list) and units:
                self.unit_registry = [dict(item) for item in units]
            else:
                self.unit_registry = [{
                    "unit_name": payload.get("unit_name", "Elder Assassin"),
                    "source_id": int(payload.get("source_id", CARD_SOURCE_DEFAULT)),
                    "graphics_id": int(payload.get("graphics_id", payload.get("source_id", CARD_SOURCE_DEFAULT))),
                    "icon_id": int(payload.get("icon_id", payload.get("source_id", CARD_SOURCE_DEFAULT))),
                    "target_id": int(payload.get("target_id", CARD_TARGET_DEFAULT)),
                    "producer_card_id": int(payload.get("producer_card_id", HUMAN_BARRACKS_CARD_ID)),
                    "panel_slot": int(payload.get("panel_slot", 6)),
                    "string_id": int(payload.get("string_id", CUSTOM_STRING_BASE)),
                    "requirement_source": payload.get("requirement_source", "Always visible (proof)"),
                    "overrides": dict(payload.get("overrides", {})),
                }]
            self.active_registry_index = max(0, min(int(payload.get("active_registry_index", 0)), len(self.unit_registry)-1))
            self._registry_validate(self.unit_registry)
            self._load_editor_spec(self.unit_registry[self.active_registry_index])
            self._load_reference_dat(silent=True)
            self._refresh_registry_tree()
            self._log(f"Loaded {len(self.unit_registry)}-unit project: {path}", "good")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)

    def _validate_live_target(self, target: int | None = None) -> int:
        target = self._target_id() if target is None else int(target)
        if not 0 <= target < len(UNIT_NAMES):
            raise RuntimeError(f"Unit {target} is outside the unit-data range 0-{len(UNIT_NAMES)-1}.")
        return target

    # ------------------------------------------------------------- Live core
    def _attach_live(self) -> None:
        if os.name != "nt":
            messagebox.showerror(APP_TITLE, "Live attach is available only on Windows.", parent=self)
            return
        if self.card_service:
            self._detach_live()

        def work():
            service = CardService(ENGINE)
            runtime = service.attach(progress=lambda text: self._log(text))
            return service, runtime

        def done(result) -> None:
            service, runtime = result
            self.card_service = service
            self.table_resolver = None
            fp = fingerprint(service.process.path)
            self.connection_var.set(f"Attached PID {service.process.pid} | base 0x{service.process.base:08X} | {fp.profile}")
            self.gcards_var.set(f"gCards: RVA 0x{runtime.gcards_rva:08X} | {runtime.card_count} descriptors")
            self._refresh_compatibility()
            self._refresh_selected()
            self._log(f"Live attach complete: {fp.profile}", "good")

        self._run_job("Attaching to Warcraft II.exe and resolving command cards...", work, done)

    def _require_live(self):
        if not self.card_service or not self.card_service.runtime:
            raise RuntimeError("Attach to Warcraft II.exe first.")
        return self.card_service, self.card_service.runtime

    def _detach_live(self) -> None:
        if self.table_resolver and self.table_resolver.snapshots:
            if not messagebox.askyesno(APP_TITLE, "Live unit-table changes are still active. Detach without restoring them?", parent=self):
                return
        if self.card_service:
            self.card_service.close()
        self.card_service = None
        self.table_resolver = None
        self.connection_var.set("Not attached")
        self.gcards_var.set("gCards: unresolved")
        self.tables_var.set("Live DAT arrays: unresolved")
        self.compat_var.set("Producer compatibility: unknown")
        self.selected_var.set("Selected object: unavailable")
        self._refresh_data_tree()
        self._log("Detached lightweight live connection.")

    def _refresh_compatibility(self) -> None:
        if not self.card_service:
            self.compat_var.set("Producer compatibility: not attached")
            return
        raw = self.card_service.compatibility_bytes()
        if raw == PRODUCER_MISMATCH_ORIGINAL:
            text = "Producer compatibility: strict original check (75 29)"
        elif raw == PRODUCER_MISMATCH_PATCH:
            text = "Producer compatibility: enabled (90 90)"
        else:
            text = f"Producer compatibility: unexpected bytes {raw.hex(' ').upper()}"
        self.compat_var.set(text)

    def _set_compatibility(self, enabled: bool) -> None:
        try:
            service, _runtime = self._require_live()
            service.set_compatibility(enabled)
            self._refresh_compatibility()
            self._log("Enabled producer compatibility." if enabled else "Restored strict producer compatibility.", "good")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)

    def _refresh_selected(self) -> None:
        try:
            service, _runtime = self._require_live()
            pm = service.process
            pointer = pm.read_u32(pm.base + SELECTED_OBJECT_POINTER_RVA)
            if not pointer:
                self.selected_var.set("Selected object: none")
                return

            unit_pool = pm.read_u32(pm.base + UNIT_ARRAY_RVA)
            max_units = pm.read_u32(pm.base + MAX_UNITS_RVA)
            pool_end = unit_pool + max_units * UNIT_RECORD_SIZE
            if (
                not unit_pool
                or not 1 <= max_units <= 10000
                or not unit_pool <= pointer < pool_end
                or (pointer - unit_pool) % UNIT_RECORD_SIZE != 0
            ):
                self.selected_var.set(
                    f"Selected pointer 0x{pointer:08X} is not a validated unit-pool record; ignored"
                )
                return

            card = pm.read_u8(pointer + SELECTED_CARD_ID_OFFSET)
            unit_type = pm.read_u8(pointer + SELECTED_UNIT_TYPE_OFFSET)
            unit_group = pm.read_u8(pointer + SELECTED_UNIT_GROUP_OFFSET)
            owner = pm.read_u8(pointer + SELECTED_OWNER_OFFSET)
            if unit_type >= len(UNIT_NAMES) or unit_group >= 127 or owner > 15 or card >= 120:
                self.selected_var.set(
                    f"Selected unit-pool slot {(pointer-unit_pool)//UNIT_RECORD_SIZE} failed record validation; ignored"
                )
                return
            self.selected_var.set(
                f"Selected slot {(pointer-unit_pool)//UNIT_RECORD_SIZE} at 0x{pointer:08X}: "
                f"type {unit_type}, group {unit_group}, owner {owner}, card {card}"
            )
        except Exception as exc:
            self.selected_var.set(f"Selected object read failed: {exc}")

    def _resolve_live_tables(self) -> None:
        try:
            service, _runtime = self._require_live()
            dat_path = self._data_folder() / "unitdata.dat"
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return

        def work():
            resolver = LiveTableResolver(service.process, logger=lambda text: self._log(text))
            tables = resolver.resolve(dat_path)
            return resolver, tables

        def done(result) -> None:
            resolver, tables = result
            self.table_resolver = resolver
            self.tables_var.set(f"Live DAT arrays: {len(tables)} source-backed RVAs validated")
            self._refresh_data_tree()
            self._log(f"Resolved {len(tables)} DAT-backed arrays from the executable loader descriptor table.", "good")

        self._run_job("Resolving DAT-loaded arrays inside the live executable...", work, done)

    def _ensure_tables_sync(self) -> LiveTableResolver:
        service, _runtime = self._require_live()
        if self.table_resolver and self.table_resolver.tables:
            return self.table_resolver
        resolver = LiveTableResolver(service.process, logger=lambda text: self._log(text))
        resolver.resolve(self._data_folder() / "unitdata.dat")
        self.table_resolver = resolver
        self.tables_var.set(f"Live DAT arrays: {len(resolver.tables)} source-backed RVAs validated")
        self._refresh_data_tree()
        return resolver

    def _apply_live_unit_data_sync(self, spec: dict | None = None) -> int:
        resolver = self._ensure_tables_sync()
        spec = self._current_spec() if spec is None else dict(spec)
        source = int(spec["source_id"])
        graphics = int(spec.get("graphics_id", source))
        icon_source = int(spec.get("icon_id", source))
        target = self._validate_live_target(int(spec["target_id"]))
        overrides = {str(k): int(v) for k, v in dict(spec.get("overrides", {})).items()}
        writes = resolver.clone_row(source, target, overrides, verify=True)
        graphics_writes = resolver.install_graphics_alias(graphics, target)
        live_hp = resolver.read_value("gwUnitHPTbl", target)
        live_class = resolver.read_value("gbUnitClassTbl", target)
        live_flags = resolver.read_value("gUnitIsTbl", target)
        if live_hp <= 0:
            raise RuntimeError(
                f"Live Unit {target} HP remained {live_hp} after cloning Unit {source}. "
                "The target table write did not take effect; creation was stopped before Warcraft could create an unusable object."
            )
        self._log(
            f"Verified live Unit {target} gameplay row: HP {live_hp}, class {live_class}, flags 0x{live_flags:08X}.",
            "good",
        )
        _service, runtime = self._require_live()
        custom_name = self._clean_custom_name(str(spec.get("unit_name", f"Custom Unit {target}")))
        custom_string_id = int(spec.get("string_id", CUSTOM_STRING_BASE))
        # The remaster frontend resolves gPorts string IDs as JSON keys such as stat_txt_36.
        icon_id, string_id, portrait_address = runtime.install_portrait_identity(
            icon_source,
            target,
            string_id=custom_string_id if custom_name else None,
            callback_source_id=source,
        )
        if runtime.restore_custom_name():
            self._log("Removed the obsolete classic string-table clone from an older Forge session.", "good")
        if custom_name:
            string_id = custom_string_id
            # The live gPorts record now references the JSON localization key.
            # Keep this state on RuntimeCards as well so the producer button is
            # assigned the same custom tooltip instead of the donor tooltip.
            runtime.custom_name = custom_name
            self._log(
                f'Assigned Unit {target} remaster localization key stat_txt_{string_id} for "{custom_name}".',
                "good",
            )
        else:
            runtime.custom_name = None
        voice_callback, create_sound, voice_address, create_address = runtime.install_sound_aliases(source, target)
        self._log(
            f"Cloned Unit {source} remaster sound identity to Unit {target}: voice callback "
            f"0x{voice_callback:08X} at 0x{voice_address:08X}, completed-unit sound {create_sound} "
            f"at 0x{create_address:08X}.",
            "good",
        )
        gameplay_flags = resolver.read_value("gUnitIsTbl", source)
        # Several unused enum positions retain naval/air classification. Warcraft
        # also caches those bits in each live object at +0x1C, independently of
        # the DAT tables. Mirror the gameplay donor so movement and targeting agree.
        dynamic_class_bits = 0x04 if (gameplay_flags & 0x00000002) else (
            0x08 if (gameplay_flags & (0x00000008 | 0x00000040)) else 0x00
        )
        group_hook_address, group_cave = runtime.install_group_alias(
            graphics, target, dynamic_class_bits=dynamic_class_bits
        )
        sequence_hook_address, sequence_cave = runtime.install_sequence_alias(graphics, target)
        sequence_table, sequence_source_word, sequence_old_word, sequence_target_address = (
            runtime.install_sequence_table_alias(graphics, target)
        )
        all_writes = writes + graphics_writes
        changed = sum(1 for item in all_writes if item.before != item.after)
        graphics_loader_count = max(0, len(graphics_writes) - 1)
        graphics_pointer_count = 1 if graphics_writes else 0
        self._log(
            f"Applied {changed} live data/graphics changes to Unit {target}: {len(writes)} gameplay fields from Unit {source}, "
            f"{graphics_loader_count} graphics-loader entries and {graphics_pointer_count} renderer pointer from Unit {graphics}.",
            "good",
        )
        self._log(
            f"Installed mixed gPorts identity on Unit {target}: icon/frame from Unit {icon_source}, "
            f"status query/draw callbacks from gameplay Unit {source}, string {string_id}, "
            f"record 0x{portrait_address:08X}.",
            "good",
        )
        group_prefix = "Recovered and upgraded existing" if runtime.group_hook_recovered else "Installed"
        sequence_prefix = "Recovered and upgraded existing" if runtime.sequence_hook_recovered else "Installed"
        self._log(
            f"{group_prefix} unitGroup alias {target}->{graphics}: hook 0x{group_hook_address:08X}, cave 0x{group_cave:08X}; "
            f"live movement/target class bits 0x{dynamic_class_bits:02X} from gameplay Unit {source}.",
            "good",
        )
        self._log(
            f"{sequence_prefix} action-sequence alias {target}->{graphics}: hook 0x{sequence_hook_address:08X}, cave 0x{sequence_cave:08X}; "
            f"global row 0x{sequence_old_word:04X}->0x{sequence_source_word:04X} at 0x{sequence_target_address:08X} "
            f"(table 0x{sequence_table:08X}).",
            "good",
        )
        return changed

    def _apply_live_unit_data(self) -> None:
        def work():
            self._require_live()
            return self._apply_live_unit_data_sync(self._current_spec())
        self._run_job("Applying DAT-backed unit values to live Warcraft memory...", work)

    def _install_unit_card_sync(self, spec: dict | None = None) -> tuple[int, int]:
        _service, runtime = self._require_live()
        spec = self._current_spec() if spec is None else dict(spec)
        source = int(spec["source_id"])
        target = self._validate_live_target(int(spec["target_id"]))
        source_descriptor = runtime.factory_descriptors.get(source) or runtime.read_descriptor(source)
        records = runtime.load_records(source_descriptor)
        count, allocation = runtime.apply_raw(target, records)
        installed = runtime.load_current_records(target)
        if installed != records:
            raise RuntimeError(
                f"Unit {target} command-card descriptor changed, but its {len(records)} donor records did not verify byte-for-byte."
            )
        self._log(
            f"Installed and byte-verified Unit {target} command card from Unit {source}: "
            f"{count} buttons at 0x{allocation:08X}.",
            "good",
        )
        return count, allocation

    def _install_unit_card(self) -> None:
        self._run_job("Installing target unit command card...", self._install_unit_card_sync)

    def _build_training_record(self, runtime, spec: dict | None = None) -> bytes:
        spec = self._current_spec() if spec is None else dict(spec)
        icon_source = int(spec.get("icon_id", spec["source_id"]))
        producer_id = int(spec.get("producer_card_id", HUMAN_BARRACKS_CARD_ID))
        if producer_id < 0:
            raise RuntimeError("This registry unit has no producer button configured.")
        producer_info = PRODUCER_OPTIONS[producer_id]
        donor_icon, _frame, _name, _pad, _query, _draw = runtime.read_portrait_identity(icon_source)
        appearance = runtime.module_record(producer_info["card_array"], int(producer_info["train_row"]))
        appearance = set_field(appearance, "icon_id", donor_icon)
        requirement_name = str(spec.get("requirement_source", "Always visible (proof)"))
        requirement_array, requirement_row = BARRACKS_REQUIREMENT_ROWS.get(requirement_name, BARRACKS_REQUIREMENT_ROWS["Always visible (proof)"])
        requirement = runtime.module_record(requirement_array, requirement_row)
        raw = appearance
        raw = set_field(raw, "panel_slot", int(spec.get("panel_slot", 6)))
        raw = set_field(raw, "action_parameter", int(spec["target_id"]))
        if self._clean_custom_name(str(spec.get("unit_name", "Custom Unit"))):
            raw = set_field(raw, "tooltip_id", int(spec.get("string_id", CUSTOM_STRING_BASE)))
        # Keep Archer icon/tooltip/action. Replace only the existing verified
        # visibility callback and its one-byte parameter.
        result = bytearray(raw)
        result[0x04:0x08] = requirement[0x04:0x08]
        result[0x10] = requirement[0x10]
        return bytes(result)

    def _install_barracks_button_sync(self, spec: dict | None = None) -> tuple[int, int]:
        service, runtime = self._require_live()
        spec = self._current_spec() if spec is None else dict(spec)
        source = int(spec["source_id"])
        target = self._validate_live_target(int(spec["target_id"]))
        producer_card_id = int(spec.get("producer_card_id", HUMAN_BARRACKS_CARD_ID))
        panel_slot = int(spec.get("panel_slot", 6))
        if producer_card_id < 0:
            self._log(
                f"Unit {target} has no producer: command card and Native Test remain available, but no building button was installed.",
                "warn",
            )
            return 0, 0
        producer, eligibility, producer_address, eligibility_address = (
            runtime.install_training_metadata(source, target, producer_id=producer_card_id)
        )
        # Keep the exact metadata clone and also bypass the single producer-byte
        # rejection. The remaster action path validates both; using both is the
        # reliable equivalent of the old DOS train-button compatibility patch.
        service.set_compatibility(True)
        self._log(
            f"Installed native training metadata Unit {target}<-{source}: producer 0x{producer:02X} "
            f"at 0x{producer_address:08X}, stable custom eligibility callback 0x{eligibility:08X} "
            f"at 0x{eligibility_address:08X}; verified 75 29 -> 90 90 producer compatibility enabled.",
            "good",
        )
        records = runtime.load_current_records(producer_card_id)
        new_record = self._build_training_record(runtime, spec)
        target_action = get_field(new_record, "action_callback")
        replaced = False
        for index, raw in enumerate(records):
            if get_field(raw, "action_callback") == target_action and get_field(raw, "action_parameter") == target:
                records[index] = new_record
                replaced = True
                break
        if not replaced:
            records.append(new_record)
        count, allocation = runtime.apply_raw(producer_card_id, records)
        verify_records = runtime.load_current_records(producer_card_id)
        matches = [
            raw for raw in verify_records
            if get_field(raw, "action_callback") == target_action
            and get_field(raw, "action_parameter") == target
            and get_field(raw, "panel_slot") == panel_slot
        ]
        if count != len(verify_records) or len(matches) != 1:
            raise RuntimeError(
                f"Producer card {producer_card_id} changed, but its Unit {target} button did not verify exactly once."
            )
        verb = "Updated" if replaced else "Added"
        self._log(
            f"{verb} {PRODUCER_OPTIONS[producer_card_id]['name']} training button for Unit {target}: slot {panel_slot}, "
            f"{count} total buttons, allocation 0x{allocation:08X}; remaster card redraw requested.",
            "good",
        )
        return count, allocation

    def _install_barracks_button(self) -> None:
        self._run_job("Installing producer training button...", lambda: self._install_barracks_button_sync(self._current_spec()))

    def _rollback_partial_preset_sync(self) -> list[str]:
        """Best-effort transaction rollback for a preset that failed before live testing."""
        service, runtime = self._require_live()
        results: list[str] = []
        errors: list[str] = []

        def attempt(label: str, action) -> None:
            try:
                value = action()
                if value:
                    results.append(f"{label}: {value}" if value is not True else label)
            except Exception as exc:
                errors.append(f"{label}: {exc}")

        if self.table_resolver:
            attempt("unit tables", self.table_resolver.restore)
        attempt("action-sequence rows", runtime.restore_sequence_table_aliases)
        attempt("action-sequence hook", runtime.restore_sequence_alias)
        attempt("unitGroup hook", runtime.restore_group_alias)
        attempt("classic name table", runtime.restore_custom_name)
        attempt("training metadata", runtime.restore_training_metadata)
        attempt("sound identity", runtime.restore_sound_aliases)
        attempt("gPorts", runtime.restore_portrait_aliases)
        card_ids = {int(spec.get("target_id", 0)) for spec in self.unit_registry}
        card_ids.update(int(spec.get("producer_card_id", HUMAN_BARRACKS_CARD_ID)) for spec in self.unit_registry if int(spec.get("producer_card_id", HUMAN_BARRACKS_CARD_ID)) >= 0)
        for card_id in sorted(card_ids):
            if card_id in runtime.attach_descriptors:
                attempt(f"card 0x{card_id:02X}", lambda card_id=card_id: (runtime.restore_attach_state(card_id), True)[1])
        if service.compatibility_bytes() == PRODUCER_MISMATCH_PATCH:
            attempt("producer compatibility", lambda: (service.set_compatibility(False), True)[1])
        if errors:
            raise RuntimeError("; ".join(errors))
        return results

    def _install_complete_live(self) -> None:
        def work():
            self._require_live()
            localized = self._patch_installed_localizations()
            try:
                spec = self._current_spec()
                changed = self._apply_live_unit_data_sync(spec)
                unit_card = self._install_unit_card_sync(spec)
                barracks = self._install_barracks_button_sync(spec)
            except Exception as exc:
                try:
                    rolled_back = self._rollback_partial_preset_sync()
                    detail = ", ".join(rolled_back) if rolled_back else "no live writes remained"
                except Exception as rollback_exc:
                    detail = f"rollback also reported: {rollback_exc}"
                raise RuntimeError(f"{exc}\n\nThe partial live preset was rolled back ({detail}).") from exc
            return changed, unit_card, barracks, localized

        def done(result) -> None:
            self._refresh_compatibility()
            self._refresh_selected()
            localized = result[3]
            target = self._target_id()
            extra = ""
            if localized.get("changed"):
                extra = (
                    f"\n\nPatched {localized['changed']} language file(s) under Data\\Strings. "
                    "Restart Warcraft II once so the remaster reloads the registry stat_txt entries."
                )
            if target in UNUSED_CUSTOM_TARGETS:
                extra += (
                    f"\n\nWarning: Unit {target} is an experimental full-bootstrap unused slot. "
                    "Test selection, movement, attack, death, save/load, and production before combining it with other unused slots."
                )
            producer_note = (
                "No producer button was requested; use Native Test or triggers to create this unit."
                if self._producer_id() < 0 else
                "Its producer button, stable production eligibility, and guarded compatibility patch are active."
            )
            messagebox.showinfo(
                APP_TITLE,
                f"Unit {target} live preset installed.\n\n"
                "Gameplay tables, renderer/animation aliases, icon/portrait, status callbacks, donor sounds, "
                "and byte-verified command card are active. " + producer_note
                + extra,
                parent=self,
            )

        self._run_job("Installing complete live Unit Forge preset...", work, done)

    def _install_all_registry(self) -> None:
        self._sync_active_registry()
        try:
            specs = self._registry_validate(self.unit_registry)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return

        def work():
            self._require_live()
            localized = self._patch_installed_localizations()
            results = []
            try:
                for spec in specs:
                    target = int(spec["target_id"])
                    self._log(f"Installing registry Unit {target}: {spec['unit_name']}", "head")
                    changed = self._apply_live_unit_data_sync(spec)
                    card = self._install_unit_card_sync(spec)
                    producer = self._install_barracks_button_sync(spec)
                    results.append({"target": target, "changed": changed, "card": card, "producer": producer})
            except Exception as exc:
                try:
                    rolled_back = self._rollback_partial_preset_sync()
                    detail = ", ".join(rolled_back) if rolled_back else "no live writes remained"
                except Exception as rollback_exc:
                    detail = f"rollback also reported: {rollback_exc}"
                raise RuntimeError(f"Registry installation stopped at Unit {spec.get('target_id')}: {exc}\n\nAll registry live changes were rolled back ({detail}).") from exc
            return results, localized

        def done(result):
            results, localized = result
            self._refresh_compatibility()
            self._refresh_selected()
            spec_by_target = {int(spec["target_id"]): spec for spec in specs}
            summary = "\n".join(
                f"Unit {item['target']}: installed" +
                (" (no producer)" if int(spec_by_target[item['target']].get("producer_card_id", -1)) < 0 else "")
                for item in results
            )
            restart = "\n\nRestart Warcraft once to reload the changed Data\\Strings JSON files." if localized.get("changed") else ""
            messagebox.showinfo(APP_TITLE, f"Installed {len(results)} custom units.\n\n{summary}{restart}", parent=self)

        self._run_job(f"Installing {len(specs)} registry units...", work, done)

    def _restore_live_changes(self) -> None:
        def work():
            service, runtime = self._require_live()
            targets = {int(spec.get("target_id", self._target_id())) for spec in self.unit_registry}
            unit_pool = service.process.read_u32(service.process.base + UNIT_ARRAY_RVA) or 0
            max_units = service.process.read_u32(service.process.base + MAX_UNITS_RVA) or 0
            active_target = 0
            if unit_pool and 1 <= max_units <= 10000:
                pool = service.process.read(unit_pool, max_units * UNIT_RECORD_SIZE)
                if len(pool) == max_units * UNIT_RECORD_SIZE:
                    for offset in range(0, len(pool), UNIT_RECORD_SIZE):
                        if pool[offset + SELECTED_UNIT_TYPE_OFFSET] not in targets:
                            continue
                        health = struct.unpack_from("<H", pool, offset + 0x22)[0]
                        if health:
                            active_target += 1
            if active_target:
                raise RuntimeError(
                    f"Remove or kill the {active_target} active registry unit object(s) before restoring. "
                    "Restoring their animation group and graphics aliases while they are alive can crash Warcraft."
                )
            results = []
            if self.table_resolver:
                results.append(f"unit tables: {self.table_resolver.restore()}")
            restored_sequence_rows = runtime.restore_sequence_table_aliases()
            if restored_sequence_rows:
                results.append(f"action-sequence rows: {restored_sequence_rows}")
            if runtime.restore_sequence_alias():
                results.append("action-sequence hook")
            if runtime.restore_group_alias():
                results.append("unitGroup hook")
            if runtime.restore_custom_name():
                results.append("custom name table")
            restored_training = runtime.restore_training_metadata()
            if restored_training:
                results.append(f"training metadata: {restored_training}")
            restored_sounds = runtime.restore_sound_aliases()
            if restored_sounds:
                results.append(f"sound identity: {restored_sounds}")
            restored_ports = runtime.restore_portrait_aliases()
            if restored_ports:
                results.append(f"gPorts: {restored_ports}")
            card_ids = {int(spec.get("target_id", self._target_id())) for spec in self.unit_registry}
            card_ids.update(int(spec.get("producer_card_id", HUMAN_BARRACKS_CARD_ID)) for spec in self.unit_registry if int(spec.get("producer_card_id", HUMAN_BARRACKS_CARD_ID)) >= 0)
            for card_id in sorted(card_ids):
                if card_id in runtime.attach_descriptors:
                    runtime.restore_attach_state(card_id)
                    results.append(f"card 0x{card_id:02X}")
            if service.compatibility_bytes() == PRODUCER_MISMATCH_PATCH:
                service.set_compatibility(False)
            return results

        def done(result) -> None:
            self._refresh_compatibility()
            self._log("Restored live attach state: " + ", ".join(result), "good")

        self._run_job("Restoring live Unit Forge changes...", work, done)

    # ---------------------------------------------------------- Native test
    def _attach_test_engine(self) -> None:
        if os.name != "nt":
            messagebox.showerror(APP_TITLE, "Native testing is available only on Windows.", parent=self)
            return
        if self.test_adapter:
            self.test_var.set("Native test engine is already attached")
            return

        def work():
            runtime_dir = ENGINE / "live_runtime"
            if str(runtime_dir) not in sys.path:
                sys.path.insert(0, str(runtime_dir))
            from live import LiveAdapter
            from engine import ActionDeferred
            adapter = LiveAdapter(scenario=None, logger=self._test_log)
            adapter.attach()
            return adapter, ActionDeferred

        def done(result) -> None:
            self.test_adapter, self.test_action_deferred = result
            self.test_var.set(
                f"Attached | base 0x{self.test_adapter.base:08X} | unit_create RVA "
                f"0x{self.test_adapter.unit_create_address - self.test_adapter.base:08X} | "
                f"map {self.test_adapter.map_width}x{self.test_adapter.map_height}"
            )
            self._test_log("Native Unit Forge test engine attached successfully.")

        self._run_job("Attaching embedded native simulation test engine...", work, done)

    def _create_test_units(self) -> None:
        if not self.test_adapter:
            messagebox.showerror(APP_TITLE, "Attach the native test engine first.", parent=self)
            return
        owner = int(self.owner_var.get())
        try:
            target = self._validate_live_target()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return
        x = int(self.x_var.get())
        y = int(self.y_var.get())
        amount = int(self.amount_var.get())

        # The renderer indexes its active graphics-pointer table by the live unitGroup byte.
        # Refuse creation rather than reproducing the Unit 34 NULL-pointer crash
        # when the complete live runtime preset is missing.
        try:
            service, runtime = self._require_live()
            resolver = self._ensure_tables_sync()
            expected_hp = resolver.read_value("gwUnitHPTbl", target)
            if expected_hp <= 0:
                raise RuntimeError(
                    f"Unit {target} live HP table value is {expected_hp}. Reinstall the complete preset; "
                    "Forge will not create a 1-HP/incomplete unit."
                )
            descriptor_count, descriptor_pointer = runtime.unpack_descriptor(runtime.read_descriptor(target))
            if descriptor_count <= 0 or descriptor_pointer == 0:
                raise RuntimeError(
                    f"Unit {target} command card is blank. Install the complete live preset before creation."
                )
            _icon, _frame, _string, _pad, query_cb, draw_cb = runtime.read_portrait_identity(target)
            if not query_cb or not draw_cb:
                raise RuntimeError(
                    f"Unit {target} status identity is blank. Install the complete live preset before creation."
                )
            graphics_address = service.process.base + UNIT_GRAPHICS_POINTER_TABLE_RVA + target * 4
            graphics_raw = service.process.read(graphics_address, 4)
            graphics_pointer = struct.unpack("<I", graphics_raw)[0] if len(graphics_raw) == 4 else 0
            if graphics_pointer == 0 or len(service.process.read(graphics_pointer, 4)) != 4:
                raise RuntimeError(
                    f"Unit {target} has no readable active graphics descriptor. "
                    "Use INSTALL CURRENT UNIT or INSTALL ALL REGISTRY UNITS before creating it."
                )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return

        def work():
            created = []
            for index in range(amount):
                px = x + (index % 4)
                py = y + (index // 4)
                deadline = time.monotonic() + 10.0
                while True:
                    try:
                        unit = self.test_adapter._create_unit(owner, target, px, py)
                        break
                    except self.test_action_deferred as exc:
                        if time.monotonic() >= deadline:
                            raise RuntimeError(f"Timed out waiting for the native dispatcher: {exc}") from exc
                        time.sleep(exc.retry_after)
                if unit is None:
                    raise RuntimeError(f"Warcraft unit_create could not place Unit {target} near ({px},{py}).")
                identity = service.process.read(unit.address, 0x30)
                if len(identity) != 0x30:
                    raise RuntimeError("Could not read the created unit identity fields.")
                card_id = identity[SELECTED_CARD_ID_OFFSET]
                unit_type = identity[SELECTED_UNIT_TYPE_OFFSET]
                unit_class = identity[0x2A]
                unit_group = identity[SELECTED_UNIT_GROUP_OFFSET]
                current_action = identity[0x2E]
                next_action = identity[0x2F]
                sequence = struct.unpack_from("<H", identity, 0x04)[0]
                if unit_type != target or card_id != target:
                    raise RuntimeError(
                        f"Created object identity mismatch: type {unit_type}, card {card_id}; expected {target}."
                    )
                if unit.health != expected_hp:
                    raise RuntimeError(
                        f"Created Unit {target} has HP {unit.health}; expected live table HP {expected_hp}. "
                        "The unit was created from an incomplete runtime row."
                    )
                if unit_group != self._graphics_id():
                    raise RuntimeError(
                        f"Created Unit {target} has animation group {unit_group}; expected graphics group "
                        f"{self._graphics_id()}. Reinstall the complete live preset before testing."
                    )
                created.append(unit)
                self._test_log(
                    f"Created Unit {target} for P{owner + 1}: slot {(unit.address-self.test_adapter.unit_pool)//UNIT_RECORD_SIZE}, "
                    f"address 0x{unit.address:08X}, tile ({unit.x},{unit.y}), HP {unit.health}; "
                    f"card {card_id}, class {unit_class}, group {unit_group}, action "
                    f"{current_action}/{next_action}, sequence 0x{sequence:04X}."
                )
            return created

        def done(result) -> None:
            extra = ""
            if owner == 0:
                extra = "\n\nThese are P1 units. Use force attack to target them with another P1 unit, or create a P8 target for a normal attack order."
            messagebox.showinfo(
                APP_TITLE,
                f"Created and verified {len(result)} Unit {target} object(s)." + extra,
                parent=self,
            )

        self._run_job(f"Creating {amount} native Unit {target} object(s)...", work, done)

    def _queue_selected_barracks(self) -> None:
        if not self.test_adapter:
            messagebox.showerror(APP_TITLE, "Attach the native test engine first.", parent=self)
            return
        try:
            service, runtime = self._require_live()
            target = self._validate_live_target()
            resolver = self._ensure_tables_sync()
            expected_hp = resolver.read_value("gwUnitHPTbl", target)
            if expected_hp <= 0:
                raise RuntimeError(f"Unit {target} has invalid live HP {expected_hp}; install the complete preset first.")
            count, pointer = runtime.unpack_descriptor(runtime.read_descriptor(target))
            if count <= 0 or pointer == 0:
                raise RuntimeError(f"Unit {target} command card is blank; install the complete preset first.")
            selected = service.process.read_u32(service.process.base + SELECTED_OBJECT_POINTER_RVA) or 0
            unit_pool = service.process.read_u32(service.process.base + UNIT_ARRAY_RVA) or 0
            max_units = service.process.read_u32(service.process.base + MAX_UNITS_RVA) or 0
            if not unit_pool or not (1 <= max_units <= 10000):
                raise RuntimeError("The live unit pool is not available.")
            if not (unit_pool <= selected < unit_pool + max_units * UNIT_RECORD_SIZE):
                raise RuntimeError("Select the configured producer building in Warcraft before using this test.")
            record = service.process.read(selected, UNIT_RECORD_SIZE)
            if len(record) != UNIT_RECORD_SIZE:
                raise RuntimeError("Could not read the selected Warcraft unit record.")
            unit_type = record[SELECTED_UNIT_TYPE_OFFSET]
            owner = record[SELECTED_OWNER_OFFSET]
            expected_producer = self._producer_id()
            if expected_producer < 0:
                raise RuntimeError(f"Unit {target} is configured without a producer. Use Native Test or assign a Barracks producer in Unit Editor.")
            if unit_type != expected_producer:
                expected_name = PRODUCER_OPTIONS.get(expected_producer, {"name": str(expected_producer)})["name"]
                raise RuntimeError(
                    f"Selected Unit {unit_type} ({UNIT_NAMES[unit_type] if unit_type < len(UNIT_NAMES) else 'unknown'}) is not the configured {expected_name} ({expected_producer})."
                )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return

        def work():
            producer_address = service.process.base + TRAINING_PRODUCER_TABLE_RVA + target
            callback_address = service.process.base + TRAINING_ELIGIBILITY_TABLE_RVA + target * 4
            producer = service.process.read_u8(producer_address)
            callback = service.process.read_u32(callback_address) or 0
            before = service.process.read(selected, UNIT_RECORD_SIZE)
            if len(before) != UNIT_RECORD_SIZE:
                raise RuntimeError("Could not read the selected producer before production.")
            busy_before = bool(struct.unpack_from("<H", before, 0x1C)[0] & 0x10)
            complete_before = bool(before[0x1E] & 0x80)
            if busy_before:
                raise RuntimeError("The selected producer building is already training, upgrading, or researching.")
            if not complete_before:
                raise RuntimeError("The selected producer building is not complete yet.")
            if producer != self._producer_id():
                raise RuntimeError(
                    f"Unit {target} producer metadata is 0x{int(producer or 0):02X}, not selected producer 0x{self._producer_id():02X}. "
                    "Reinstall the complete live preset."
                )
            if not callback:
                raise RuntimeError(f"Unit {target} has no native production eligibility callback.")

            deadline = time.monotonic() + 10.0
            eligibility = None
            while True:
                try:
                    eligibility = self.test_adapter._call_cdecl(callback, [selected])
                    result = self.test_adapter._call_cdecl(
                        self.test_adapter.source_native_paths["bldg_build_start"],
                        [selected, target & 0xFF, 0],
                    )
                    break
                except self.test_action_deferred as exc:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"Timed out waiting for the native production dispatcher: {exc}") from exc
                    time.sleep(exc.retry_after)
            state = service.process.read(selected + 0x1C, 0x56)
            if len(state) != 0x56:
                raise RuntimeError("Could not verify the Barracks production state.")
            flags = struct.unpack_from("<H", state, 0)[0]
            order = state[0x6C - 0x1C]
            parameter = state[0x6D - 0x1C]
            progress = struct.unpack_from("<H", state, 0x6E - 0x1C)[0]
            total = struct.unpack_from("<H", state, 0x70 - 0x1C)[0]
            started = bool(result and (flags & 0x10) and order == 0 and parameter == target and total > 0)
            if not started:
                # The bytes at +6C..+70 are stale when bldg_build_start returns
                # before queue initialization; do not misreport them as the
                # argument that was passed to the function.
                raise RuntimeError(
                    f"Warcraft rejected native production before queue initialization: return={result}, "
                    f"eligibility={eligibility}, complete={complete_before}, busy={busy_before}, "
                    f"producer=0x{int(producer or 0):02X}, callback=0x{callback:08X}. "
                    f"Stale queue bytes were order={order}, parameter={parameter}, progress={progress}, total={total}."
                )
            return owner, flags, order, parameter, progress, total

        def done(result) -> None:
            owner, flags, order, parameter, progress, total = result
            self._test_log(
                f"Native producer queue accepted Unit {parameter} for P{owner + 1}: "
                f"flags 0x{flags:04X}, order {order}, progress {progress}/{total}."
            )
            messagebox.showinfo(
                APP_TITLE,
                f"Warcraft accepted Unit {parameter} in the selected producer building queue.\n\n"
                "This proves the target production row is valid. The normal command-card button should use the same native path.",
                parent=self,
            )

        self._run_job(f"Queuing Unit {target} in the selected producer building...", work, done)

    def _create_enemy_test_unit(self) -> None:
        previous_owner = int(self.owner_var.get())
        previous_amount = int(self.amount_var.get())
        self.owner_var.set(7)
        self.amount_var.set(1)
        try:
            self._create_test_units()
        finally:
            self.after(250, lambda: self.owner_var.set(previous_owner))
            self.after(250, lambda: self.amount_var.set(previous_amount))

    def _count_test_units(self) -> None:
        if not self.test_adapter:
            messagebox.showerror(APP_TITLE, "Attach the native test engine first.", parent=self)
            return
        try:
            target = self._target_id()
            units = [unit for unit in self.test_adapter.units() if unit.unit_type == target]
            lines = [f"Active Unit {target} count: {len(units)}"]
            service, _runtime = self._require_live()
            for unit in units[:100]:
                identity = service.process.read(unit.address, 0x30)
                card_id = identity[SELECTED_CARD_ID_OFFSET] if len(identity) == 0x30 else -1
                unit_group = identity[SELECTED_UNIT_GROUP_OFFSET] if len(identity) == 0x30 else -1
                lines.append(
                    f"P{unit.owner + 1} slot {(unit.address-self.test_adapter.unit_pool)//UNIT_RECORD_SIZE} "
                    f"0x{unit.address:08X} ({unit.x},{unit.y}) HP {unit.health} card {card_id} "
                    f"group {unit_group} action {unit.action}/{unit.next_action}"
                )
            self._test_log("\n".join(lines))
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)

    # --------------------------------------------------------------- Cleanup
    def _on_close(self) -> None:
        if self.busy_count and not messagebox.askyesno(APP_TITLE, "A Unit Forge operation is still running. Close anyway?", parent=self):
            return
        try:
            self._save_project(silent=True)
        except Exception:
            pass
        if self.test_adapter:
            try:
                self.test_adapter.close()
            except Exception as exc:
                self._log(f"Native test cleanup warning: {exc}", "warn")
        if self.card_service:
            try:
                self.card_service.close()
            except Exception:
                pass
        self.destroy()


def run_self_test() -> int:
    checks = [
        ("unit-data row count", len(UNIT_NAMES) == 110, len(UNIT_NAMES)),
        ("native trainable range", TRAINABLE_UNIT_LIMIT == 58, TRAINABLE_UNIT_LIMIT),
        ("unit-data descriptor count", len(UNITDATA_DESCRIPTOR_RVAS) == UNITDATA_DESCRIPTOR_COUNT == 32, len(UNITDATA_DESCRIPTOR_RVAS)),
        ("editable runtime bindings", len(SOURCE_DESCRIPTOR_BINDINGS) == 34, len(SOURCE_DESCRIPTOR_BINDINGS)),
        ("multi-unit starter targets", SAFE_CUSTOM_TARGETS == (16, 17), SAFE_CUSTOM_TARGETS),
        ("always-trainable callback", RuntimeCards.build_always_trainable_callback() == ALWAYS_TRAINABLE_CALLBACK, RuntimeCards.build_always_trainable_callback().hex(" ")),
        ("runtime project format", PROJECT_VERSION >= 8, PROJECT_VERSION),
    ]
    failed = 0
    for label, passed, detail in checks:
        print(("PASS" if passed else "FAIL") + f" | {label} | {detail}")
        failed += 0 if passed else 1
    print(f"Self-test complete: {len(checks)-failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(run_self_test())
    UnitForge().mainloop()
