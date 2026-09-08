from pathlib import Path
import ast
root=Path(__file__).parents[1]
main=(root/"unit_forge.py").read_text(encoding="utf-8")
ast.parse(main)
assert "Unit Forge 0.5.0 Runtime" in main
assert "self.book.add(self.build_tab" not in main
assert "0 <= target < len(UNIT_NAMES)" in main
assert "self.book.add(self.build_tab" not in main and "def _deploy_project_files" not in main
assert not (root/"originals").exists()
for p in root.rglob("*"):
    if "tests" in p.parts: continue
    if p.is_file() and p.suffix.lower() in {".py",".md",".txt"}:
        t=p.read_text(encoding="utf-8",errors="ignore")
        assert ("P" + "SX") not in t and ("Play" + "Station") not in t
print("Unit Forge public runtime checks: PASS")
