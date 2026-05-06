from __future__ import annotations

import logging
from pathlib import Path


def _patch_bundle_name() -> None:
    """Override CFBundleName before tkinter starts so the menu bar shows 'sshot2pdf'.

    NSBundle is safe to touch before Tk(); NSApplication is not.
    """
    try:
        from AppKit import NSBundle
        info = NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = "sshot2pdf"
    except Exception:
        pass


def main() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger(__name__).info("sshot2pdf starting")
    _patch_bundle_name()
    from sshot2pdf.gui import AppWindow
    app = AppWindow()
    app.run()


if __name__ == "__main__":
    main()
