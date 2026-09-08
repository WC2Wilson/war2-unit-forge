from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import random
import struct
import time
from typing import Any

from massive_features import (
    MASSIVE_UNHANDLED,
    MassiveFeatureMixin,
    ORDER_ACTIONS,
    STATUS_OFFSETS,
)


# Warcraft II source spell bit order. These are the bits stored in glSpells and
# glSpellsAllowed for each player. The gaps in the original source enum are
# intentionally preserved by the explicit indices below.
SPELL_BITS: dict[str, int] = {
    "Holy Vision": 0,
    "Healing": 1,
    "Area Heal": 2,
    "Exorcism": 3,
    "Flame Shield": 4,
    "Fireball": 5,
    "Slow": 6,
    "Invisibility": 7,
    "Polymorph": 8,
    "Blizzard": 9,
    "Eye of Kilrogg": 10,
    "Bloodlust": 11,
    "Hallucinate": 12,
    "Raise Dead": 13,
    "Death Coil": 14,
    "Whirlwind": 15,
    "Haste": 16,
    "Unholy Armor": 17,
    "Runes": 18,
    "Death and Decay": 19,
    "Paladin / Ogre-Mage Conversion": 20,
}
SPELL_MASK = sum(1 << bit for bit in SPELL_BITS.values())

# Source sgbTechTbl row order. Each row contains one byte per player.
UPGRADE_ROWS: dict[str, int] = {
    "Ranged Attack": 0,
    "Melee Attack": 1,
    "Armor": 2,
    "Ship Attack": 3,
    "Ship Armor": 4,
    "Ship Speed": 5,
    "Siege Damage": 6,
    "Ranger / Berserker": 7,
    "Longbow / Light Axes": 8,
    "Scouting": 9,
    "Marksmanship / Regeneration": 10,
}

SAPPER_TYPES = {14, 15}
MOBILE_MAX = 57
DEAD_FLAG_MASK = 0x0007
HIDDEN_FLAG = 0x0008


@dataclass
class TimedEffect:
    kind: str
    expires_at: float
    amount: float = 0.0
    interval: float = 1.0
    next_tick: float = 0.0
    source_ref: str = ""
    radius: int = 0
    chance: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class WaveDirectorState:
    name: str
    owner: int
    unit_pool: list[int]
    spawn_location: str
    spawn_x: int
    spawn_y: int
    destination: str
    destination_x: int
    destination_y: int
    base_count: int
    growth: int
    intermission: float
    max_waves: int
    boss_every: int
    boss_unit: int
    formation: str
    spacing: int
    group_prefix: str
    wave: int = 0
    active: bool = True
    waiting_until: float = 0.0
    current_group: str = ""
    completed: bool = False


@dataclass
class BossControllerState:
    name: str
    reference: str
    phases: list[dict[str, Any]]
    current_phase: int = 0
    active: bool = True


