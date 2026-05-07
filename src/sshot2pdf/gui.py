from __future__ import annotations

import logging
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

logger = logging.getLogger(__name__)

import Quartz

from sshot2pdf.capture import Capturer
from sshot2pdf.windows import list_windows

KEY_PAGE_DOWN = 121
KEY_DOWN_ARROW = 125


class AppWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("sshot2pdf")
        self.root.resizable(False, False)
        self._set_app_icon()

        self._windows: list[dict] = []
        self._capturer: Capturer | None = None

        self._build_ui()
        self._refresh_windows()

    # ── macOS identity ───────────────────────────────────────────────────

    def _set_app_icon(self) -> None:
        """Set Dock/Cmd+Tab icon after Tk() is initialized, and override About panel."""
        try:
            from AppKit import NSApplication, NSImage
            icon_path = Path(__file__).parent.parent.parent / "icon.png"
            if not icon_path.exists():
                return
            self._ns_icon = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
            if not self._ns_icon:
                return
            NSApplication.sharedApplication().setApplicationIconImage_(self._ns_icon)
        except Exception:
            self._ns_icon = None

        # Intercept tkinter's About menu action so we can pass our icon explicitly
        self.root.createcommand("tkAboutDialog", self._show_about)

    def _show_about(self) -> None:
        try:
            from AppKit import NSApplication
            options = {}
            if getattr(self, "_ns_icon", None):
                options["ApplicationIcon"] = self._ns_icon
            NSApplication.sharedApplication().orderFrontStandardAboutPanelWithOptions_(options)
        except Exception:
            pass

    # ── UI construction ──────────────────────────────────────────────────

    def _build_ui(self) -> None:
        pad = {"padx": 12, "pady": 4}

        # Window selector
        frm_win = tk.Frame(self.root)
        frm_win.pack(fill="x", **pad)
        tk.Label(frm_win, text="창 선택:").pack(anchor="w")

        frm_combo = tk.Frame(frm_win)
        frm_combo.pack(fill="x")
        self._win_var = tk.StringVar()
        self._combo = ttk.Combobox(
            frm_combo, textvariable=self._win_var, state="readonly", width=36
        )
        self._combo.pack(side="left", fill="x", expand=True)
        tk.Button(frm_combo, text="새로고침", command=self._refresh_windows).pack(
            side="left", padx=(6, 0)
        )

        # Key choice
        frm_key = tk.Frame(self.root)
        frm_key.pack(fill="x", **pad)
        tk.Label(frm_key, text="전환 키:").pack(anchor="w")
        self._key_var = tk.IntVar(value=KEY_PAGE_DOWN)
        tk.Radiobutton(
            frm_key, text="Page Down", variable=self._key_var, value=KEY_PAGE_DOWN
        ).pack(side="left")
        tk.Radiobutton(
            frm_key, text="↓ Arrow", variable=self._key_var, value=KEY_DOWN_ARROW
        ).pack(side="left", padx=(12, 0))

        # Delay
        frm_delay = tk.Frame(self.root)
        frm_delay.pack(fill="x", **pad)
        tk.Label(frm_delay, text="대기 시간:").pack(side="left")
        self._delay_var = tk.StringVar(value="1.5")
        tk.Entry(frm_delay, textvariable=self._delay_var, width=6).pack(side="left", padx=4)
        tk.Label(frm_delay, text="초").pack(side="left")

        # Separator
        ttk.Separator(self.root, orient="horizontal").pack(fill="x", padx=12, pady=6)

        # Crop options
        frm_crop = tk.Frame(self.root)
        frm_crop.pack(fill="x", **pad)
        tk.Label(frm_crop, text="여백 처리:").pack(anchor="w")

        self._crop_mode_var = tk.StringVar(value="first")
        for label, value in [
            ("감지안함 (전체화면)", "none"),
            ("첫 페이지 자동감지", "first"),
            ("매 페이지 자동감지", "every"),
        ]:
            tk.Radiobutton(
                frm_crop, text=label, variable=self._crop_mode_var, value=value
            ).pack(anchor="w")

        # Separator
        ttk.Separator(self.root, orient="horizontal").pack(fill="x", padx=12, pady=6)

        # Status
        frm_status = tk.Frame(self.root)
        frm_status.pack(fill="x", **pad)
        self._status_var = tk.StringVar(value="상태: 대기 중")
        tk.Label(frm_status, textvariable=self._status_var, anchor="w").pack(fill="x")
        self._pages_var = tk.StringVar(value="캡처: 0 페이지")
        tk.Label(frm_status, textvariable=self._pages_var, anchor="w").pack(fill="x")

        # Buttons
        frm_btn = tk.Frame(self.root)
        frm_btn.pack(pady=(6, 14))
        self._btn_start = tk.Button(
            frm_btn, text="  시작  ", width=10, command=self._on_start
        )
        self._btn_start.pack(side="left", padx=6)
        self._btn_stop = tk.Button(
            frm_btn, text="  종료  ", width=10, command=self._on_stop, state="disabled"
        )
        self._btn_stop.pack(side="left", padx=6)

    # ── Window list ──────────────────────────────────────────────────────

    def _refresh_windows(self) -> None:
        self._windows = list_windows()
        labels = [w["label"] for w in self._windows]
        self._combo["values"] = labels
        if labels:
            self._combo.current(0)
        else:
            self._win_var.set("")

    def _selected_window(self) -> dict | None:
        idx = self._combo.current()
        if idx < 0 or idx >= len(self._windows):
            return None
        return self._windows[idx]

    # ── Start / Stop ─────────────────────────────────────────────────────

    def _on_start(self) -> None:
        # Must request Screen Recording permission from the main thread.
        # CGWindowListCreateImage called from a background thread will deadlock
        # while waiting for the TCC popup — so we gate here first.
        if not Quartz.CGPreflightScreenCaptureAccess():
            logger.warning("screen capture permission denied, requesting access")
            Quartz.CGRequestScreenCaptureAccess()
            messagebox.showwarning(
                "권한 필요",
                "시스템 설정 → 개인정보 보호 및 보안에서\n"
                "아래 세 가지를 허용한 뒤 앱을 재시작해 주세요:\n\n"
                "  • 화면 기록\n"
                "  • 자동화\n"
                "  • 손쉬운 사용",
            )
            return

        win = self._selected_window()
        if win is None:
            messagebox.showerror("오류", "캡처할 창을 선택해 주세요.")
            return

        try:
            delay = float(self._delay_var.get())
            if delay < 0.1:
                raise ValueError
        except ValueError:
            messagebox.showerror("오류", "대기 시간은 0.1 이상의 숫자여야 합니다.")
            return

        captures_dir = Path.cwd() / "temp"
        logger.info(
            "start: win=%r key=%d delay=%.1f crop_mode=%s temp=%s",
            win["label"], self._key_var.get(), delay, self._crop_mode_var.get(), captures_dir,
        )
        self._capturer = Capturer(
            window_id=win["id"],
            owner=win["owner"],
            key_code=self._key_var.get(),
            delay=delay,
            captures_dir=captures_dir,
            on_page_cb=self._on_page,
            on_done_cb=self._on_done,
            crop_mode=self._crop_mode_var.get(),
        )

        self._btn_start.config(state="disabled")
        self._btn_stop.config(state="normal")
        self._pages_var.set("캡처: 0 페이지")
        self._status_var.set("상태: 캡처 중…")
        self._capturer.start()

    def _on_stop(self) -> None:
        logger.info("stop requested by user")
        if self._capturer:
            self._capturer.stop()
        self._btn_stop.config(state="disabled")
        self._status_var.set("상태: 중지 중…")

    # ── Callbacks from Capturer thread ───────────────────────────────────

    def _on_page(self, page: int) -> None:
        self.root.after(0, lambda: self._pages_var.set(f"캡처: {page} 페이지"))

    def _on_done(self, pdf_path: Path | None, error: Exception | None) -> None:
        logger.info("on_done: pdf=%s error=%s", pdf_path, error)

        def _update() -> None:
            self._btn_start.config(state="normal")
            self._btn_stop.config(state="disabled")
            if error is not None:
                self._status_var.set("상태: 오류")
                messagebox.showerror("오류", str(error))
            elif pdf_path:
                self._status_var.set(f"완료: {pdf_path.name}")
                messagebox.showinfo("완료", f"PDF 저장됨:\n{pdf_path}")
            else:
                self._status_var.set("상태: 대기 중")

        self.root.after(0, _update)

    # ── Main loop ────────────────────────────────────────────────────────

    def run(self) -> None:
        self.root.mainloop()
