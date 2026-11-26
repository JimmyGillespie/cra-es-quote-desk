"""
apply_layering.py - switch on Dynamic Layering in your CRA project. Standard library only.

Run it ONCE, from the project folder (the one that contains app.py), after copying the new files in:

    py apply_layering.py              apply the changes, then rebuild manifest.json and the upload folder
    py apply_layering.py --check      only report what it would change

It makes five small edits (each is skipped if it is already there):
  cra/pipeline.py    the default account selection becomes "Latest account only" (one set of locations)
  views/common.py    adds the "Layers" step between Hazards and Analyses   (+ a tick when a layer has been created)
  app.py             imports the new page and registers it

If any anchor line cannot be found, NOTHING is changed and you are told which one. Your original files are copied to
..\\layering_backup first. When the edits are in, it runs prepare_deploy.py (if present) to rebuild manifest.json and write
the clean upload folder  ..\\cra_upload_these_v3 .
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

NEEDED = ["cra/layering.py", "cra/rms/layer_api.py", "cra/rms/demo_layering.py", "views/layering.py"]

PATCHES = [
    # (file, description, regex to find, replacement, text that proves it is already applied, required?)
    ("cra/pipeline.py", "default account selection = latest account (one set of locations)",
     r'(return\s+sub\.get\(\s*"account_mode"\s*,\s*)"all"(\s*\))', r'\1"latest"\2',
     r'"account_mode"\s*,\s*"latest"', True),
    ("views/common.py", "add the Layers step to the step bar",
     r'\("hazards",\s*"Hazards"\)\s*,\s*\("analyses",\s*"Analyses"\)',
     '("hazards", "Hazards"), ("layers", "Layers"), ("analyses", "Analyses")',
     r'\("layers",\s*"Layers"\)', True),
    ("views/common.py", "tick the Layers step once a layer has been created",
     r'"analyses":\s*bool\(sel\)\s*,', '"layers": "layer_runs" in parts, "analyses": bool(sel),',
     r'"layers":\s*"layer_runs"', False),
    ("app.py", "import the Layers page",
     r'(from views import [^\n#]*?\bhazards,\s*)(report\b)', r'\1layers, \2',
     r'from views import [^\n#]*\blayers\b', True),
    ("app.py", "register the Layers page",
     r'"hazards":\s*hazards\s*,', '"hazards": hazards, "layers": layers,',
     r'"layers":\s*layers', True),
]


def main() -> None:
    check_only = "--check" in sys.argv
    if not (ROOT / "app.py").exists():
        sys.exit("app.py not found next to this script. Run it from the project folder.")
    missing = [f for f in NEEDED if not (ROOT / f).exists()]
    if missing:
        sys.exit("These new files are not in the project yet: " + ", ".join(missing)
                 + "\nExtract the layering zip into the project folder first (choose Replace), then run this again.")

    texts, notes, fatal = {}, [], []
    for rel, what, find, repl, proof, required in PATCHES:
        path = ROOT / rel
        if not path.exists():
            (fatal if required else notes).append(f"{rel}: file not found")
            continue
        text = texts.get(rel) or path.read_text(encoding="utf-8")
        if re.search(proof, text):
            notes.append(f"already there : {rel} - {what}")
            texts[rel] = text
            continue
        new, n = re.subn(find, repl, text, count=1)
        if n != 1:
            msg = f"{rel}: could not find the line to change for '{what}'"
            (fatal if required else notes).append(msg if required else f"skipped       : {msg} (optional)")
            texts[rel] = text
            continue
        texts[rel] = new
        notes.append(f"will change   : {rel} - {what}")

    for rel, text in texts.items():                                   # nothing is written unless every file still parses
        if rel.endswith(".py"):
            try:
                ast.parse(text)
            except SyntaxError as exc:
                fatal.append(f"{rel}: would not be valid Python after the edit ({exc})")
    print("\n".join(notes))
    if fatal:
        sys.exit("\nNOTHING WAS CHANGED.\n  " + "\n  ".join(fatal))
    if check_only:
        print("\n--check only: no files written.")
        return

    backup = ROOT.parent / "layering_backup"
    changed = [rel for rel, text in texts.items() if text != (ROOT / rel).read_text(encoding="utf-8")]
    for rel in changed:
        dst = backup / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dst)
        (ROOT / rel).write_text(texts[rel], encoding="utf-8", newline="\n")
    print(f"\nEdited {len(changed)} file(s)." + (f" Originals saved in {backup}" if changed else ""))

    prep = ROOT / "prepare_deploy.py"
    if prep.exists():
        out = ROOT.parent / "cra_upload_these_v3"
        print("\nRebuilding manifest.json and the upload folder...")
        r = subprocess.run([sys.executable, str(prep), "--out", str(out)], cwd=str(ROOT))
        if r.returncode:
            sys.exit("prepare_deploy.py reported a problem - see the message above.")
        print(f"\nDone. Upload everything inside  {out}  to GitHub, then press Update Now in Posit Connect.")
    else:
        print("\nprepare_deploy.py is not in this folder, so manifest.json was not rebuilt.")


if __name__ == "__main__":
    main()
