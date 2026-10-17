"""Locales a digit guard must hold under, each proven live before it is trusted.

Bash `[[ =~ ]]` and `case` bracket ranges follow the locale's collation, so a
`[1-9]` guard that is correct under C.UTF-8 admits U+0663 ARABIC-INDIC DIGIT
THREE under en_US.UTF-8. A guard test that runs in one locale proves nothing
about the other, so tests run every value under both. A locale that merely
names itself is not evidence: en_US.UTF-8 counts only once the hazard
reproduces in it, and it is built into a private LOCPATH when the host lacks it.
"""

import os
from pathlib import Path
import subprocess

HAZARD_PROBE = '[[ "$1" =~ ^[1-9]$ ]]'
ARABIC_INDIC_THREE = "٣"


def _bash_probe(environment):
    result = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-c", HAZARD_PROBE, "probe", ARABIC_INDIC_THREE],
        env=environment,
        capture_output=True,
        check=False,
        timeout=10,
    )
    return result.returncode == 0


def guard_locales(build_directory, environ=None, probe=None):
    """Return ({label: env overrides}, reason en_US.UTF-8 is unavailable or None).

    Under CI the hazard locale is required: a skipped en_US.UTF-8 check there
    would report green for exactly the case these tests exist to catch.
    """
    environ = os.environ if environ is None else environ
    probe = _bash_probe if probe is None else probe
    base = {key: value for key, value in os.environ.items() if not key.startswith("LC_")}
    locales = {"C.UTF-8": {"LC_ALL": "C.UTF-8"}}
    if probe({**base, "LC_ALL": "C.UTF-8"}):
        raise AssertionError("C.UTF-8 collates U+0663 into [1-9]; the ASCII baseline is not ASCII")
    hazard = {"LC_ALL": "en_US.UTF-8"}
    if not probe({**base, **hazard}):
        locpath = Path(build_directory) / "locales"
        locpath.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["localedef", "-i", "en_US", "-f", "UTF-8", str(locpath / "en_US.UTF-8")],
            capture_output=True,
            check=False,
            timeout=120,
        )
        hazard = {"LC_ALL": "en_US.UTF-8", "LOCPATH": str(locpath)}
        if not probe({**base, **hazard}):
            reason = (
                "en_US.UTF-8 is neither installed nor buildable with localedef here, "
                "so the collation hazard cannot be reproduced"
            )
            if environ.get("CI") == "true":
                raise AssertionError(reason + "; CI must run the en_US.UTF-8 checks")
            return locales, reason
    locales["en_US.UTF-8"] = hazard
    print(f"ascii-digit-locales: en_US.UTF-8 hazard reproduced ({hazard})", flush=True)
    return locales, None
