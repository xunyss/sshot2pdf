from __future__ import annotations

import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import logging

import Quartz
from Foundation import NSURL
import img2pdf
from PIL import Image, ImageChops

logger = logging.getLogger(__name__)


def take_screenshot(window_id: int, path: Path) -> None:
    logger.debug("screenshot wid=%d → %s", window_id, path.name)
    image = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        window_id,
        Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageShouldBeOpaque,
    )
    if image is None:
        raise PermissionError(
            "스크린샷을 생성할 수 없습니다.\n\n"
            "시스템 설정 → 개인정보 보호 및 보안에서\n"
            "아래 세 가지 권한을 허용한 뒤 앱을 재시작해 주세요:\n"
            "  • 화면 기록\n"
            "  • 자동화\n"
            "  • 손쉬운 사용"
        )
    url = NSURL.fileURLWithPath_(str(path))
    dest = Quartz.CGImageDestinationCreateWithURL(url, "public.png", 1, None)
    if dest is None:
        raise RuntimeError(f"이미지 목적지 생성 실패: {path}")
    Quartz.CGImageDestinationAddImage(dest, image, None)
    if not Quartz.CGImageDestinationFinalize(dest):
        raise RuntimeError(f"이미지 파일 저장 실패: {path}")


def send_key(owner: str, key_code: int) -> None:
    """Focus window then send a key event via AppleScript.

    key_code: 121 = Page Down, 125 = Down Arrow
    """
    logger.debug("send_key owner=%r key_code=%d", owner, key_code)
    script = f"""
tell application "{owner}" to activate
delay 0.15
tell application "System Events"
    key code {key_code}
end tell
"""
    subprocess.run(["osascript", "-e", script], check=True)


def _autocrop_borders(img: Image.Image, black_threshold: int = 20, border_ratio: float = 0.95) -> Image.Image:
    """Remove borders where >= border_ratio of pixels are near-black.

    Uses a ratio instead of any-pixel detection, so sparse UI elements
    (e.g., a Toolbar strip at the right edge) don't prevent removal
    of the surrounding black letterbox bars.
    """
    gray = img.convert("L")
    w, h = img.size
    data = list(gray.getdata())

    def col_is_border(x: int) -> bool:
        black = sum(1 for y in range(h) if data[y * w + x] <= black_threshold)
        return black / h >= border_ratio

    def row_is_border(y: int) -> bool:
        start = y * w
        black = sum(1 for p in data[start : start + w] if p <= black_threshold)
        return black / w >= border_ratio

    left = 0
    while left < w and col_is_border(left):
        left += 1

    right = w
    while right > left and col_is_border(right - 1):
        right -= 1

    top = 0
    while top < h and row_is_border(top):
        top += 1

    bottom = h
    while bottom > top and row_is_border(bottom - 1):
        bottom -= 1

    if (right - left) < w * 0.10 or (bottom - top) < h * 0.10:
        logger.debug("autocrop: %dx%d → skipped (result too small: %dx%d)", w, h, right - left, bottom - top)
        return img
    logger.debug("autocrop: %dx%d → %dx%d", w, h, right - left, bottom - top)
    return img.crop((left, top, right, bottom))


def _is_mostly_black(img: Image.Image, threshold: float = 0.90, black_val: int = 20) -> bool:
    """Return True if ≥ threshold fraction of pixels are near-black.

    Detects end-of-slideshow screens like "슬라이드 쇼가 끝났습니다" which are
    nearly entirely black with only a thin toolbar and one line of text.
    """
    gray = img.convert("L")
    total = img.width * img.height
    if total == 0:
        logger.debug("is_mostly_black: zero-size image → True")
        return True
    black = sum(1 for p in gray.getdata() if p <= black_val)
    ratio = black / total
    result = ratio >= threshold
    logger.debug("is_mostly_black: ratio=%.3f threshold=%.2f → %s", ratio, threshold, result)
    return result


def crop_image(img: Image.Image, top_px: int = 0, autocrop: bool = True) -> Image.Image:
    """Crop title-bar pixels from top, then remove near-black borders on all sides."""
    if top_px > 0:
        img = img.crop((0, top_px, img.width, img.height))
    if autocrop:
        img = _autocrop_borders(img)
    return img


def is_same_page(img_a: Path, img_b: Path, threshold: float = 0.99) -> bool:
    a = Image.open(img_a).convert("RGB")
    b = Image.open(img_b).convert("RGB")
    if a.size != b.size:
        logger.debug("is_same_page: size mismatch %s vs %s → False", a.size, b.size)
        return False
    diff = ImageChops.difference(a, b)
    total = a.width * a.height
    same = sum(1 for px in diff.getdata() if max(px) < 10)
    ratio = same / total
    result = ratio >= threshold
    logger.debug("is_same_page: %s vs %s ratio=%.4f → %s", img_a.name, img_b.name, ratio, result)
    return result


def build_pdf(captures_dir: Path) -> Path:
    images = sorted(captures_dir.glob("page_*.png"))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = captures_dir.parent / f"output_{ts}.pdf"
    logger.info("build_pdf: %d pages → %s", len(images), out.name)
    with open(out, "wb") as f:
        f.write(img2pdf.convert([str(p) for p in images]))
    return out


class Capturer(threading.Thread):
    def __init__(
        self,
        window_id: int,
        owner: str,
        key_code: int,
        delay: float,
        captures_dir: Path,
        on_page_cb: Callable[[int], None],
        on_done_cb: Callable[[Path | None, Exception | None], None],
        top_px: int = 0,
        autocrop: bool = True,
    ) -> None:
        super().__init__(daemon=True)
        self.window_id = window_id
        self.owner = owner
        self.key_code = key_code
        self.delay = delay
        self.captures_dir = captures_dir
        self.on_page_cb = on_page_cb
        self.on_done_cb = on_done_cb
        self.top_px = top_px
        self.autocrop = autocrop
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self.captures_dir.mkdir(parents=True, exist_ok=True)
        prev: Path | None = None
        page = 1

        logger.info(
            "capturer start: wid=%d owner=%r key=%d delay=%.1fs top_px=%d autocrop=%s",
            self.window_id, self.owner, self.key_code, self.delay, self.top_px, self.autocrop,
        )

        try:
            while not self._stop.is_set():
                path = self.captures_dir / f"page_{page:03d}.png"
                take_screenshot(self.window_id, path)

                img = Image.open(path)
                if self.top_px > 0 or self.autocrop:
                    img = crop_image(img, top_px=self.top_px, autocrop=self.autocrop)
                    img.save(path)

                # End-of-slideshow: "슬라이드 쇼가 끝났습니다" is almost entirely black
                if _is_mostly_black(img):
                    logger.info("end-of-slideshow detected at page %d, stopping", page)
                    path.unlink(missing_ok=True)
                    break

                if prev is not None and is_same_page(prev, path):
                    logger.info("duplicate page detected at page %d, stopping", page)
                    path.unlink(missing_ok=True)
                    break

                logger.info("page %d captured: %s", page, path.name)
                self.on_page_cb(page)

                send_key(self.owner, self.key_code)
                time.sleep(self.delay)

                prev = path
                page += 1

            if self._stop.is_set():
                logger.info("stop event received after page %d", page - 1)

            pdf_path = build_pdf(self.captures_dir) if page > 1 else None
            logger.info("capturer done: pdf=%s", pdf_path)
            self.on_done_cb(pdf_path, None)

        except Exception as exc:
            logger.error("capturer error: %s", exc, exc_info=True)
            self.on_done_cb(None, exc)
            return

    # signature change: on_done_cb receives (pdf_path | None, error | None)
