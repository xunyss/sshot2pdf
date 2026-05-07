"""Test: build PDF from temp/ PNGs and verify page size is normalized to 297mm wide."""
import logging
from pathlib import Path

logging.basicConfig(level=logging.WARNING)

import pypdf
from sshot2pdf.capture import build_pdf

captures_dir = Path(__file__).parent / "temp"
assert sorted(captures_dir.glob("page_*.png")), "temp/ 에 page_*.png 없음"

pdf = build_pdf(captures_dir)
print(f"\nOutput: {pdf.name}  ({pdf.stat().st_size / 1024 / 1024:.2f}MB)")

reader = pypdf.PdfReader(pdf)
print(f"Pages : {len(reader.pages)}")
box = reader.pages[0].mediabox
w_mm = float(box.width) / 72 * 25.4
h_mm = float(box.height) / 72 * 25.4
print(f"Page 1: {w_mm:.1f} x {h_mm:.1f} mm")
