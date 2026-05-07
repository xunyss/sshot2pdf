from __future__ import annotations

import logging
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import Quartz
import img2pdf
# noinspection PyUnresolvedReferences
from Foundation import NSURL
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


def _find_content_bbox(
    img: Image.Image,
    dark_threshold: int = 25,
    dark_ratio: float = 0.85,
    min_ratio: float = 0.10,
) -> tuple[int, int, int, int]:
    """Find the bounding box of the main slide content.

    Classifies each row/column as 'dark background' or 'content', then selects
    the LONGEST contiguous content run on each axis. This handles all cases
    without case-by-case logic:
      - Black letterbox / pillarbox bars → dark, removed
      - Thin PDF viewer border lines     → very short content run, skipped
      - macOS title bar (windowed mode)  → short content run, skipped
      - Actual slide                     → longest content run, selected

    Falls back to the full image when the detected area is too small.
    """
    gray = img.convert("L")
    w, h = img.size
    data = list(gray.getdata())

    # Count dark pixels per row and column in a single pass.
    row_dark = [0] * h
    col_dark = [0] * w
    for y in range(h):
        base = y * w
        for x in range(w):
            if data[base + x] <= dark_threshold:
                row_dark[y] += 1
                col_dark[x] += 1

    row_is_dark = [row_dark[y] / w >= dark_ratio for y in range(h)]
    col_is_dark = [col_dark[x] / h >= dark_ratio for x in range(w)]

    def longest_run(is_dark: list[bool]) -> tuple[int, int]:
        best = (0, len(is_dark))
        best_len = 0
        run_start: int | None = None
        for i, dark in enumerate(is_dark):
            if not dark:
                if run_start is None:
                    run_start = i
            else:
                if run_start is not None:
                    run_len = i - run_start
                    if run_len > best_len:
                        best_len, best = run_len, (run_start, i)
                    run_start = None
        if run_start is not None:
            run_len = len(is_dark) - run_start
            if run_len > best_len:
                best = (run_start, len(is_dark))
        return best

    top, bottom = longest_run(row_is_dark)
    left, right = longest_run(col_is_dark)

    if (right - left) < w * min_ratio or (bottom - top) < h * min_ratio:
        logger.debug("find_content_bbox: %dx%d → result too small, keeping full image", w, h)
        return (0, 0, w, h)

    logger.debug("find_content_bbox: %dx%d → (%d,%d,%d,%d)", w, h, left, top, right, bottom)
    return (left, top, right, bottom)


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


def crop_image(img: Image.Image) -> Image.Image:
    """Crop to the main slide content area, removing all surrounding chrome."""
    left, top, right, bottom = _find_content_bbox(img)
    if (left, top, right, bottom) == (0, 0, img.width, img.height):
        return img
    return img.crop((left, top, right, bottom))


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


def _make_max_long_side_layout(max_mm: float = 297.0):
    """긴 쪽(가로/세로 문서 모두)이 max_mm 초과 시 비율 유지로 축소."""
    max_pt = img2pdf.mm_to_pt(max_mm)

    def layout_fun(imgwidth, imgheight, ndpi):
        xdpi, ydpi = ndpi if ndpi else (72, 72)
        w_pt = imgwidth / xdpi * 72
        h_pt = imgheight / ydpi * 72
        long_pt = max(w_pt, h_pt)
        if long_pt > max_pt:
            scale = max_pt / long_pt
            w_pt *= scale
            h_pt *= scale
        return (w_pt, h_pt, w_pt, h_pt)

    return layout_fun


def build_pdf(captures_dir: Path) -> Path:
    images = sorted(captures_dir.glob("page_*.png"))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = captures_dir.parent / f"output_{ts}.pdf"
    logger.info("build_pdf: %d pages → %s", len(images), out.name)
    with open(out, "wb") as f:
        f.write(img2pdf.convert([str(p) for p in images], layout_fun=_make_max_long_side_layout()))
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
        crop_mode: str = "first",
    ) -> None:
        super().__init__(daemon=True)
        self.window_id = window_id
        self.owner = owner
        self.key_code = key_code
        self.delay = delay
        self.captures_dir = captures_dir
        self.on_page_cb = on_page_cb
        self.on_done_cb = on_done_cb
        self.crop_mode = crop_mode  # "none" | "first" | "every"
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self.captures_dir.mkdir(parents=True, exist_ok=True)
        prev: Path | None = None
        page = 1

        logger.info(
            "capturer start: wid=%d owner=%r key=%d delay=%.1fs crop_mode=%s",
            self.window_id, self.owner, self.key_code, self.delay, self.crop_mode,
        )

        cached_bbox: tuple[int, int, int, int] | None = None

        try:
            while not self._stop.is_set():
                path = self.captures_dir / f"page_{page:03d}.png"
                take_screenshot(self.window_id, path)

                img = Image.open(path)

                # End-of-slideshow check on raw image before cropping.
                if _is_mostly_black(img):
                    logger.info("end-of-slideshow detected at page %d, stopping", page)
                    path.unlink(missing_ok=True)
                    break

                if self.crop_mode != "none":
                    if cached_bbox is None or self.crop_mode == "every":
                        bbox = _find_content_bbox(img)
                        if self.crop_mode == "first" and cached_bbox is None:
                            cached_bbox = bbox
                            logger.info("content bbox cached from page %d: %s", page, bbox)
                    else:
                        bbox = cached_bbox
                    left, top, right, bottom = bbox
                    if (left, top, right, bottom) != (0, 0, img.width, img.height):
                        img = img.crop((left, top, right, bottom))
                        img.save(path)

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
