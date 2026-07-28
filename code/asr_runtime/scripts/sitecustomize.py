"""Initialize split packaged site-packages before FunASR module discovery."""

from __future__ import annotations

import os
from pathlib import Path
import site


def _activate_packaged_site_packages() -> None:
    configured = str(
        os.environ.get("MEETING_COPILOT_FUNASR_SITE_PACKAGES") or ""
    ).strip()
    if not configured:
        return
    site_packages = Path(configured).expanduser().resolve(strict=False)
    if site_packages.name.casefold() != "site-packages" or not site_packages.is_dir():
        return
    site.addsitedir(str(site_packages))


_activate_packaged_site_packages()