class UltimateFeatureMixin(MassiveFeatureMixin):
    """1.25 systems layered above the proven 1.24.7 runtime.

    This mixin deliberately leaves WT30 vision, the simulation dispatcher,
    attack-route acquisition, even-aligned placement, and atomic auto-spells in
    the base adapter. New systems use exact allocation tokens and existing native
    primitives so stale unit records are never retained as raw pointers.
    """

    def _init_massive_features(self) -> None:
        super()._init_massive_features()
        self.unit_references: dict[str, tuple[int, int]] = {}
        self.player_spell_grants: dict[int, set[str]] = {}
        self.player_upgrade_levels: dict[int, dict[str, int]] = {}
        self.player_tech_flags: dict[int, set[str]] = {}
        self._progression_tables: dict[str, int] | None = None
        self._progression_resolution_error: str = ""
        self.custom_effects: dict[tuple[int, int], dict[str, TimedEffect]] = {}
        self.combat_traits: dict[tuple[int, int], dict[str, float]] = {}
        self.tactical_ai: dict[str, dict[str, Any]] = {}
        self.wave_directors: dict[str, WaveDirectorState] = {}
        self.boss_controllers: dict[str, BossControllerState] = {}
        self.hero_state: dict[tuple[int, int], dict[str, Any]] = {}
        self.inventory: dict[tuple[int, int], dict[str, int]] = {}
        self.quests: dict[str, str] = {}
        self.periodic_income: dict[str, dict[str, Any]] = {}
        self._previous_missiles: dict[int, Any] = {}
        self.projectile_created: list[Any] = []
        self.projectile_expired: list[Any] = []
        self._iteration_context: dict[str, Any] = {}
        self._ultimate_next_maintenance = 0.0
        self._ultimate_rng = random.Random(time.time_ns() ^ 0x1250)

    def _massive_begin_run(self) -> None:
        super()._massive_begin_run()
        self.unit_references.clear()
        self.player_spell_grants.clear()
        self.player_upgrade_levels.clear()
        self.player_tech_flags.clear()
        self.custom_effects.clear()
        self.combat_traits.clear()
        self.tactical_ai.clear()
        self.wave_directors.clear()
        self.boss_controllers.clear()
        self.hero_state.clear()
        self.inventory.clear()
        self.quests.clear()
        self.periodic_income.clear()
        self._previous_missiles = {m.address: m for m in self._active_missiles()} if getattr(self, "pm", None) else {}
        self.projectile_created = []
        self.projectile_expired = []
        self._iteration_context.clear()
        self._ultimate_next_maintenance = 0.0

    # ---------------------------------------------------------------- refs
    def _resolve_unit_reference(self, name: Any) -> Any | None:
        key = str(name or "Unit Reference 1").strip()
        marker = self.unit_references.get(key)
        if marker is None:
            return None
        current = getattr(self, "_snapshot", {})
        unit = current.get(marker)
        if unit is None:
            # Refresh directly because reference actions can run after a native
            # create/replace action but before the next normal event snapshot.
            unit = next((candidate for candidate in self.units() if self._unit_key_massive(candidate) == marker), None)
        if unit is None or int(unit.sflags) & DEAD_FLAG_MASK:
            self.unit_references.pop(key, None)
            return None
        return unit

    def _save_reference(self, name: Any, unit: Any | None) -> None:
        key = str(name or "Unit Reference 1").strip()
        if not key:
            raise ValueError("Unit reference name cannot be blank")
        if unit is None:
            self.unit_references.pop(key, None)
            return
        self.unit_references[key] = self._unit_key_massive(unit)

    @staticmethod
    def _distance_sq(a: Any, b: Any) -> int:
        return (int(a.x) - int(b.x)) ** 2 + (int(a.y) - int(b.y)) ** 2

    def _pick_reference_unit(self, args: dict[str, Any], player: int) -> Any | None:
        source = str(args.get("source", "Matching units"))
        if source == "Event unit":
            return self._event_unit()
        if source == "Last created unit":
            units = list(getattr(self, "created_units", []))
            return units[-1] if units else None
        if source == "Unit group":
            units = self._group_units(args.get("group", "Unit Group 1"))
        else:
            units = self._selected_units(args, player)
        if not units:
            return None
        selection = str(args.get("selection", "First matching"))
        if selection == "Random":
            return self._ultimate_rng.choice(units)
        if selection == "Most wounded":
            return min(units, key=lambda u: (int(u.health) / max(1, self._max_hp(u)), int(u.address)))
        if selection == "Highest health":
            return max(units, key=lambda u: (int(u.health), -int(u.address)))
        if selection in {"Nearest to reference", "Farthest from reference"}:
            anchor = self._resolve_unit_reference(args.get("anchor_reference", "Anchor"))
            if anchor is None:
                return units[0]
            reverse = selection.startswith("Farthest")
            return sorted(units, key=lambda u: (self._distance_sq(anchor, u), int(u.address)), reverse=reverse)[0]
        return units[0]

    # ------------------------------------------------------- progression tables
    @staticmethod
    def _dwords(blob: bytes) -> list[int]:
        return list(struct.unpack("<" + "I" * (len(blob) // 4), blob))

    def _resolve_progression_tables(self) -> dict[str, int]:
        if self._progression_tables is not None:
            return self._progression_tables
        if self._progression_resolution_error:
            raise RuntimeError(self._progression_resolution_error)
        if not getattr(self, "pm", None):
            raise RuntimeError("Progression tables require a live attachment")

        image = self.pm.read_bytes(self.base, self.image_size)
        code = image[: min(len(image), 0x510000)]
        start = 0x500000
        stop = min(len(image) - 0x140, 0x620000)
        candidates: list[tuple[int, int, tuple[list[int], ...]]] = []
        for offset in range(start, stop, 4):
            chunk = image[offset:offset + 0x140]
            arrays = tuple(self._dwords(chunk[index:index + 0x40]) for index in range(0, 0x140, 0x40))
            spells, allowed, spell_in, tech_allowed, tech_in = arrays
            if any(value & ~SPELL_MASK for value in spells + allowed + spell_in):
                continue
            if any(value & ~0xFFFFFFFF for value in tech_allowed + tech_in):
                continue
            if sum(value != 0 for value in allowed[:8]) < 2:
                continue
            if sum(value == 0 for value in spell_in) < 12 or sum(value == 0 for value in tech_in) < 12:
                continue
            if any(spells[i] & ~allowed[i] for i in range(8)):
                continue
            addresses = [self.base + offset + index * 0x40 for index in range(5)]
            xrefs = [code.count(struct.pack("<I", address)) for address in addresses]
            score = xrefs[0] * 3 + xrefs[1] * 2 + xrefs[2] * 3 + xrefs[3] + xrefs[4] * 2
            if xrefs[0] >= 2 and xrefs[1] >= 1 and xrefs[2] >= 1 and xrefs[4] >= 1:
                candidates.append((score, offset, arrays))
        if not candidates:
            self._progression_resolution_error = "Source-backed spell/technology array scan found no validated candidate"
            raise RuntimeError(self._progression_resolution_error)
        candidates.sort(reverse=True)
        if len(candidates) > 1 and candidates[0][0] <= candidates[1][0] + 2:
            self._progression_resolution_error = (
                "Source-backed spell/technology array scan was ambiguous: "
                + ", ".join(f"RVA 0x{offset:X} score {score}" for score, offset, _ in candidates[:4])
            )
            raise RuntimeError(self._progression_resolution_error)
        _score, offset, _arrays = candidates[0]
        result = {
            "spells": self.base + offset,
            "spells_allowed": self.base + offset + 0x40,
            "spells_in_process": self.base + offset + 0x80,
            "tech_allowed": self.base + offset + 0xC0,
            "tech_in_process": self.base + offset + 0x100,
        }

        # Resolve sgbTechTbl from the source-matched tech completion function.
        tech_in_bytes = struct.pack("<I", result["tech_in_process"])
        windows: list[bytes] = []
        position = 0
        while True:
            hit = code.find(tech_in_bytes, position)
            if hit < 0:
                break
            windows.append(code[max(0, hit - 0x180): min(len(code), hit + 0x280)])
            position = hit + 1
        tech_candidates: dict[int, int] = {}
        for window in windows:
            for pos in range(0, max(0, len(window) - 4)):
                address = struct.unpack_from("<I", window, pos)[0]
                rva = address - self.base
                if not 0x500000 <= rva <= self.image_size - 176:
                    continue
                if address in result.values():
                    continue
                table = image[rva:rva + 176]
                if len(table) != 176 or any(value > 4 for value in table):
                    continue
                # The unused P9-P16 half of each row is normally zero in an
                # eight-player match, making the real row-major table distinctive.
                zero_tail = sum(1 for row in range(11) for value in table[row * 16 + 8:row * 16 + 16] if value == 0)
                if zero_tail < 72:
                    continue
                refs = code.count(struct.pack("<I", address))
                tech_candidates[address] = refs * 10 + zero_tail
        if tech_candidates:
            ranked = sorted(((score, address) for address, score in tech_candidates.items()), reverse=True)
            if len(ranked) == 1 or ranked[0][0] > ranked[1][0] + 5:
                result["tech_levels"] = ranked[0][1]

        self._progression_tables = result
        self.log(
            "PROGRESSION TABLES: "
            + ", ".join(f"{name}=RVA 0x{address - self.base:X}" for name, address in result.items())
        )
        return result

    def _spell_mask_value(self, name: Any) -> tuple[str, int]:
        text = str(name or "Healing").strip()
        canonical = next((label for label in SPELL_BITS if label.casefold() == text.casefold()), None)
        if canonical is None:
            raise ValueError(f"Unknown spell research: {text}")
        return canonical, 1 << SPELL_BITS[canonical]

    def _upgrade_row_value(self, name: Any) -> tuple[str, int]:
        text = str(name or "Melee Attack").strip()
        canonical = next((label for label in UPGRADE_ROWS if label.casefold() == text.casefold()), None)
        if canonical is None:
            raise ValueError(f"Unknown upgrade category: {text}")
        return canonical, UPGRADE_ROWS[canonical]

    # --------------------------------------------------------------- effects
    def _effect_bucket(self, unit: Any) -> dict[str, TimedEffect]:
        return self.custom_effects.setdefault(self._unit_key_massive(unit), {})

    def _effect_active(self, unit: Any, kind: str) -> bool:
        effect = self.custom_effects.get(self._unit_key_massive(unit), {}).get(kind)
        return bool(effect and effect.expires_at > time.monotonic())

    def _apply_custom_effect(self, unit: Any, effect: TimedEffect) -> None:
        effect.next_tick = time.monotonic() + max(0.05, effect.interval)
        self._effect_bucket(unit)[effect.kind] = effect

    def _infer_attacker(self, victim: Any, world: list[Any]) -> Any | None:
        candidates = [
            unit for unit in world
            if int(getattr(unit, "target_unit", 0)) == int(victim.address)
            and int(getattr(unit, "owner", -1)) != int(victim.owner)
            and not (int(unit.sflags) & (DEAD_FLAG_MASK | HIDDEN_FLAG))
        ]
        return min(candidates, key=lambda u: (self._distance_sq(u, victim), int(u.address))) if candidates else None

    def _maintain_custom_effects(self, world: list[Any], now: float) -> None:
        current = {self._unit_key_massive(unit): unit for unit in world}
        for key, effects in list(self.custom_effects.items()):
            unit = current.get(key)
            if unit is None:
                self.custom_effects.pop(key, None)
                self.combat_traits.pop(key, None)
                continue
            for name, effect in list(effects.items()):
                if effect.expires_at <= now:
                    effects.pop(name, None)
                    continue
                if name in {"Stun", "Root", "Fear"}:
                    # A target-less Move to the current tile is a stable native
                    # parking order and avoids raw unit target pointers.
                    if int(unit.target_unit) or (int(unit.target_x), int(unit.target_y)) != (int(unit.x), int(unit.y)):
                        self._call_cdecl(
                            self.order_callees["set_target"],
                            [unit.address, int(unit.x), int(unit.y), 0, self.order_callees["do_move"]],
                        )
                if name == "Taunt":
                    source = self._resolve_unit_reference(effect.source_ref)
                    if source is not None:
                        self._call_cdecl(
                            self.order_callees["set_target"],
                            [unit.address, int(source.x), int(source.y), source.address, self.order_callees["do_attack"]],
                        )
                if name in {"Damage Over Time", "Healing Over Time"} and now >= effect.next_tick:
                    amount = max(1, int(effect.amount))
                    if name == "Damage Over Time":
                        source = self._resolve_unit_reference(effect.source_ref) or unit
                        self._call_damage_unit(source, unit, amount)
                    else:
                        hp = min(self._max_hp(unit), int(unit.health) + amount)
                        self._dispatch_ops([("write_word", unit.address + 0x22, hp)])
                    effect.next_tick = now + max(0.05, effect.interval)
            if not effects:
                self.custom_effects.pop(key, None)

        # Post-hit traits use the already prepared damage events. They are
        # deliberately bounded to one response per victim per engine cycle.
        for key, damage in list(getattr(self, "_unit_damage_events", {}).items()):
            victim = current.get(key)
            if victim is None or damage <= 0:
                continue
            bucket = self.custom_effects.get(key, {})
            shield = bucket.get("Shield")
            if shield and shield.amount > 0:
                absorbed = min(int(shield.amount), int(damage))
                restored = min(self._max_hp(victim), int(victim.health) + absorbed)
                self._dispatch_ops([("write_word", victim.address + 0x22, restored)])
                shield.amount -= absorbed
                if shield.amount <= 0:
                    bucket.pop("Shield", None)
            attacker = self._infer_attacker(victim, world)
            if attacker is None:
                continue
            attacker_traits = self.combat_traits.get(self._unit_key_massive(attacker), {})
            victim_traits = self.combat_traits.get(key, {})
            evasion = float(victim_traits.get("Evasion", 0.0))
            if evasion > 0 and self._ultimate_rng.random() < evasion / 100.0:
                restored = min(self._max_hp(victim), int(victim.health) + int(damage))
                self._dispatch_ops([("write_word", victim.address + 0x22, restored)])
                continue
            reflect = float(victim_traits.get("Reflect", 0.0))
            if reflect > 0:
                self._call_damage_unit(victim, attacker, max(1, round(int(damage) * reflect / 100.0)))
            lifesteal = float(attacker_traits.get("Lifesteal", 0.0))
            if lifesteal > 0:
                healed = min(self._max_hp(attacker), int(attacker.health) + max(1, round(int(damage) * lifesteal / 100.0)))
                self._dispatch_ops([("write_word", attacker.address + 0x22, healed)])
            crit = float(attacker_traits.get("Critical Chance", 0.0))
            crit_mult = max(1.0, float(attacker_traits.get("Critical Multiplier", 2.0)))
            if crit > 0 and self._ultimate_rng.random() < crit / 100.0:
                self._call_damage_unit(attacker, victim, max(1, round(int(damage) * (crit_mult - 1.0))))
            cleave = float(attacker_traits.get("Cleave", 0.0))
            if cleave > 0:
                for target in world:
                    if target.address == victim.address or int(target.owner) == int(attacker.owner):
                        continue
                    if self._distance_sq(target, victim) <= 4:
                        self._call_damage_unit(attacker, target, max(1, round(int(damage) * cleave / 100.0)))

    # -------------------------------------------------------------- AI/waves
    def _maintain_tactical_ai(self, world: list[Any], now: float) -> None:
        for name, config in list(self.tactical_ai.items()):
            units = self._group_units(config.get("group", ""))
            if not units:
                continue
            profile = str(config.get("profile", "Aggressive"))
            destination = str(config.get("destination", "Anywhere"))
            if destination == "Anywhere":
                dx, dy = int(config.get("x", 0)), int(config.get("y", 0))
            else:
                loc = self._find_location(destination)
                dx, dy = (loc.left + loc.right) // 2, (loc.top + loc.bottom) // 2
            orders: list[tuple[int, int, int, int, int]] = []
            for unit in units:
                hp_percent = int(unit.health) * 100 / max(1, self._max_hp(unit))
                if profile == "Retreat when wounded" and hp_percent <= float(config.get("retreat_health", 30)):
                    retreat = str(config.get("retreat_location", destination))
                    if retreat != "Anywhere":
                        loc = self._find_location(retreat)
                        tx, ty = (loc.left + loc.right) // 2, (loc.top + loc.bottom) // 2
                    else:
                        tx, ty = int(config.get("retreat_x", unit.x)), int(config.get("retreat_y", unit.y))
                    orders.append((unit.address, tx, ty, 0, self.order_callees["do_move"]))
                elif profile == "Hold formation":
                    if self._distance_sq(unit, type("P", (), {"x": dx, "y": dy})()) > int(config.get("radius", 5)) ** 2:
                        orders.append((unit.address, dx, dy, 0, self.order_callees["do_move"]))
                else:
                    orders.append((unit.address, dx, dy, 0, self.order_callees["do_attack"]))
            if orders:
                self._call_cdecl_batched([(self.order_callees["set_target"], list(order)) for order in orders[:64]])

    def _maintain_wave_directors(self, now: float) -> None:
        for state in list(self.wave_directors.values()):
            if not state.active or state.completed:
                continue
            if state.current_group and self._group_units(state.current_group):
                continue
            if state.wave >= state.max_waves > 0:
                state.completed = True
                state.active = False
                continue
            if now < state.waiting_until:
                continue
            state.wave += 1
            count = max(1, min(64, state.base_count + (state.wave - 1) * state.growth))
            unit_type = self._ultimate_rng.choice(state.unit_pool)
            group = f"{state.group_prefix} {state.wave}"
            create_args = {
                "player": state.owner,
                "unit": unit_type,
                "amount_expression": str(count),
                "spawn_location": state.spawn_location,
                "spawn_x": state.spawn_x,
                "spawn_y": state.spawn_y,
                "formation": state.formation,
                "spacing": state.spacing,
                "health_percent": 100,
                "mana": 255,
                "facing": -1,
                "group": group,
                "order": "Attack",
                "destination": state.destination,
                "destination_x": state.destination_x,
                "destination_y": state.destination_y,
            }
            MassiveFeatureMixin.massive_action(self, "Create Wave", create_args, state.owner)
            if state.boss_every > 0 and state.wave % state.boss_every == 0 and state.boss_unit >= 0:
                boss_args = dict(create_args)
                boss_args.update({"unit": state.boss_unit, "amount_expression": "1", "group": f"{group} Boss"})
                MassiveFeatureMixin.massive_action(self, "Create Wave", boss_args, state.owner)
                self.unit_groups[group].update(self.unit_groups.get(f"{group} Boss", set()))
            state.current_group = group
            state.waiting_until = now + state.intermission
            self.counters[f"{state.name} Wave"] = state.wave
            self.log(f"WAVE DIRECTOR: {state.name} spawned wave {state.wave} with {len(self._group_units(group))} unit(s)")

    def _maintain_boss_controllers(self, now: float) -> None:
        for state in list(self.boss_controllers.values()):
            if not state.active:
                continue
            boss = self._resolve_unit_reference(state.reference)
            if boss is None:
                state.active = False
                continue
            hp_percent = int(boss.health) * 100 / max(1, self._max_hp(boss))
            while state.current_phase < len(state.phases):
                phase = state.phases[state.current_phase]
                threshold = float(phase.get("health_at_or_below", 100))
                if hp_percent > threshold:
                    break
                state.current_phase += 1
                self.counters[f"{state.name} Phase"] = state.current_phase
                message = str(phase.get("message", "")).strip()
                if message:
                    self.action("Game Message", {"text": message, "color": "Red — native selected / warning", "recipients": "All active players", "seconds": 4, "also_log": True}, int(boss.owner))
                spawn_unit = int(phase.get("spawn_unit", -1))
                spawn_count = int(phase.get("spawn_count", 0))
                if 0 <= spawn_unit <= MOBILE_MAX and spawn_count > 0:
                    args = {
                        "player": int(phase.get("spawn_player", boss.owner)),
                        "unit": spawn_unit,
                        "amount_expression": str(min(64, spawn_count)),
                        "spawn_location": "Anywhere",
                        "spawn_x": int(boss.x),
                        "spawn_y": int(boss.y),
                        "formation": str(phase.get("formation", "Grid")),
                        "spacing": int(phase.get("spacing", 1)),
                        "group": f"{state.name} Phase {state.current_phase} Adds",
                        "order": "Attack",
                        "destination": str(phase.get("destination", "Anywhere")),
                        "destination_x": int(phase.get("destination_x", boss.x)),
                        "destination_y": int(phase.get("destination_y", boss.y)),
                        "mana": 255,
                        "health_percent": 100,
                        "facing": -1,
                    }
                    MassiveFeatureMixin.massive_action(self, "Create Wave", args, int(boss.owner))
                if bool(phase.get("invulnerable", False)):
                    self._write_status(boss, "Unholy Armor", int(phase.get("invulnerable_ticks", 250)))

    def _maintain_periodic_income(self, now: float) -> None:
        for name, config in list(self.periodic_income.items()):
            if now < float(config.get("next", 0.0)):
                continue
            owner = int(config["player"])
            resource = str(config["resource"])
            amount = int(config["amount"])
            self.action("Add Resources", {"player": owner, "resource": resource, "amount": amount}, owner)
            config["next"] = now + max(0.25, float(config["interval"]))

    # ---------------------------------------------------------- cycle hooks
    def _massive_prepare_pre(self, world: list[Any]) -> bool:
        result = super()._massive_prepare_pre(world)
        if result is False:
            return False
        now = time.monotonic()
        # Keep maintenance bounded. The normal trigger engine may cycle faster
        # than Warcraft needs for these high-level systems.
        if now < self._ultimate_next_maintenance:
            return True
        self._ultimate_next_maintenance = now + 0.20
        self._maintain_custom_effects(world, now)
        self._maintain_tactical_ai(world, now)
        self._maintain_wave_directors(now)
        self._maintain_boss_controllers(now)
        self._maintain_periodic_income(now)
        return True

    def _massive_prepare_events(self, current: dict[tuple[int, int], Any], previous: dict[tuple[int, int], Any]) -> None:
        super()._massive_prepare_events(current, previous)
        missiles = {m.address: m for m in self._active_missiles()}
        self.projectile_created = [missiles[address] for address in missiles.keys() - self._previous_missiles.keys()]
        self.projectile_expired = [self._previous_missiles[address] for address in self._previous_missiles.keys() - missiles.keys()]
        if self.projectile_created:
            self._available_events.add("Projectile Created")
        if self.projectile_expired:
            self._available_events.add("Projectile Expired")
        self._previous_missiles = missiles

        # Grant XP from credited kills to referenced/group heroes belonging to
        # the killer. XP is a trigger-level RPG layer and never changes Warcraft's
        # native score or kill tables.
        for owner in range(8):
            kills = int(self._kill_delta(owner, "All")) if hasattr(self, "_kill_delta") else 0
            if kills <= 0:
                continue
            for key, state in list(self.hero_state.items()):
                unit = current.get(key)
                if unit is None or int(unit.owner) != owner:
                    continue
                state["xp"] = int(state.get("xp", 0)) + kills * int(state.get("xp_per_kill", 10))
                needed = max(1, int(state.get("next_level_xp", 100)))
                while int(state["xp"]) >= needed:
                    state["xp"] -= needed
                    state["level"] = int(state.get("level", 1)) + 1
                    needed = round(needed * float(state.get("growth", 1.25)))
                    state["next_level_xp"] = needed
                    hp = min(65535, self._max_hp(unit) + int(state.get("hp_per_level", 10)))
                    self._dispatch_ops([("write_word", unit.address + 0x22, hp)])
                    self.log(f"HERO LEVEL: P{owner + 1} unit 0x{unit.address:08X} reached level {state['level']}")

    # ------------------------------------------------------------- conditions
    def massive_value(self, kind: str, args: dict[str, Any], player: int) -> Any:
        if kind.startswith("Unit Reference"):
            unit = self._resolve_unit_reference(args.get("reference", "Unit Reference 1"))
            if kind == "Unit Reference Exists":
                return 1 if unit is not None else 0
            if unit is None:
                return 0
            if kind == "Unit Reference Alive":
                return 1
            if kind == "Unit Reference Health":
                return int(unit.health)
            if kind == "Unit Reference Health Percent":
                return round(int(unit.health) * 100 / max(1, self._max_hp(unit)))
            if kind == "Unit Reference Mana":
                return int(unit.mana)
            if kind == "Unit Reference Owner":
                return int(unit.owner) + 1
            if kind == "Unit Reference Type":
                return int(unit.unit_type)
            if kind == "Unit Reference Order":
                wanted = str(args.get("order", "Attack"))
                return 1 if int(unit.action) in ORDER_ACTIONS.get(wanted, set()) or int(unit.next_action) in ORDER_ACTIONS.get(wanted, set()) else 0
            if kind == "Unit Reference In Location":
                location = self._find_location(str(args.get("location", "Location 1")))
                return 1 if self._inside(unit, location) else 0
        if kind == "Upgrade Level":
            owner = int(args.get("player", player))
            name, row = self._upgrade_row_value(args.get("upgrade", "Melee Attack"))
            try:
                tables = self._resolve_progression_tables()
                if "tech_levels" in tables:
                    return int(self.pm.read_uchar(tables["tech_levels"] + row * 16 + owner))
            except Exception:
                pass
            return int(self.player_upgrade_levels.get(owner, {}).get(name, 0))
        if kind in {"Spell Researched", "Spell Allowed"}:
            owner = int(args.get("player", player))
            name, mask = self._spell_mask_value(args.get("spell", "Healing"))
            try:
                tables = self._resolve_progression_tables()
                table = tables["spells"] if kind == "Spell Researched" else tables["spells_allowed"]
                return 1 if self.pm.read_uint(table + owner * 4) & mask else 0
            except Exception:
                return 1 if name in self.player_spell_grants.get(owner, set()) else 0
        if kind == "Custom Effect Active":
            units = self._selected_units(args, player)
            effect = str(args.get("effect", "Stun"))
            return sum(1 for unit in units if self._effect_active(unit, effect))
        if kind == "Shield Amount":
            units = self._selected_units(args, player)
            if not units:
                return 0
            effect = self.custom_effects.get(self._unit_key_massive(units[0]), {}).get("Shield")
            return max(0, int(effect.amount)) if effect else 0
        if kind in {"Projectile Created", "Projectile Expired"}:
            source = self.projectile_created if kind == "Projectile Created" else self.projectile_expired
            missile = args.get("missile", "Any")
            matches = [m for m in source if missile == "Any" or int(m.missile_type) == int(missile)]
            if matches:
                m = matches[0]
                self._event_context = {
                    "event_type": kind,
                    "x": int(m.x), "y": int(m.y), "missile_type": int(m.missile_type),
                    "damage": int(m.damage), "amount": len(matches),
                }
            return len(matches)
        if kind == "Wave Director State":
            state = self.wave_directors.get(str(args.get("name", "Wave Director 1")))
            wanted = str(args.get("state", "Active"))
            actual = "Missing" if state is None else "Complete" if state.completed else "Active" if state.active else "Stopped"
            return actual
        if kind == "Wave Number":
            state = self.wave_directors.get(str(args.get("name", "Wave Director 1")))
            return int(state.wave if state else 0)
        if kind == "Boss Phase":
            state = self.boss_controllers.get(str(args.get("name", "Boss 1")))
            return int(state.current_phase if state else 0)
        if kind == "Tactical AI Enabled":
            return 1 if str(args.get("name", "Squad AI 1")) in self.tactical_ai else 0
        if kind == "Hero Level":
            unit = self._resolve_unit_reference(args.get("reference", "Hero"))
            if unit is None:
                return 0
            return int(self.hero_state.get(self._unit_key_massive(unit), {}).get("level", 1))
        if kind == "Hero Experience":
            unit = self._resolve_unit_reference(args.get("reference", "Hero"))
            if unit is None:
                return 0
            return int(self.hero_state.get(self._unit_key_massive(unit), {}).get("xp", 0))
        if kind == "Inventory Item Count":
            unit = self._resolve_unit_reference(args.get("reference", "Hero"))
            if unit is None:
                return 0
            return int(self.inventory.get(self._unit_key_massive(unit), {}).get(str(args.get("item", "Item")), 0))
        if kind == "Quest State":
            return self.quests.get(str(args.get("quest", "Quest 1")), "Missing")
        if kind == "Force Member":
            force = str(args.get("force", "Force 1"))
            owner = int(args.get("player", player))
            scenario = getattr(self, "scenario", None)
            return 1 if scenario and owner in scenario.forces.get(force, []) else 0
        if kind == "Active Player Count":
            return sum(1 for owner in range(8) if any(int(unit.owner) == owner for unit in self.units()))
        if kind == "Sapper Count":
            owner = int(args.get("player", player))
            location = str(args.get("location", "Anywhere"))
            selected = self._selected_units({"player": owner, "unit": "Any", "location": location}, player)
            return sum(1 for unit in selected if int(unit.unit_type) in SAPPER_TYPES)
        result = super().massive_value(kind, args, player)
        return result

    # ---------------------------------------------------------------- actions
    def massive_action(self, kind: str, args: dict[str, Any], player: int) -> bool:
        # Persistent references and trigger iteration.
        if kind == "Save Unit Reference":
            unit = self._pick_reference_unit(args, player)
            self._save_reference(args.get("reference", "Unit Reference 1"), unit)
            self.log(f"UNIT REFERENCE: {args.get('reference', 'Unit Reference 1')} -> {hex(unit.address) if unit else 'none'}")
            return True
        if kind == "Save Event Unit Reference":
            unit = self._event_unit()
            self._save_reference(args.get("reference", "Event Unit"), unit)
            return True
        if kind == "Save Last Created Unit Reference":
            unit = list(getattr(self, "created_units", []))[-1] if getattr(self, "created_units", []) else None
            self._save_reference(args.get("reference", "Last Created Unit"), unit)
            return True
        if kind == "Clear Unit Reference":
            self.unit_references.pop(str(args.get("reference", "Unit Reference 1")), None)
            return True
        if kind in {"Order Unit Reference", "Teleport Unit Reference", "Give Unit Reference", "Set Unit Reference Health", "Set Unit Reference Mana", "Apply Effect To Unit Reference", "Kill Unit Reference"}:
            unit = self._resolve_unit_reference(args.get("reference", "Unit Reference 1"))
            if unit is None:
                raise RuntimeError("The named unit reference is missing or dead")
            if kind == "Order Unit Reference":
                order = str(args.get("order", "Attack"))
                destination = str(args.get("destination", "Anywhere"))
                if destination == "Anywhere":
                    x, y = int(args.get("x", unit.x)), int(args.get("y", unit.y))
                else:
                    loc = self._find_location(destination)
                    x, y = (loc.left + loc.right) // 2, (loc.top + loc.bottom) // 2
                callback = {"Move": "do_move", "Attack": "do_attack", "Patrol": "do_patrol"}.get(order)
                if callback is None:
                    raise ValueError(f"Unsupported reference order: {order}")
                self._call_cdecl(self.order_callees["set_target"], [unit.address, x, y, 0, self.order_callees[callback]])
                if order == "Attack":
                    self._attack_routes[self._unit_key_massive(unit)] = (x, y)
            elif kind == "Teleport Unit Reference":
                destination = str(args.get("destination", "Anywhere"))
                if destination == "Anywhere":
                    x, y = int(args.get("x", unit.x)), int(args.get("y", unit.y))
                else:
                    loc = self._find_location(destination)
                    x, y = (loc.left + loc.right) // 2, (loc.top + loc.bottom) // 2
                moved = self._move_mobile_unit(unit, x, y)
                self._save_reference(args.get("reference"), moved)
            elif kind == "Give Unit Reference":
                new_owner = int(args.get("new_owner", player))
                self._call_cdecl(self.capture_unit_address, [unit.address, new_owner, 0])
                refreshed = next((u for u in self.units() if u.address == unit.address), None)
                self._save_reference(args.get("reference"), refreshed)
            elif kind == "Set Unit Reference Health":
                value = max(1, min(self._max_hp(unit), int(args.get("amount", 100))))
                self._dispatch_ops([("write_word", unit.address + 0x22, value)])
            elif kind == "Set Unit Reference Mana":
                value = max(0, min(255, int(args.get("amount", 255))))
                self._dispatch_ops([("write_bytes", unit.address + 0x26, bytes([value]))])
            elif kind == "Apply Effect To Unit Reference":
                effect = str(args.get("effect", "Bloodlust"))
                if effect in STATUS_OFFSETS:
                    self._write_status(unit, effect, int(args.get("ticks", 0)))
                else:
                    seconds = max(0.1, float(args.get("seconds", 5)))
                    self._apply_custom_effect(unit, TimedEffect(effect, time.monotonic() + seconds, amount=float(args.get("amount", 0)), interval=float(args.get("interval", 1)), source_ref=str(args.get("source_reference", ""))))
            else:
                self._call_cdecl(self.damage_callees["unit_kill"], [unit.address])
            return True

        if kind == "Run Trigger For Each Unit In Group":
            engine = getattr(self, "engine", None)
            if engine is None:
                raise RuntimeError("Trigger iteration requires an active TriggerEngine")
            target_name = str(args.get("trigger", "")).strip()
            target_index = engine.find_trigger_index(target_name)
            if target_index is None:
                raise ValueError(f"Unknown trigger: {target_name}")
            units = self._group_units(args.get("group", "Unit Group 1"))
            reference = str(args.get("reference", "Loop Unit"))
            original = self.unit_references.get(reference)
            try:
                for unit in units[: max(1, min(1600, int(args.get("maximum", 1600))))]:
                    self._save_reference(reference, unit)
                    self._set_event_context("Unit Group Iteration", unit, 1)
                    engine._execute_actions(target_index, player, 0, time.monotonic(), called=True)
            finally:
                if original is None:
                    self.unit_references.pop(reference, None)
                else:
                    self.unit_references[reference] = original
            self.log(f"FOR EACH UNIT: called {target_name} for {min(len(units), int(args.get('maximum', 1600)))} unit(s)")
            return True
        if kind == "Run Trigger For Each Player":
            engine = getattr(self, "engine", None)
            if engine is None:
                raise RuntimeError("Player iteration requires an active TriggerEngine")
            target_name = str(args.get("trigger", "")).strip()
            target_index = engine.find_trigger_index(target_name)
            if target_index is None:
                raise ValueError(f"Unknown trigger: {target_name}")
            force = str(args.get("force", "All Players"))
            if force == "All Players":
                players = list(range(8))
            else:
                players = list(getattr(self.scenario, "forces", {}).get(force, []))
            for owner in players:
                engine._execute_actions(target_index, int(owner), 0, time.monotonic(), called=True)
            return True

        # Native spell research and upgrade levels.
        if kind in {"Give Spell", "Remove Spell", "Give All Spells", "Remove All Spells", "Set Spell Allowed"}:
            owner = int(args.get("player", player))
            if not 0 <= owner <= 15:
                raise ValueError("Spell research player must be P1-P16")
            tables = self._resolve_progression_tables()
            spells_address = tables["spells"] + owner * 4
            allowed_address = tables["spells_allowed"] + owner * 4
            before = self.pm.read_uint(spells_address)
            allowed_before = self.pm.read_uint(allowed_address)
            if kind in {"Give Spell", "Remove Spell", "Set Spell Allowed"}:
                name, mask = self._spell_mask_value(args.get("spell", "Healing"))
            else:
                name, mask = "All spells", SPELL_MASK
            if kind == "Give Spell":
                after = before | mask
                allowed_after = allowed_before | mask
                self.player_spell_grants.setdefault(owner, set()).add(name)
            elif kind == "Remove Spell":
                after = before & ~mask
                allowed_after = allowed_before
                self.player_spell_grants.setdefault(owner, set()).discard(name)
            elif kind == "Give All Spells":
                after = before | SPELL_MASK
                allowed_after = allowed_before | SPELL_MASK
                self.player_spell_grants[owner] = set(SPELL_BITS)
            elif kind == "Remove All Spells":
                after = before & ~SPELL_MASK
                allowed_after = allowed_before
                self.player_spell_grants[owner] = set()
            else:
                allowed_after = (allowed_before | mask) if bool(args.get("allowed", True)) else (allowed_before & ~mask)
                after = before & allowed_after
            self._dispatch_ops([
                ("write_dword", spells_address, after),
                ("write_dword", allowed_address, allowed_after),
            ])
            self.log(f"SPELL RESEARCH: P{owner + 1} {kind} {name}; researched=0x{after:08X}, allowed=0x{allowed_after:08X}")
            return True

        if kind in {"Set Upgrade Level", "Add Upgrade Level", "Give All Upgrades", "Clear All Upgrades"}:
            owner = int(args.get("player", player))
            tables = self._resolve_progression_tables()
            if "tech_levels" not in tables:
                raise RuntimeError("The native sgbTechTbl upgrade-level table could not be uniquely resolved")
            operations: list[tuple] = []
            if kind in {"Give All Upgrades", "Clear All Upgrades"}:
                level = 2 if kind == "Give All Upgrades" else 0
                for name, row in UPGRADE_ROWS.items():
                    operations.append(("write_bytes", tables["tech_levels"] + row * 16 + owner, bytes([level])))
                    self.player_upgrade_levels.setdefault(owner, {})[name] = level
            else:
                name, row = self._upgrade_row_value(args.get("upgrade", "Melee Attack"))
                address = tables["tech_levels"] + row * 16 + owner
                before = int(self.pm.read_uchar(address))
                if kind == "Set Upgrade Level":
                    level = int(args.get("level", 1))
                else:
                    level = before + int(args.get("amount", 1))
                level = max(0, min(2, level))
                operations.append(("write_bytes", address, bytes([level])))
                self.player_upgrade_levels.setdefault(owner, {})[name] = level
            self._dispatch_ops(operations)
            self.log(f"UPGRADES: P{owner + 1} {kind} applied to {len(operations)} native tech cell(s)")
            return True

        if kind == "Enable Advanced Unit Classes":
            owner = int(args.get("player", player))
            # Grant the source conversion bit and the two automatic baseline
            # spells, then convert existing Knights/Ogres through the validated
            # replace path so HP and ownership remain coherent.
            self.massive_action("Give Spell", {"player": owner, "spell": "Paladin / Ogre-Mage Conversion"}, player)
            self.massive_action("Give Spell", {"player": owner, "spell": "Holy Vision"}, player)
            self.massive_action("Give Spell", {"player": owner, "spell": "Eye of Kilrogg"}, player)
            for old, new in ((6, 12), (7, 13)):
                MassiveFeatureMixin.massive_action(self, "Replace Units", {"player": owner, "unit": old, "location": "Anywhere", "amount": "All", "new_unit": new, "preserve_health_percent": True}, owner)
            return True

        # Sappers / demolition.
        if kind in {"Order Sappers Demolish", "Create Sapper Assault", "Arm Sappers"}:
            do_demolish = self.base + 0x319D70
            body = self.pm.read_bytes(do_demolish, 0x48)
            if b"\x55\x8B\xEC" not in body[:8] or b"\x6A" not in body:
                raise RuntimeError("Source-backed do_unit_demolish signature validation failed")
            if kind == "Create Sapper Assault":
                owner = int(args.get("player", player))
                unit_type = int(args.get("unit", 14))
                if unit_type not in SAPPER_TYPES:
                    raise ValueError("Create Sapper Assault requires Dwarves or Goblins")
                wave_args = {
                    "player": owner, "unit": unit_type,
                    "amount_expression": str(max(1, min(32, int(args.get("amount", 6))))),
                    "spawn_location": str(args.get("spawn_location", "Anywhere")),
                    "spawn_x": int(args.get("spawn_x", 0)), "spawn_y": int(args.get("spawn_y", 0)),
                    "formation": str(args.get("formation", "Grid")), "spacing": int(args.get("spacing", 1)),
                    "health_percent": 100, "mana": 0, "facing": -1,
                    "group": str(args.get("group", "Sapper Assault")), "order": "None",
                    "destination": "Anywhere", "destination_x": 0, "destination_y": 0,
                }
                MassiveFeatureMixin.massive_action(self, "Create Wave", wave_args, owner)
                targets = self._group_units(wave_args["group"])
            else:
                targets = [unit for unit in self._selected_units(args, player) if int(unit.unit_type) in SAPPER_TYPES]
            if kind == "Arm Sappers":
                # The source intentionally makes sappers explode if given
                # Invisibility or Unholy Armor. Arm uses a harmless runtime tag,
                # not those dangerous native timers.
                for unit in targets:
                    self._apply_custom_effect(unit, TimedEffect("Armed Sapper", time.monotonic() + max(1.0, float(args.get("seconds", 60)))))
                return True
            destination = str(args.get("destination", "Anywhere"))
            if destination == "Anywhere":
                x, y = int(args.get("x", 0)), int(args.get("y", 0))
            else:
                loc = self._find_location(destination)
                x, y = (loc.left + loc.right) // 2, (loc.top + loc.bottom) // 2
            target_unit = self._resolve_unit_reference(args.get("target_reference", "")) if str(args.get("target_reference", "")).strip() else None
            self._call_cdecl_batched([
                (self.order_callees["set_target"], [unit.address, x, y, target_unit.address if target_unit else 0, do_demolish])
                for unit in targets
            ])
            self.log(f"SAPPER DEMOLISH: ordered {len(targets)} sapper(s) toward ({x},{y})")
            return True

        # Advanced combat effects and traits.
        if kind in {"Apply Stun", "Apply Root", "Apply Silence", "Apply Fear", "Apply Taunt", "Apply Damage Over Time", "Apply Healing Over Time", "Add Shield", "Clear Custom Effects"}:
            units = self._selected_units(args, player)
            seconds = max(0.1, float(args.get("seconds", 5)))
            now = time.monotonic()
            mapping = {
                "Apply Stun": "Stun", "Apply Root": "Root", "Apply Silence": "Silence",
                "Apply Fear": "Fear", "Apply Taunt": "Taunt",
                "Apply Damage Over Time": "Damage Over Time", "Apply Healing Over Time": "Healing Over Time",
                "Add Shield": "Shield",
            }
            if kind == "Clear Custom Effects":
                effect = str(args.get("effect", "All"))
                for unit in units:
                    key = self._unit_key_massive(unit)
                    if effect == "All":
                        self.custom_effects.pop(key, None)
                    else:
                        self.custom_effects.get(key, {}).pop(effect, None)
                return True
            effect_name = mapping[kind]
            for unit in units:
                effect = TimedEffect(
                    effect_name, now + seconds,
                    amount=float(args.get("amount", 40)),
                    interval=max(0.1, float(args.get("interval", 1.0))),
                    source_ref=str(args.get("source_reference", "")),
                )
                self._apply_custom_effect(unit, effect)
            self.log(f"CUSTOM EFFECT: {effect_name} applied to {len(units)} unit(s) for {seconds:.1f}s")
            return True
        if kind in {"Set Lifesteal", "Set Critical Strike", "Set Evasion", "Set Reflect Damage", "Set Cleave"}:
            units = self._selected_units(args, player)
            for unit in units:
                traits = self.combat_traits.setdefault(self._unit_key_massive(unit), {})
                if kind == "Set Lifesteal":
                    traits["Lifesteal"] = max(0.0, min(100.0, float(args.get("percent", 20))))
                elif kind == "Set Critical Strike":
                    traits["Critical Chance"] = max(0.0, min(100.0, float(args.get("chance", 20))))
                    traits["Critical Multiplier"] = max(1.0, min(10.0, float(args.get("multiplier", 2.0))))
                elif kind == "Set Evasion":
                    traits["Evasion"] = max(0.0, min(100.0, float(args.get("percent", 20))))
                elif kind == "Set Reflect Damage":
                    traits["Reflect"] = max(0.0, min(500.0, float(args.get("percent", 25))))
                else:
                    traits["Cleave"] = max(0.0, min(500.0, float(args.get("percent", 35))))
            return True
        if kind in {"Knockback Units", "Pull Units"}:
            units = self._selected_units(args, player)
            anchor = self._resolve_unit_reference(args.get("anchor_reference", "Anchor"))
            if anchor is None:
                ax, ay = self._resolve_point(args, "anchor_location")
            else:
                ax, ay = int(anchor.x), int(anchor.y)
            distance = max(1, min(16, int(args.get("distance", 3))))
            for unit in units:
                dx, dy = int(unit.x) - ax, int(unit.y) - ay
                length = max(1.0, math.hypot(dx, dy))
                sign = -1 if kind == "Pull Units" else 1
                tx = round(int(unit.x) + sign * distance * dx / length)
                ty = round(int(unit.y) + sign * distance * dy / length)
                try:
                    self._move_mobile_unit(unit, tx, ty)
                except Exception:
                    continue
            return True

        # Projectile controls.
        if kind in {"Redirect Projectiles", "Destroy Projectiles", "Duplicate Projectiles"}:
            missiles = self._selected_missiles(args, player)
            if kind == "Destroy Projectiles":
                for missile in missiles:
                    self._dispatch_ops([("write_bytes", missile.address + 0x35, bytes([int(missile.flags) | 0x01]))])
                return True
            destination = str(args.get("destination", "Anywhere"))
            if destination == "Anywhere":
                x, y = int(args.get("x", 0)), int(args.get("y", 0))
            else:
                loc = self._find_location(destination)
                x, y = (loc.left + loc.right) // 2, (loc.top + loc.bottom) // 2
            if kind == "Redirect Projectiles":
                for missile in missiles:
                    self._dispatch_ops([
                        ("write_word", missile.address + 0x28, x),
                        ("write_word", missile.address + 0x2A, y),
                        ("write_dword", missile.address + 0x2C, 0),
                    ])
                return True
            # Duplicate through the validated Create Missile action using each
            # projectile's living owner and target when available.
            world = self.units()
            for missile in missiles[:16]:
                attacker = next((u for u in world if u.address == missile.owner_unit), None)
                target = next((u for u in world if u.address == missile.target_unit), None)
                if attacker and target:
                    for _ in range(max(1, min(8, int(args.get("copies", 1))))):
                        self._create_missile(attacker, target)
            return True

        # Tactical AI, wave director, and boss phases.
        if kind == "Enable Tactical AI":
            name = str(args.get("name", "Squad AI 1"))
            self.tactical_ai[name] = dict(args)
            return True
        if kind == "Disable Tactical AI":
            self.tactical_ai.pop(str(args.get("name", "Squad AI 1")), None)
            return True
        if kind == "Start Wave Director":
            name = str(args.get("name", "Wave Director 1"))
            raw_pool = args.get("unit_pool", "0")
            if isinstance(raw_pool, str):
                unit_pool = [int(part.strip(), 0) for part in raw_pool.replace(";", ",").split(",") if part.strip()]
            else:
                unit_pool = [int(value) for value in raw_pool]
            if not unit_pool or any(not 0 <= value <= MOBILE_MAX for value in unit_pool):
                raise ValueError("Wave Director unit pool must contain mobile unit IDs 0-57")
            state = WaveDirectorState(
                name=name, owner=int(args.get("player", player)), unit_pool=unit_pool,
                spawn_location=str(args.get("spawn_location", "Anywhere")),
                spawn_x=int(args.get("spawn_x", 0)), spawn_y=int(args.get("spawn_y", 0)),
                destination=str(args.get("destination", "Anywhere")),
                destination_x=int(args.get("destination_x", 0)), destination_y=int(args.get("destination_y", 0)),
                base_count=max(1, int(args.get("base_count", 6))), growth=max(0, int(args.get("growth", 2))),
                intermission=max(0.25, float(args.get("intermission", 5))), max_waves=max(0, int(args.get("max_waves", 10))),
                boss_every=max(0, int(args.get("boss_every", 5))), boss_unit=int(args.get("boss_unit", -1)),
                formation=str(args.get("formation", "Grid")), spacing=max(1, int(args.get("spacing", 1))),
                group_prefix=str(args.get("group_prefix", name)), waiting_until=time.monotonic() + max(0.0, float(args.get("start_delay", 0))),
            )
            self.wave_directors[name] = state
            return True
        if kind == "Stop Wave Director":
            state = self.wave_directors.get(str(args.get("name", "Wave Director 1")))
            if state:
                state.active = False
            return True
        if kind == "Advance Wave Director":
            state = self.wave_directors.get(str(args.get("name", "Wave Director 1")))
            if not state:
                raise ValueError("Unknown Wave Director")
            if state.current_group:
                self.unit_groups.pop(state.current_group, None)
            state.waiting_until = 0.0
            return True
        if kind == "Start Boss Controller":
            name = str(args.get("name", "Boss 1"))
            phases_raw = args.get("phases_json", "[]")
            phases = json.loads(phases_raw) if isinstance(phases_raw, str) else list(phases_raw)
            if not isinstance(phases, list):
                raise ValueError("Boss phases JSON must be a list")
            phases = sorted((dict(phase) for phase in phases), key=lambda p: float(p.get("health_at_or_below", 100)), reverse=True)
            self.boss_controllers[name] = BossControllerState(name, str(args.get("reference", "Boss")), phases)
            return True
        if kind == "Stop Boss Controller":
            state = self.boss_controllers.get(str(args.get("name", "Boss 1")))
            if state:
                state.active = False
            return True

        # Economy and production.
        if kind in {"Transfer Resources", "Steal Resources", "Tax Player"}:
            source = int(args.get("source_player", player))
            destination = int(args.get("destination_player", player))
            resource = str(args.get("resource", "Gold"))
            amount = max(0, int(args.get("amount", 0)))
            available = self._read_resource(source, resource)
            moved = min(available, amount)
            self.action("Subtract Resources", {"player": source, "resource": resource, "amount": moved}, player)
            self.action("Add Resources", {"player": destination, "resource": resource, "amount": moved}, player)
            return True
        if kind == "Start Periodic Income":
            name = str(args.get("name", "Income 1"))
            self.periodic_income[name] = {
                "player": int(args.get("player", player)), "resource": str(args.get("resource", "Gold")),
                "amount": max(0, int(args.get("amount", 100))), "interval": max(0.25, float(args.get("interval", 5))),
                "next": time.monotonic() + max(0.0, float(args.get("start_delay", 0))),
            }
            return True
        if kind == "Stop Periodic Income":
            self.periodic_income.pop(str(args.get("name", "Income 1")), None)
            return True
        if kind == "Refill Resource Node":
            units = self._selected_units(args, player)
            amount = max(0, min(65535, int(args.get("amount", 50000))))
            changed = 0
            for unit in units:
                if int(unit.unit_type) not in {92, 93}:
                    continue
                # Gold Mine/Oil Patch resource quantity is the source build's
                # building union word at +0x84, already validated for fresh
                # construction progress. This action is intentionally limited to
                # these two map-resource object types.
                self._dispatch_ops([("write_word", unit.address + 0x84, amount)])
                changed += 1
            self.log(f"RESOURCE NODE: refilled {changed} mine/oil object(s) to {amount}")
            return True
        if kind == "Train Units Instantly At Buildings":
            buildings = self._selected_units(args, player)
            unit_type = int(args.get("new_unit", 0))
            amount_each = max(1, min(16, int(args.get("amount_each", 1))))
            group = str(args.get("group", "Instant Production"))
            created: list[Any] = []
            for building in buildings[:64]:
                for _ in range(amount_each):
                    unit = self._create_unit(int(args.get("new_owner", building.owner)), unit_type, int(building.x) + 1, int(building.y) + 1)
                    if unit:
                        created.append(unit)
            if group:
                self.unit_groups[group] = {self._unit_key_massive(unit) for unit in created}
            self.log(f"INSTANT PRODUCTION: created {len(created)} unit(s) at {len(buildings)} building(s)")
            return True

        # RPG, inventory, quests, and transmissions.
        if kind == "Register Hero":
            unit = self._resolve_unit_reference(args.get("reference", "Hero"))
            if unit is None:
                raise RuntimeError("Register Hero requires a live unit reference")
            self.hero_state[self._unit_key_massive(unit)] = {
                "level": max(1, int(args.get("level", 1))), "xp": max(0, int(args.get("xp", 0))),
                "next_level_xp": max(1, int(args.get("next_level_xp", 100))),
                "growth": max(1.0, float(args.get("xp_growth", 1.25))),
                "xp_per_kill": max(0, int(args.get("xp_per_kill", 10))),
                "hp_per_level": max(0, int(args.get("hp_per_level", 10))),
            }
            return True
        if kind in {"Add Hero Experience", "Set Hero Level"}:
            unit = self._resolve_unit_reference(args.get("reference", "Hero"))
            if unit is None:
                raise RuntimeError("Hero action requires a live unit reference")
            state = self.hero_state.setdefault(self._unit_key_massive(unit), {"level": 1, "xp": 0, "next_level_xp": 100, "growth": 1.25, "xp_per_kill": 10, "hp_per_level": 10})
            if kind == "Add Hero Experience":
                state["xp"] = int(state.get("xp", 0)) + max(0, int(args.get("amount", 0)))
            else:
                state["level"] = max(1, int(args.get("level", 1)))
            return True
        if kind in {"Give Inventory Item", "Remove Inventory Item", "Clear Inventory"}:
            unit = self._resolve_unit_reference(args.get("reference", "Hero"))
            if unit is None:
                raise RuntimeError("Inventory action requires a live unit reference")
            bag = self.inventory.setdefault(self._unit_key_massive(unit), {})
            if kind == "Clear Inventory":
                bag.clear()
            else:
                item = str(args.get("item", "Item"))
                amount = max(0, int(args.get("amount", 1)))
                bag[item] = max(0, int(bag.get(item, 0)) + (amount if kind == "Give Inventory Item" else -amount))
                if bag[item] == 0:
                    bag.pop(item, None)
            return True
        if kind in {"Set Quest", "Complete Quest", "Fail Quest", "Clear Quest"}:
            name = str(args.get("quest", "Quest 1"))
            if kind == "Clear Quest":
                self.quests.pop(name, None)
            else:
                self.quests[name] = {"Set Quest": "Active", "Complete Quest": "Completed", "Fail Quest": "Failed"}[kind]
            return True
        if kind == "Transmission":
            speaker = str(args.get("speaker", "Narrator"))
            text = str(args.get("text", ""))
            message = f"{speaker}: {text}" if speaker else text
            self.action("Game Message", {"text": message, "color": args.get("color", "White — native highlight"), "recipients": args.get("recipients", "All active players"), "seconds": int(args.get("seconds", 5)), "also_log": True}, player)
            reference = str(args.get("reference", "")).strip()
            unit = self._resolve_unit_reference(reference) if reference else None
            if unit is not None and bool(args.get("center_camera", False)):
                MassiveFeatureMixin.massive_action(self, "Center Camera", {"location": "Anywhere", "x": unit.x, "y": unit.y}, player)
            return True

        # Diagnostics.
        if kind == "Watch Expression":
            expression = str(args.get("expression", "0"))
            value = self._evaluate_expression(expression, player)
            self.log(f"WATCH: {expression} = {value}")
            return True
        if kind == "Dump Unit References":
            entries = []
            for name in sorted(self.unit_references):
                unit = self._resolve_unit_reference(name)
                entries.append(f"{name}={'dead' if unit is None else f'P{unit.owner+1} type {unit.unit_type} ({unit.x},{unit.y}) HP {unit.health}'}")
            self.log("UNIT REFERENCES: " + ("; ".join(entries) if entries else "none"))
            return True
        if kind == "Dump Unit Group":
            name = str(args.get("group", "Unit Group 1"))
            units = self._group_units(name)
            self.log(f"UNIT GROUP DUMP: {name} has {len(units)} live unit(s): " + ", ".join(f"P{u.owner+1}/T{u.unit_type}@{u.x},{u.y}" for u in units[:64]))
            return True

        return super().massive_action(kind, args, player)
