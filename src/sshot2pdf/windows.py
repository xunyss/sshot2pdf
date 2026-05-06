from __future__ import annotations

import logging

import Quartz
from AppKit import NSApplicationActivationPolicyRegular, NSRunningApplication

logger = logging.getLogger(__name__)

# Belt-and-suspenders: system processes that may pass the activation-policy check
# but have no meaningful user-facing window.
_SYSTEM_OWNERS = frozenset(
    {
        "Window Server",
        "WindowManager",  # macOS 14 tiling UI
        "loginwindow",
        "Spotlight",
        "AutoFill",
        "Raycast",
        "nsattributedstringagent",
        "손쉬운 사용",
        "Open and Save Panel Service",
        "자동 완성",
        "CursorUIViewService",
    }
)


def list_windows() -> list[dict]:
    """Return app windows across all Spaces as list of dicts.

    Each dict: {"id": int, "label": str, "owner": str}

    Only includes NSApplicationActivationPolicyRegular apps (those that appear
    in Cmd+Tab). Within the same app, title-less entries are suppressed when at
    least one titled window exists.

    kCGWindowListOptionOnScreenOnly is intentionally NOT used because it
    excludes windows that belong to other Spaces.
    """
    info_list = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )

    # Cache activation-policy lookups per PID (many windows share a PID).
    pid_policy: dict[int, bool] = {}

    def is_regular_app(pid: int) -> bool:
        if pid not in pid_policy:
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            pid_policy[pid] = (
                app is not None
                and app.activationPolicy() == NSApplicationActivationPolicyRegular
            )
        return pid_policy[pid]

    # First pass: collect raw entries grouped by owner, skipping non-GUI processes.
    by_owner: dict[str, list[dict]] = {}
    for w in info_list:
        if w.get("kCGWindowLayer", 99) > 3:
            continue
        owner = w.get("kCGWindowOwnerName", "")
        if not owner or owner in _SYSTEM_OWNERS:
            continue
        pid = w.get("kCGWindowOwnerPID", 0)
        if not is_regular_app(pid):
            continue
        title = w.get("kCGWindowName") or ""
        if owner not in by_owner:
            by_owner[owner] = []
        by_owner[owner].append({"id": w["kCGWindowNumber"], "owner": owner, "title": title})

    # Second pass: per owner, prefer titled windows over title-less ones.
    windows = []
    seen_labels: set[str] = set()
    for owner, entries in by_owner.items():
        titled = [e for e in entries if e["title"]]
        to_show = titled if titled else entries[:1]
        for e in to_show:
            label = f"{owner} — {e['title']}" if e["title"] else owner
            if label in seen_labels:
                continue
            seen_labels.add(label)
            windows.append({"id": e["id"], "label": label, "owner": owner})

    logger.debug("%d windows found (from %d owners)", len(windows), len(by_owner))
    return windows
