"""Stage 1: PDF -> text. Needs poppler's pdftotext on PATH.

Act uses raw mode (keeps marginal notes on their own lines instead of bleeding
them into the body); Rules use -layout (preserves the Third Schedule table).
"""
import shutil
import subprocess
import sys
from pathlib import Path

DATA = Path(__file__).parent / "data"

SOURCES = {
    "act2023.txt": ("DPDP_Act_2023.pdf", ["-enc", "UTF-8"]),
    "rules2025.txt": ("Digital_Personal_Data_Protection_Rules_2025.pdf",
                      ["-layout", "-enc", "UTF-8"]),
}


def main(pdf_dir: Path) -> int:
    if not shutil.which("pdftotext"):
        sys.exit("pdftotext not found. Install poppler-utils and re-run.")
    DATA.mkdir(exist_ok=True)
    for out, (pattern, flags) in SOURCES.items():
        matches = sorted(pdf_dir.glob(f"*{pattern}"))
        if not matches:
            sys.exit(f"no PDF matching *{pattern} in {pdf_dir}")
        subprocess.run(
            ["pdftotext", *flags, str(matches[0]), str(DATA / out)], check=True
        )
        print(f"{matches[0].name} -> data/{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else ".")))
