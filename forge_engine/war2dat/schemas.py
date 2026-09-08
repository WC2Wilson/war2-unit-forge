from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Field:
    name: str
    source: str
    offset: int
    kind: str
    stride: int


@dataclass(frozen=True)
class View:
    name: str
    rows: tuple[str, ...]
    fields: tuple[Field, ...]


FIELD_HELP = {
    "gwBuildGroup": "Graphics group used to draw this unit or building. The value indexes the unit-group tables.",
    "gwLoadAlwaysTbl": "GRP subfile loaded for this graphics group in every terrain set. 65535 (0xFFFF) means no file.",
    "gwSummerLoadTbl": "Forest/summer GRP subfile for this graphics group. 65535 (0xFFFF) means no terrain-specific file.",
    "gwSnowLoadTbl": "Winter/snow GRP subfile for this graphics group. 65535 (0xFFFF) means no terrain-specific file.",
    "gwSwampLoadTbl": "Wasteland/swamp GRP subfile for this graphics group. 65535 (0xFFFF) means no terrain-specific file.",
    "gwOrcSwampLoadTbl": "Orc-land swamp GRP subfile added by the expansion table. 65535 (0xFFFF) means no file.",
    "gUnitUnmaskTbl": "Source FUNC value used by the unit's fog-of-war unmask routine. This is an internal function/selector value; change carefully.",
    "gwUnitHPTbl": "Maximum hit points. Higher values make the unit or building survive more damage.",
    "gbUnitMPTbl": "Magic/mana capacity field used by spell-capable units. Stored as one unsigned byte.",
    "gbUnitStepsCostTbl": "Construction, training, or production time in the engine's step units. Higher values take longer.",
    "gbUnitStoneCostTbl": "Gold cost stored in tens. A raw value of 60 is displayed by the game as 600 gold.",
    "gbUnitLumberCostTbl": "Lumber cost stored in tens. A raw value of 30 represents 300 lumber.",
    "gbUnitOilCostTbl": "Oil cost stored in tens. A raw value of 50 represents 500 oil.",
    "gUnitMtxSizeTbl.x": "Unit footprint width in map matrices/tiles. Buildings normally use values larger than 1.",
    "gUnitMtxSizeTbl.y": "Unit footprint height in map matrices/tiles. Buildings normally use values larger than 1.",
    "gUnitSelectSizeTbl.x": "Horizontal selection-box size in screen pixels used for clicking and selection.",
    "gUnitSelectSizeTbl.y": "Vertical selection-box size in screen pixels used for clicking and selection.",
    "gbUnitMtxRangeTbl": "Matrix/footprint range used for spatial interaction and proximity checks.",
    "gbUnitCompRangeTbl": "Attack/acquisition range used by computer-controlled AI.",
    "gbUnitPlayerRangeTbl": "Attack/acquisition range used for player-controlled behavior.",
    "gbUnitArmorTbl": "Base armor. Armor reduces applicable incoming basic damage.",
    "gbMultiSelectTbl": "Whether this type can participate in normal multi-selection. Usually 0 = no and 1 = yes.",
    "gbKillPriorityTbl": "AI target priority. Higher-priority targets are generally preferred when choosing what to attack.",
    "gbUnitStrengthTbl": "Basic attack damage before upgrades. Warcraft II damage combines basic and piercing damage differently against armor.",
    "gbUnitPierceTbl": "Piercing attack damage. Piercing damage is the portion that is less affected by armor.",
    "gbUnitHasAttackUpgradeTbl": "Whether weapon upgrades apply to this unit. Usually 0 = no and 1 = yes.",
    "gbUnitHasArmorUpgradeTbl": "Whether armor upgrades apply to this unit. Usually 0 = no and 1 = yes.",
    "gbUnitBulletTbl": "Projectile/bullet type ID used for the attack animation and impact. 29 is BT_NONE for non-projectile attacks.",
    "gbUnitClassTbl": "Movement class used by pathing: land, air, or water-class behavior. This is an engine enum, not a bitmask.",
    "gbUnitDecayTbl": "Decay behavior/time selector used for corpses and temporary objects. Zero normally means no special decay entry.",
    "gbRepairPri": "Repair priority used when workers or AI choose damaged repair targets.",
    "gbRClickActionTbl": "Default right-click behavior: 0 none, 1 attacker, 2 mover, 3 peon, 4 tanker, 5 bomber, 6 transport.",
    "gwUnitScoreTbl": "Score awarded for killing this unit or building.",
    "gbUnitTargetTbl": "Allowed target-class flags controlling whether the unit may attack land, air, sea, and related target classes.",
    "gUnitIsTbl": "32-bit identity/capability bitmask such as fleshy, walking, attacker, building, flying, or naval. Edit bits carefully.",
    "gbUpgradeStepsCostTbl": "Research time in engine step units. Higher values make the upgrade take longer.",
    "gwUpgradeGoldCostTbl": "Gold required to research this upgrade. Unlike unit cost bytes, this is stored as the full value.",
    "gwUpgradeLumberCostTbl": "Lumber required to research this upgrade, stored as the full value.",
    "gwUpgradeOilCostTbl": "Oil required to research this upgrade, stored as the full value.",
    "gwUpgradeFrameTbl": "Button/icon frame ID used to display this research item in the command card.",
    "gwUpgradeIndex": "Technology category/index set by the research, such as swords, arrows, shields, boat upgrades, or spell technology.",
    "glUpgradeBit": "32-bit completion bitmask applied when research finishes. It controls the exact level, spell, or technology flag granted.",
}


