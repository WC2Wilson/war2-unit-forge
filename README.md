# Warcraft II Remaster Unit Forge 0.5.0 Runtime

Runtime custom-unit workbench for **Warcraft II Remastered x86**. Unit Forge attaches to the running game, builds one or more custom unit definitions from existing engine slots, applies verified live table/card/graphics/identity changes, and restores them without permanently modifying `Warcraft II.exe`.

Version 0.5.0 uses the multi-unit registry and a runtime-only game-modification workflow.

## Multi-unit runtime editing

- **Multiple custom units in one project.**
- **Any unit-data target ID 0–109** can be selected for runtime editing.
- Gameplay/stat donor, graphics/animation donor, and icon/portrait donor are independent.
- Every exposed unit-data field can be overridden numerically instead of being locked to the donor.
- Targets 0–57 can optionally receive a native training button. Targets 58–109 use **No producer** because Warcraft's native `bldg_build_man` unit-training path is limited to the men/unit range.
- Runtime writes are transactional and can be restored.
- Runtime-only game modification; project JSON is the persistent configuration.

## Supported builds

The included profile/fingerprint layer recognizes the Remastered x86 builds carried by the original Forge, including 1.0.2.2818. Live hooks verify the expected executable layout before use.

## Install

1. Install Python 3.12 or newer.
2. Run `INSTALL_REQUIREMENTS.bat`.
3. Start Warcraft II Remastered.
4. Enter an offline/private match.
5. Run `START_UNIT_FORGE.bat`.
6. Use **Live Install → Attach + Resolve gCards**.
7. Click **Resolve DAT Arrays**.

## Build a custom unit

1. Open **Unit Registry**.
2. Add a row or select the existing row.
3. In **Unit Editor**, choose:
   - **Clone source** — gameplay/stat donor;
   - **Target slot** — unit ID whose runtime definition will be replaced;
   - **Graphics + animation donor**;
   - **Icon + portrait donor**;
   - optional producer and button slot.
4. Open **Unit Data**. Double-click any field or type a raw numeric value in the detail panel. Those overrides are applied on top of the donor row.
5. Save the project JSON if you want to reuse the definition later.
6. Open **Live Install** and choose **INSTALL CURRENT UNIT**, or use **INSTALL ALL REGISTRY UNITS** for the whole project.
7. Reselect the object in Warcraft so its status/card UI redraws.

## Runtime systems applied by a complete install

Depending on the selected target/donors, the Forge can update the live unit-data arrays, graphics aliases/pointers, status identity, sound identity, unit-group classification, action-sequence routing, command cards, producer metadata, and producer compatibility guard. Writes are read back and validated.

## Unit IDs and training

Unit Forge now lets you select the complete 0–109 unit-data range instead of forcing a single custom target. That does **not** mean every enum slot is equally safe or has every native subsystem initialized.

IDs 16 and 17 are useful dormant initialized combat-worker slots. Other unused IDs can need additional bootstrap behavior, while ordinary IDs deliberately replace an existing unit/building definition for the current run. Test custom targets individually.

Native Barracks-style training uses unit IDs 0–57. For IDs 58–109 set **Producer building → No producer** and use the unit through runtime creation/triggers or the engine behavior appropriate for that object type.

## Restore

Use **Restore All Live Changes** before detaching when possible. Exiting Warcraft also discards the runtime changes. Project JSON files remain on disk; Warcraft data files and `Warcraft II.exe` are not rewritten by this public runtime edition.

## Testing

`SELF_TEST.bat` runs the package's offline structural checks. The **Native Test** page can create and inspect a selected runtime unit through the simulation-thread adapter after a successful live install.

## Multiplayer

Do not treat these runtime unit changes as multiplayer-safe. They can change simulation data and require identical state on every peer. This project is not marked `M`.
