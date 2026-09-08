from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

SCHEMA = "war2-trigger-sidecar"
VERSION = 2
SUPPORTED_VERSIONS = {1, 2}


@dataclass
class Location:
    name: str
    left: int
    top: int
    right: int
    bottom: int

    def normalize(self) -> None:
        self.left, self.right = sorted((int(self.left), int(self.right)))
        self.top, self.bottom = sorted((int(self.top), int(self.bottom)))


@dataclass
class Clause:
    kind: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class Trigger:
    name: str = "New Trigger"
    players: list[int] = field(default_factory=lambda: [0])
    conditions: list[Clause] = field(default_factory=list)
    actions: list[Clause] = field(default_factory=list)
    preserved: bool = False
    enabled: bool = True
    comment: str = ""
    # All = classic StarCraft AND behavior. Any enables an OR-style trigger.
    condition_mode: str = "All"
    # Trigger-level timing is handled by TriggerEngine. It never blocks Warcraft's
    # main thread and is safe to use with delayed action sequences.
    start_delay: float = 0.0
    repeat_interval: float = 1.0
    max_runs: int = 1  # 0 means unlimited; only used when preserved is true.


@dataclass
class Scenario:
    map_file: str = ""
    locations: list[Location] = field(default_factory=list)
    triggers: list[Trigger] = field(default_factory=list)
    # These are initial values. The live runtime owns mutable copies while a run
    # is active. Keeping them in the sidecar makes RPG/defense maps portable.
    variables: dict[str, Any] = field(default_factory=dict)
    timers: dict[str, float] = field(default_factory=dict)
    forces: dict[str, list[int]] = field(default_factory=dict)
    objectives: dict[str, str] = field(default_factory=dict)

    def validate(self) -> list[str]:
        errors: list[str] = []
        names: set[str] = set()
        for i, loc in enumerate(self.locations):
            loc.normalize()
            if not loc.name.strip():
                errors.append(f"Location {i + 1} has no name")
            if loc.name.casefold() in names:
                errors.append(f"Duplicate location: {loc.name}")
            names.add(loc.name.casefold())
        trigger_names: set[str] = set()
        for i, trig in enumerate(self.triggers):
            if not trig.name.strip():
                errors.append(f"Trigger {i + 1} has no name")
            folded = trig.name.strip().casefold()
            if folded in trigger_names:
                errors.append(f"Duplicate trigger name: {trig.name}")
            trigger_names.add(folded)
            if not trig.players:
                errors.append(f"{trig.name}: no executing players")
            if not trig.conditions:
                errors.append(f"{trig.name}: no conditions (use Always explicitly)")
            if not trig.actions:
                errors.append(f"{trig.name}: no actions")
            if str(trig.condition_mode) not in {"All", "Any"}:
                errors.append(f"{trig.name}: condition mode must be All or Any")
            if float(trig.start_delay) < 0:
                errors.append(f"{trig.name}: start delay cannot be negative")
            if float(trig.repeat_interval) < 0:
                errors.append(f"{trig.name}: repeat interval cannot be negative")
            if int(trig.max_runs) < 0:
                errors.append(f"{trig.name}: max runs cannot be negative")
            if trig.preserved and float(trig.repeat_interval) == 0:
                errors.append(
                    f"{trig.name}: repeating with a zero-second interval would run every engine cycle; "
                    "use at least 0.25 seconds"
                )
        for name, players in self.forces.items():
            if not str(name).strip():
                errors.append("A Force has no name")
            if not players:
                errors.append(f"Force {name}: no players")
            if any(not 0 <= int(player) <= 15 for player in players):
                errors.append(f"Force {name}: player outside P1-P16")

        location_names = {loc.name for loc in self.locations}
        trigger_names_exact = {trig.name.strip().casefold() for trig in self.triggers}
        location_fields = {
            "location", "source_location", "destination", "spawn_location",
            "count_location", "attacker_location", "caster_location",
            "target_location", "unit_location", "retreat_location",
            "anchor_location", "start_location",
        }
        trigger_ref_kinds = {"Enable Trigger", "Disable Trigger", "Toggle Trigger", "Reset Trigger", "Run Trigger", "Trigger Enabled", "Run Trigger For Each Unit In Group", "Run Trigger For Each Player", "Call Trigger Function"}
        for trig in self.triggers:
            for clause in [*trig.conditions, *trig.actions]:
                removed_shop_kinds = {
                    "Shop Item Stock", "Register Shop Item", "Set Shop Stock",
                    "Buy Shop Item", "Sell Inventory Item", "Use Inventory Item",
                }
                if clause.kind in removed_shop_kinds:
                    errors.append(
                        f"{trig.name}: {clause.kind} was removed in Trigger Studio 1.28.2; "
                        "use direct inventory, equipment, currency, and hero-attribute actions instead"
                    )
                if clause.kind in trigger_ref_kinds:
                    target = str(clause.args.get("trigger", "")).strip()
                    if not target or target.casefold() not in trigger_names_exact:
                        errors.append(f"{trig.name}: {clause.kind} references unknown trigger {target!r}")
                for field_name in location_fields:
                    if field_name not in clause.args:
                        continue
                    value = str(clause.args.get(field_name, "Anywhere"))
                    if value and value != "Anywhere" and value not in location_names:
                        errors.append(f"{trig.name}: {clause.kind} references unknown location {value!r}")
                source_destination_point_kinds = {
                    "Source Attack Area", "Source Attack Ground", "Source Attack Wall",
                    "Source Patrol Move", "Source Demolish", "Source Harvest",
                    "Source Unload All", "Source Find Walkable Point",
                    "Source Find Buildable Point", "Source Location Walkable",
                    "Source Location Buildable", "Source Play Explosion Sound At Point",
                    "Source Create Projectile At Point", "Source Create Explosion Projectile",
                    "Source Order Worker To Build", "Source Place Building Foundation",
                    "Source Find Nearest Building Site", "Source Find Shore Point",
                    "Source Find Dock Point", "Source Find Undock Point",
                    "Source Reveal Radius", "Source Begin Tree Harvest",
                    "Source Tree Is Reachable", "Source Unit Can Reach Location",
                    "Source Locations On Same Island", "Source Trigger Rune",
                    "Source Set Rune Lifetime", "Source Remove Rune", "Source Place Rune",
                    "Source Projectile Hit Location", "Source Set Projectile Target",
                    "Source Create Tower Projectile", "Source Create Fire Projectile",
                    "Source Create Flame Spin", "Source Create Black X Marker",
                    "Source Create Stationary Projectile", "Source Show Minimap Marker",
                    "Source Set Local Camera",
                }
                source_location_point_kinds = {
                    "Source Tile Value", "Source Tile Is Tree", "Source Tile Is Rock",
                    "Source Tile Is Wall", "Source Tile Is Demolishable",
                    "Source Location Is Visible", "Source Location Is Explored",
                    "Source Tile Region Type", "Source Wall Is Connected",
                    "Source Rune Exists At Location", "Source Rune Owner",
                    "Source Rune Lifetime",
                }
                source_terrain_kinds = {
                    "Source Set Tiles", "Source Remove Trees", "Source Remove Rocks",
                    "Source Place Walls", "Source Destroy Walls",
                    "Source Damage Terrain", "Source Native Demolish Tile",
                    "Source Remove Terrain Region", "Source Rebuild Wall Connections",
                    "Source Rebuild Shore Regions", "Source Refresh Terrain Pathing",
                    "Source Reveal Area For Player", "Source Find Reachable Tree",
                    "Source Find Nearest Reachable Tree",
                }
                source_filtered_tile_kinds = {
                    "Source Tile Is Tree", "Source Tile Is Rock", "Source Tile Is Wall",
                    "Source Tile Is Demolishable", "Source Remove Trees",
                    "Source Remove Rocks", "Source Destroy Walls",
                    "Source Find Reachable Tree", "Source Find Nearest Reachable Tree",
                }
                if clause.kind in source_destination_point_kinds:
                    destination = str(clause.args.get("destination", "Anywhere"))
                    if destination == "Anywhere" and ("x" not in clause.args or "y" not in clause.args):
                        errors.append(f"{trig.name}: {clause.kind} at Anywhere requires X and Y")
                if clause.kind in source_location_point_kinds:
                    destination = str(clause.args.get("location", "Anywhere"))
                    if destination == "Anywhere" and ("x" not in clause.args or "y" not in clause.args):
                        errors.append(f"{trig.name}: {clause.kind} at Anywhere requires X and Y")
                if clause.kind in source_terrain_kinds:
                    destination = str(clause.args.get("location", "Anywhere"))
                    if destination == "Anywhere" and ("x" not in clause.args or "y" not in clause.args):
                        errors.append(f"{trig.name}: {clause.kind} at Anywhere requires top-left X and Y")
                    try:
                        if int(clause.args.get("width", 1)) <= 0 or int(clause.args.get("height", 1)) <= 0:
                            errors.append(f"{trig.name}: {clause.kind} width and height must be positive")
                    except (TypeError, ValueError):
                        errors.append(f"{trig.name}: {clause.kind} width and height must be integers")
                if clause.kind in source_filtered_tile_kinds:
                    raw_tiles = str(clause.args.get("tile_values", "")).strip()
                    if not raw_tiles:
                        errors.append(f"{trig.name}: {clause.kind} requires one or more MTXM Tile values")
                    else:
                        try:
                            [int(part.strip(), 0) for part in raw_tiles.replace(";", ",").split(",") if part.strip()]
                        except ValueError:
                            errors.append(f"{trig.name}: {clause.kind} Tile values must be decimal or 0x-prefixed integers")
                if clause.kind == "Source Set Production Progress":
                    try:
                        percent = int(clause.args.get("percent", 50))
                        if not 0 <= percent <= 100:
                            errors.append(f"{trig.name}: Source Set Production Progress percent must be 0-100")
                    except (TypeError, ValueError):
                        errors.append(f"{trig.name}: Source Set Production Progress percent must be an integer")
                if clause.kind in {"Source Play Animation", "Source Freeze Animation"}:
                    for key, high in (("animation", 255), ("frame", 255), ("facing", 7)):
                        if key not in clause.args or clause.args.get(key) in (None, ""):
                            continue
                        try:
                            value = int(clause.args[key])
                            if not 0 <= value <= high:
                                errors.append(f"{trig.name}: {clause.kind} {key} must be 0-{high}")
                        except (TypeError, ValueError):
                            errors.append(f"{trig.name}: {clause.kind} {key} must be an integer")
                if clause.kind == "Create Wave":
                    if not str(clause.args.get("amount_expression", "")).strip():
                        errors.append(f"{trig.name}: Create Wave amount expression is blank")
                    if str(clause.args.get("order", "Attack")) != "None":
                        destination = str(clause.args.get("destination", "Anywhere"))
                        if destination == "Anywhere" and ("destination_x" not in clause.args or "destination_y" not in clause.args):
                            errors.append(f"{trig.name}: Create Wave at Anywhere requires destination X and Y")
                if clause.kind in {"Create Units", "Create Completed Buildings"}:
                    if str(clause.args.get("location", "Anywhere")) == "Anywhere" and ("x" not in clause.args or "y" not in clause.args):
                        errors.append(f"{trig.name}: {clause.kind} at Anywhere requires X and Y")
                if clause.kind == "Create Sapper Assault":
                    if str(clause.args.get("spawn_location", "Anywhere")) == "Anywhere" and ("spawn_x" not in clause.args or "spawn_y" not in clause.args):
                        errors.append(f"{trig.name}: Create Sapper Assault at Anywhere requires spawn X and Y")
                    unit = clause.args.get("unit", 14)
                    try:
                        unit = int(unit)
                    except (TypeError, ValueError):
                        unit = -1
                    if unit not in {14, 15}:
                        errors.append(f"{trig.name}: Create Sapper Assault unit must be Dwarves (14) or Goblins (15)")
                if clause.kind == "Order Sappers Demolish":
                    if str(clause.args.get("destination", "Anywhere")) == "Anywhere" and ("x" not in clause.args or "y" not in clause.args):
                        errors.append(f"{trig.name}: Order Sappers Demolish at Anywhere requires X and Y")
                if clause.kind == "Start Wave Director":
                    if str(clause.args.get("spawn_location", "Anywhere")) == "Anywhere" and ("spawn_x" not in clause.args or "spawn_y" not in clause.args):
                        errors.append(f"{trig.name}: Start Wave Director at Anywhere requires spawn X and Y")
                    if str(clause.args.get("destination", "Anywhere")) == "Anywhere" and ("destination_x" not in clause.args or "destination_y" not in clause.args):
                        errors.append(f"{trig.name}: Start Wave Director at Anywhere requires destination X and Y")
                if clause.kind == "Start Boss Controller":
                    raw = clause.args.get("phases_json", "[]")
                    try:
                        phases = json.loads(raw) if isinstance(raw, str) else raw
                        if not isinstance(phases, list):
                            raise TypeError
                    except Exception:
                        errors.append(f"{trig.name}: Start Boss Controller phases_json must be a JSON list")
                if clause.kind == "Call Trigger Function":
                    target_name = str(clause.args.get("trigger", "")).strip().casefold()
                    target = next((candidate for candidate in self.triggers if candidate.name.strip().casefold() == target_name), None)
                    if target is not None:
                        forbidden = [action.kind for action in target.actions if action.kind in {"Wait", "Random Wait", "Breakpoint"}]
                        if forbidden:
                            errors.append(f"{trig.name}: function {target.name!r} contains asynchronous action {forbidden[0]}")
                    raw = clause.args.get("arguments_json", "{}")
                    try:
                        value = json.loads(raw) if isinstance(raw, str) else raw
                        if not isinstance(value, dict):
                            raise TypeError
                    except Exception:
                        errors.append(f"{trig.name}: Call Trigger Function arguments_json must be a JSON object")
                if clause.kind == "Enable Aura":
                    anchor_mode = str(clause.args.get("anchor_mode", "Unit Reference"))
                    target_mode = str(clause.args.get("target_mode", "Named Unit Group" if str(clause.args.get("group", "")).strip() else "Matching Units"))
                    if anchor_mode == "Location Center" and str(clause.args.get("anchor_location", "Anywhere")) == "Anywhere":
                        errors.append(f"{trig.name}: Enable Aura Location Center requires a named anchor location")
                    if target_mode == "Named Unit Group" and not str(clause.args.get("group", "")).strip():
                        errors.append(f"{trig.name}: Enable Aura Named Unit Group requires a group name")
                    if target_mode == "Exact Unit Reference" and not str(clause.args.get("target_reference", "")).strip():
                        errors.append(f"{trig.name}: Enable Aura Exact Unit Reference requires a target reference")
                    if str(clause.args.get("effect", "Health Regeneration")) == "Damage" and anchor_mode != "Unit Reference":
                        errors.append(f"{trig.name}: Enable Aura Damage requires Unit Reference anchor mode")
                    try:
                        radius = int(clause.args.get("radius", 6))
                        maximum_targets = int(clause.args.get("maximum_targets", 128))
                        if not 0 <= radius <= 64:
                            errors.append(f"{trig.name}: Enable Aura radius must be 0-64")
                        if not 1 <= maximum_targets <= 512:
                            errors.append(f"{trig.name}: Enable Aura maximum targets must be 1-512")
                    except (TypeError, ValueError):
                        errors.append(f"{trig.name}: Enable Aura radius and maximum targets must be integers")
                if clause.kind == "Start Vote":
                    raw = clause.args.get("options", "[]")
                    try:
                        value = json.loads(raw) if isinstance(raw, str) and raw.strip().startswith("[") else [item.strip() for item in str(raw).split(",") if item.strip()]
                        if not isinstance(value, list) or len(value) < 2:
                            raise TypeError
                    except Exception:
                        errors.append(f"{trig.name}: Start Vote requires at least two options")
                if clause.kind == "Start Reinforcement Director":
                    raw = clause.args.get("stages_json", "[]")
                    try:
                        stages = json.loads(raw) if isinstance(raw, str) else raw
                        if not isinstance(stages, list) or any(not isinstance(stage, dict) for stage in stages):
                            raise TypeError
                    except Exception:
                        errors.append(f"{trig.name}: Start Reinforcement Director stages_json must be a JSON list of objects")
                        stages = []
                    for stage_index, stage in enumerate(stages):
                        waves = stage.get("waves", [])
                        if not isinstance(waves, list):
                            errors.append(f"{trig.name}: reinforcement stage {stage_index + 1} waves must be a list")
                            continue
                        for wave_index, wave in enumerate(waves):
                            if not isinstance(wave, dict):
                                errors.append(f"{trig.name}: reinforcement stage {stage_index + 1} wave {wave_index + 1} must be an object")
                                continue
                            for field_name in ("spawn_location", "destination"):
                                location = str(wave.get(field_name, "Anywhere"))
                                if location != "Anywhere" and location not in location_names:
                                    errors.append(f"{trig.name}: reinforcement stage {stage_index + 1} wave {wave_index + 1} references unknown location {location!r}")
                            if str(wave.get("spawn_location", "Anywhere")) == "Anywhere" and ("spawn_x" not in wave or "spawn_y" not in wave):
                                errors.append(f"{trig.name}: reinforcement stage {stage_index + 1} wave {wave_index + 1} at Anywhere requires spawn_x and spawn_y")
                            if str(wave.get("order", "Attack")) != "None" and str(wave.get("destination", "Anywhere")) == "Anywhere" and ("destination_x" not in wave or "destination_y" not in wave):
                                errors.append(f"{trig.name}: reinforcement stage {stage_index + 1} wave {wave_index + 1} requires destination_x and destination_y")
        return errors

    def warnings(self) -> list[str]:
        warnings: list[str] = []
        for trig in self.triggers:
            called = any(action.kind == "Run Trigger" for action in trig.actions)
            if called and not trig.preserved and trig.max_runs == 1:
                pass
            if "spawn" in trig.name.casefold() and not any(action.kind in {"Create Units", "Create Completed Buildings", "Create Wave", "Create Units At Event", "Create Sapper Assault", "Start Wave Director", "Train Units Instantly At Buildings", "Start Reinforcement Director"} for action in trig.actions):
                warnings.append(f"{trig.name}: name suggests spawning, but it contains no creation action")
            if any(condition.kind in {"Unit Created", "Unit Damaged", "Unit Healed", "Unit Under Attack", "Command", "Bring"} for condition in trig.conditions):
                if not any(action.kind in {"Create Units", "Create Completed Buildings", "Create Wave", "Create Units At Event", "Create Sapper Assault", "Start Wave Director", "Train Units Instantly At Buildings", "Start Reinforcement Director"} for source in self.triggers for action in source.actions):
                    warnings.append(f"{trig.name}: waits for live units, but this sidecar contains no unit-creation action; it depends on units already present in the map")
        return sorted(set(warnings))

    def save(self, path: str | Path) -> None:
        payload = {"schema": SCHEMA, "version": VERSION, **asdict(self)}
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Scenario":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        version = int(raw.get("version", 1))
        if raw.get("schema") != SCHEMA or version not in SUPPORTED_VERSIONS:
            raise ValueError("Unsupported Warcraft II trigger sidecar schema/version")

        triggers: list[Trigger] = []
        for item in raw.get("triggers", []):
            preserved = bool(item.get("preserved", False))
            # Old sidecars had only Preserve Trigger. Preserve their old behavior:
            # preserved triggers repeat forever, one-shot triggers run once.
            default_max_runs = 0 if preserved else 1
            triggers.append(Trigger(
                name=item.get("name", "New Trigger"),
                players=[int(x) for x in item.get("players", [0])],
                conditions=[Clause(**x) for x in item.get("conditions", [])],
                actions=[Clause(**x) for x in item.get("actions", [])],
                preserved=preserved,
                enabled=bool(item.get("enabled", True)),
                comment=item.get("comment", ""),
                condition_mode=str(item.get("condition_mode", "All")),
                start_delay=float(item.get("start_delay", 0.0)),
                repeat_interval=float(item.get("repeat_interval", 1.0)),
                max_runs=int(item.get("max_runs", default_max_runs)),
            ))
        return cls(
            map_file=raw.get("map_file", ""),
            locations=[Location(**x) for x in raw.get("locations", [])],
            triggers=triggers,
            variables=dict(raw.get("variables", {})),
            timers={str(k): float(v) for k, v in dict(raw.get("timers", {})).items()},
            forces={str(k): [int(x) for x in v] for k, v in dict(raw.get("forces", {})).items()},
            objectives={str(k): str(v) for k, v in dict(raw.get("objectives", {})).items()},
        )