def field_help(field: Field) -> str:
    return FIELD_HELP.get(field.source, "Source-defined Warcraft II DAT field.")


BULLETS = ("Lightning", "Hammer", "Fireball", "Fire Shield", "Flame Spin", "Blizzard", "Rot", "Human Battle",
           "Exorcism", "Heal", "Death Knight Attack", "Rune", "Typhoon", "Stone", "Bolt", "Arrow", "Axe",
           "Human Torpedo", "Orc Torpedo", "Light Fire", "Heavy Fire", "Catapult Hit", "Sparkle", "Boom Fire",
           "Cannon Ball", "Cannon Fire", "Cannon Boom", "Demon Fire", "Black X", "None")
TECHS = ("Arrows", "Swords", "Shields", "Boat Attack", "Boat Armor", "Boat Speed", "Catapult Damage", "Ranger",
         "Longbow", "Scouts", "Marksmanship")
SPELL_TECHS = ("Vision", "Heal", "Area Heal", "Exorcism", "Fire Shield", "Fireball", "Slow", "Invisibility",
               "Polymorph", "Blizzard", "Eye of Kilrogg", "Bloodlust", "Hallucinate", "Raise Dead", "Drain Life",
               "Whirlwind", "Haste", "Unholy Armor", "Runes", "Death and Decay", "Clerics")
UNIT_BITS = ((0x1, "Walking"), (0x2, "Flyer"), (0x4, "Rolling"), (0x8, "Ship"), (0x10, "Monster"),
             (0x20, "Building"), (0x40, "Submarine"), (0x80, "Sees Subs"), (0x100, "Peon"), (0x200, "Tanker"),
             (0x400, "Transport"), (0x800, "Oil Rig"), (0x1000, "Town Hall"), (0x2000, "Dead"),
             (0x4000, "Attacks Ground"), (0x8000, "Undead"), (0x10000, "Shore Building"), (0x20000, "Caster"),
             (0x40000, "Lumber"), (0x80000, "Attacker"), (0x100000, "Tower"), (0x200000, "Oil Patch"),
             (0x400000, "Gold Mine"), (0x800000, "NPC"), (0x1000000, "Returns Oil"), (0x2000000, "Bomber"),
             (0x4000000, "Wizard"), (0x8000000, "Fleshy"))


def field_options(field: Field, row: int = 0) -> tuple[tuple[int, str], ...]:
    source = field.source
    if source == "gwBuildGroup": return tuple((i, name) for i, name in enumerate(GROUP_NAMES))
    if source in {"gbMultiSelectTbl", "gbUnitHasAttackUpgradeTbl", "gbUnitHasArmorUpgradeTbl"}: return ((0, "No"), (1, "Yes"))
    if source == "gbUnitBulletTbl": return tuple(enumerate(BULLETS))
    if source == "gbUnitClassTbl": return ((0, "Land"), (1, "Air"), (2, "Water"), (3, "Water — can dock"))
    if source == "gbRClickActionTbl": return ((0, "None"), (1, "Attacker"), (2, "Mover"), (3, "Peon"), (4, "Tanker"), (5, "Bomber"), (6, "Transport"))
    if source == "gbUnitTargetTbl":
        return ((0, "None"), (1, "Land"), (2, "Sea"), (3, "Land + Sea"), (4, "Air"), (5, "Land + Air"), (6, "Sea + Air"), (7, "Any"))
    if source == "gwUpgradeIndex":
        names = TECHS if row < 32 else SPELL_TECHS
        return tuple(enumerate(names))
    if source == "glUpgradeBit":
        if row < 32:
            return ((0x1, "Arrows 1"), (0x2, "Arrows 2"), (0x4, "Swords 1"), (0x8, "Swords 2"),
                    (0x10, "Shields 1"), (0x20, "Shields 2"), (0x40, "Boat Attack 1"), (0x80, "Boat Attack 2"),
                    (0x100, "Boat Armor 1"), (0x200, "Boat Armor 2"), (0x400, "Boat Speed 1"), (0x800, "Boat Speed 2"),
                    (0x1000, "Catapult Damage 1"), (0x2000, "Catapult Damage 2"), (0x10000, "Ranger"),
                    (0x20000, "Longbow"), (0x40000, "Scouts"), (0x80000, "Marksmanship"))
        return tuple((1 << i, name) for i, name in enumerate(SPELL_TECHS))
    return ()


