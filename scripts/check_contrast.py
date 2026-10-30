#!/usr/bin/env python3
"""Measure the contrast of every token pair the design system promises.

`frontend/src/app/tokens.css` claims ratios in its comments. A comment is not
a check, and the palette has already been swapped once — the warm-paper
rewrite moved every neutral and three of the six hues. This reads the tokens
back out of the stylesheet and measures them, so the claim and the file
cannot drift.

Two bars, from WCAG 2.2:

* **4.5:1** for anything that is text. `--fg`, `--fg-muted`, `--fg-subtle`,
  and every semantic hue, because each is used as a label on its own tint.
* **3:1** for a boundary or a fill that carries meaning without being read:
  `--border-strong`, `--txn`, and the four meter bands.

`--accent-bold` is decoration and is pinned as such. It is the study's own
orange, kept for the masthead mark and the moving rule; it clears 4.5 after
dark and only 3.06 before it, and a colour whose legibility depends on the
theme cannot carry meaning as text. The check fires only if it becomes
text-safe in *both* themes, which would mean it should be promoted.

    python scripts/check_contrast.py          # report
    python scripts/check_contrast.py --check  # exit 1 on any failure

Both themes are measured. Dark is a peer, not an afterthought, and it is the
one that regresses silently.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TOKENS = (
    Path(__file__).resolve().parent.parent / "frontend" / "src" / "app" / "tokens.css"
)

#: token -> the surfaces it must be legible on, and the bar it must clear.
TEXT_ON = ("--surface", "--bg")
TEXT_TOKENS = (
    "--fg",
    "--fg-muted",
    "--fg-subtle",
    "--accent",
    "--danger",
    "--success",
    "--warning",
    "--info",
    "--authority",
)
GRAPHIC_TOKENS = (
    "--border-strong",
    "--txn",
    "--meter-0",
    "--meter-1",
    "--meter-2",
    "--meter-3",
)

#: Decoration. Pinned as failing so its role cannot quietly change.
DECORATIVE = ("--accent-bold",)

TEXT_BAR = 4.5
GRAPHIC_BAR = 3.0

_HEX = re.compile(r"^\s*(--[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;", re.MULTILINE)


def _luminance(hex_colour: str) -> float:
    value = hex_colour.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def ratio(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _blocks(css: str) -> dict[str, dict[str, str]]:
    """The light palette, and the dark one layered over it.

    Light is everything declared before the first dark selector; dark is light
    with the `[data-theme="dark"]` block applied on top, which is how the
    cascade resolves it in the browser.
    """
    marker = css.find(':root[data-theme="dark"]')
    head = css if marker < 0 else css[:marker]
    tail = "" if marker < 0 else css[marker:]

    # The light block ends at the first media query; anything after it in the
    # head belongs to the dark-by-preference variant, which mirrors the
    # explicit dark block and does not need measuring twice.
    media = head.find("@media (prefers-color-scheme: dark)")
    light = dict(_HEX.findall(head if media < 0 else head[:media]))
    dark = light | dict(_HEX.findall(tail))
    return {"light": light, "dark": dark}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 on any failure")
    args = parser.parse_args()

    css = TOKENS.read_text(encoding="utf-8")
    themes = _blocks(css)
    failures: list[str] = []
    decorative_best: dict[str, list[float]] = {}

    for theme, palette in themes.items():
        print(f"\n{theme}")
        for token in TEXT_TOKENS + GRAPHIC_TOKENS:
            colour = palette.get(token)
            if colour is None:
                failures.append(f"{theme}: {token} is not defined")
                print(f"  {token:16} MISSING")
                continue
            bar = TEXT_BAR if token in TEXT_TOKENS else GRAPHIC_BAR
            measured = [
                (on, ratio(colour, palette[on])) for on in TEXT_ON if on in palette
            ]
            worst = min(measured, key=lambda pair: pair[1])
            mark = "ok " if worst[1] >= bar else "FAIL"
            detail = "  ".join(
                f"{on.removeprefix('--')} {value:5.2f}" for on, value in measured
            )
            print(f"  {mark} {token:16} {colour}  {detail}   (needs {bar})")
            if worst[1] < bar:
                failures.append(
                    f"{theme}: {token} is {worst[1]:.2f} on {worst[0]}, below {bar}"
                )

        for token in DECORATIVE:
            colour = palette.get(token)
            if colour is None:
                continue
            best = max(ratio(colour, palette[on]) for on in TEXT_ON if on in palette)
            decorative_best.setdefault(token, []).append(best)
            print(
                f"  --  {token:16} {colour}  best {best:5.2f}   (decoration, not a label)"
            )

    # A decorative token is only mis-filed if it is text-safe in *every* theme.
    # --accent-bold clears 4.5 after dark and not before it, which is exactly
    # why it stays decoration: a token whose legibility depends on the theme
    # cannot carry meaning as text.
    for token, ratios in decorative_best.items():
        if ratios and min(ratios) >= TEXT_BAR:
            failures.append(
                f"{token} clears {TEXT_BAR} in every theme ({min(ratios):.2f}) — "
                f"promote it out of DECORATIVE and say so in tokens.css"
            )

    print()
    if failures:
        for line in failures:
            print(f"FAIL  {line}")
        print(f"\n{len(failures)} contrast failures")
        return 1 if args.check else 0
    print("every token clears its bar in both themes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
