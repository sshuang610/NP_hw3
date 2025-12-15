"""Copy curated game templates into a destination directory.

Usage examples::

    python -m tools.create_game_template --list
    python -m tools.create_game_template --template connect4_cli --dest ./games/my_connect4

The command copies the template folder verbatim (including README, manifest,
server/client scripts). Use ``--name`` to replace the ``{{GAME_NAME}}`` token
inside copied files.
"""

import argparse
import shutil
from pathlib import Path


def list_templates(root: Path) -> None:
    print("Available templates:")
    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        print(f" - {child.name}")


def replace_tokens(path: Path, replacements: dict) -> None:
    if not replacements:
        return
    if path.suffix not in {".txt", ".md", ".json", ".py", ".cfg", ".ini"}:
        return
    text = path.read_text(encoding="utf-8")
    changed = False
    for key, value in replacements.items():
        token = f"{{{{{key}}}}}"
        if token in text:
            text = text.replace(token, value)
            changed = True
    if changed:
        path.write_text(text, encoding="utf-8")


def copy_template(root: Path, template: str, dest: Path, game_name: str, overwrite: bool) -> None:
    source = root / template
    if not source.exists():
        raise SystemExit(f"Template '{template}' not found. Run with --list to see options.")
    if dest.exists() and not overwrite:
        raise SystemExit(f"Destination '{dest}' already exists. Use --overwrite to replace it.")
    shutil.copytree(source, dest, dirs_exist_ok=overwrite)
    replacements = {"GAME_NAME": game_name} if game_name else {}
    if replacements:
        for file in dest.rglob("*"):
            if file.is_file():
                replace_tokens(file, replacements)
    print(f"Created template at {dest}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy a game template into a target directory")
    parser.add_argument("--template", help="Template folder name under game_templates")
    parser.add_argument("--dest", help="Destination directory for the new game")
    parser.add_argument("--name", help="Human readable game name for token replacement", default="")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite destination if it exists")
    parser.add_argument("--list", action="store_true", help="List available templates and exit")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent / "game_templates"
    if not root.exists():
        raise SystemExit("game_templates directory is missing")

    if args.list:
        list_templates(root)
        return

    if not args.template or not args.dest:
        parser.error("--template and --dest are required unless using --list")

    dest_path = Path(args.dest).expanduser().resolve()
    game_name = args.name or dest_path.name.replace("_", " ")
    copy_template(root, args.template, dest_path, game_name, args.overwrite)


if __name__ == "__main__":
    main()