def option_label(field: Field, value: int, row: int = 0) -> str | None:
    for number, label in field_options(field, row):
        if number == value: return label
    if field.source == "gUnitIsTbl":
        labels = [label for bit, label in UNIT_BITS if value & bit]
        return " + ".join(labels) if labels else "None"
    return None


UNIT_NAMES = (
    "Footman", "Grunt", "Peasant", "Peon", "Ballista", "Catapult", "Knight", "Ogre",
    "Archer", "Axethrower", "Mage", "Death Knight", "Paladin", "Ogre Mage", "Dwarves", "Goblin Sappers",
    "Attack Peasant", "Attack Peon", "Ranger", "Berserker", "Alleria", "Teron Gorefiend", "Kurdran and Sky'ree", "Dentarg",
    "Khadgar", "Grom Hellscream", "Human Oil Tanker", "Orc Oil Tanker", "Human Transport", "Orc Transport",
    "Elven Destroyer", "Troll Destroyer", "Battleship", "Juggernaught", "Unused A100", "Deathwing",
    "Human Minelayer", "Orc Minelayer", "Gnomish Submarine", "Giant Turtle", "Flying Machine", "Goblin Zeppelin",
    "Gryphon Rider", "Dragon", "Turalyon", "Eye of Kilrogg", "Danath", "Korgath Bladefist", "Unused A5", "Cho'gall",
    "Lothar", "Gul'dan", "Uther Lightbringer", "Zuljin", "Unused A600", "Skeleton", "Daemon", "Critter",
    "Farm", "Pig Farm", "Human Barracks", "Orc Barracks", "Church", "Altar of Storms", "Human Scout Tower", "Orc Scout Tower",
    "Stables", "Ogre Mound", "Gnomish Inventor", "Goblin Alchemist", "Gryphon Aviary", "Dragon Roost", "Human Shipyard", "Orc Shipyard",
    "Town Hall", "Great Hall", "Elven Lumber Mill", "Troll Lumber Mill", "Human Foundry", "Orc Foundry", "Mage Tower", "Temple of the Damned",
    "Human Blacksmith", "Orc Blacksmith", "Human Refinery", "Orc Refinery", "Human Oil Platform", "Orc Oil Platform",
    "Keep", "Stronghold", "Castle", "Fortress", "Gold Mine", "Oil Patch", "Human Start Location", "Orc Start Location",
    "Human Guard Tower", "Orc Guard Tower", "Human Cannon Tower", "Orc Cannon Tower", "Circle of Power", "Dark Portal", "Runestone",
    "Human Wall", "Orc Wall", "Dead Body", "Destroyed 1x1", "Destroyed 2x2", "Destroyed 3x3", "Destroyed 4x4",
)

UPGRADE_NAMES = (
    "Human Sword 1", "Human Sword 2", "Orc Axe 1", "Orc Axe 2", "Human Arrow 1", "Human Arrow 2", "Orc Spear 1", "Orc Spear 2",
    "Human Shield 1", "Human Shield 2", "Orc Shield 1", "Orc Shield 2", "Human Boat Attack 1", "Human Boat Attack 2",
    "Orc Boat Attack 1", "Orc Boat Attack 2", "Human Boat Armor 1", "Human Boat Armor 2", "Orc Boat Armor 1", "Orc Boat Armor 2",
    "Catapult Damage 1", "Catapult Damage 2", "Ballista Damage 1", "Ballista Damage 2", "Ranger", "Longbow", "Human Scouts", "Human Marksmanship",
    "Berserker", "Light Axes", "Orc Scouts", "Orc Marksmanship", "Ogre Mage", "Paladin", "Holy Vision", "Heal", "Exorcism",
    "Fire Shield", "Fireball", "Slow", "Invisibility", "Polymorph", "Blizzard", "Eye of Kilrogg", "Bloodlust", "Raise Dead",
    "Drain Life", "Whirlwind", "Haste", "Unholy Armor", "Runes", "Death and Decay",
)

GROUP_NAMES = tuple([f"Unit group {i}" for i in range(110)] + [
    "Loaded Human Peon", "Loaded Orc Peon", "Gold Human Peon", "Gold Orc Peon", "Human Tanker Oil", "Orc Tanker Oil",
    "Generic Build", "Human Shipyard Build", "Orc Shipyard Build", "Human Oilrig Build", "Orc Oilrig Build",
    "Human Refinery Build", "Orc Refinery Build", "Human Foundry Build", "Orc Foundry Build", "Human Wall Build", "Orc Wall Build",
])


def _unit_views(has_orc_swamp: bool) -> tuple[View, ...]:
    # Exact field-major offsets from UNITDATA.ARR data_struct (classic source).
    units = (
        Field("Build Group", "gwBuildGroup", 0, "u16", 2),
        Field("Unmask", "gUnitUnmaskTbl", 1236, "u32", 4),
        Field("Hit Points", "gwUnitHPTbl", 1676, "u16", 2),
        Field("MP", "gbUnitMPTbl", 1896, "u8", 1),
        Field("Time / Steps", "gbUnitStepsCostTbl", 2006, "u8", 1),
        Field("Gold Cost (×10)", "gbUnitStoneCostTbl", 2116, "u8", 1),
        Field("Lumber Cost (×10)", "gbUnitLumberCostTbl", 2226, "u8", 1),
        Field("Oil Cost (×10)", "gbUnitOilCostTbl", 2336, "u8", 1),
        Field("Matrix Width", "gUnitMtxSizeTbl.x", 2446, "s16", 4), Field("Matrix Height", "gUnitMtxSizeTbl.y", 2448, "s16", 4),
        Field("Select Width", "gUnitSelectSizeTbl.x", 2886, "s16", 4), Field("Select Height", "gUnitSelectSizeTbl.y", 2888, "s16", 4),
        Field("Matrix Range", "gbUnitMtxRangeTbl", 3326, "u8", 1), Field("Computer Range", "gbUnitCompRangeTbl", 3436, "u8", 1),
        Field("Player Range", "gbUnitPlayerRangeTbl", 3546, "u8", 1), Field("Armor", "gbUnitArmorTbl", 3656, "u8", 1),
        Field("Multi Select", "gbMultiSelectTbl", 3766, "u8", 1), Field("Kill Priority", "gbKillPriorityTbl", 3876, "u8", 1),
        Field("Basic Damage", "gbUnitStrengthTbl", 3986, "u8", 1), Field("Piercing Damage", "gbUnitPierceTbl", 4096, "u8", 1),
        Field("Attack Upgrade", "gbUnitHasAttackUpgradeTbl", 4206, "u8", 1), Field("Armor Upgrade", "gbUnitHasArmorUpgradeTbl", 4316, "u8", 1),
        Field("Projectile", "gbUnitBulletTbl", 4426, "u8", 1), Field("Movement Class", "gbUnitClassTbl", 4536, "u8", 1),
        Field("Decay", "gbUnitDecayTbl", 4646, "u8", 1), Field("Repair Priority", "gbRepairPri", 4756, "u8", 1),
        Field("Score", "gwUnitScoreTbl", 4924, "u16", 2), Field("Allowed Targets", "gbUnitTargetTbl", 5144, "u8", 1),
        Field("Unit Flags", "gUnitIsTbl", 5254, "u32", 4),
    )
    groups = [
        Field("Always", "gwLoadAlwaysTbl", 220, "u16", 2), Field("Summer", "gwSummerLoadTbl", 474, "u16", 2),
        Field("Snow", "gwSnowLoadTbl", 728, "u16", 2), Field("Swamp", "gwSwampLoadTbl", 982, "u16", 2),
    ]
    if has_orc_swamp: groups.append(Field("Orc Swamp", "gwOrcSwampLoadTbl", 5694, "u16", 2))
    right = (Field("Right-click Action", "gbRClickActionTbl", 4866, "u8", 1),)
    return (View("Units", UNIT_NAMES, units), View("Unit Groups", GROUP_NAMES, tuple(groups)), View("Men Right-click", UNIT_NAMES[:58], right))


UPGRADE_VIEW = View("Upgrades", UPGRADE_NAMES, (
    Field("Time / Steps", "gbUpgradeStepsCostTbl", 0, "u8", 1), Field("Gold Cost", "gwUpgradeGoldCostTbl", 52, "u16", 2),
    Field("Lumber Cost", "gwUpgradeLumberCostTbl", 156, "u16", 2), Field("Oil Cost", "gwUpgradeOilCostTbl", 260, "u16", 2),
    Field("Button Frame", "gwUpgradeFrameTbl", 364, "u16", 2), Field("Tech Index", "gwUpgradeIndex", 468, "u16", 2),
    Field("Upgrade Bit", "glUpgradeBit", 572, "u32", 4),
))


def detect_views(path: str | Path, size: int) -> tuple[View, ...]:
    name = Path(path).name.lower()
    if size == 780 or "upgrade" in name: return (UPGRADE_VIEW,)
    if size == 5948: return _unit_views(True)
    if size == 5694: return _unit_views(False)
    return ()
