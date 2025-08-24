from __future__ import annotations

import pathlib
import random
from io import BytesIO
from typing import TYPE_CHECKING

import matplotlib.pyplot as plt
import numpy as np
from discord import Enum
from matplotlib.ticker import FuncFormatter, MultipleLocator
from PIL import Image, ImageOps

if TYPE_CHECKING:
    from matplotlib.axes import Axes

RESOURCES_DIR = pathlib.Path(__file__).parent / "resources"
PERSON = RESOURCES_DIR / "person.png"
JOBS = RESOURCES_DIR / "steve_jobs.png"
ZERO = RESOURCES_DIR / "zero_two.png"
MEWTWO = RESOURCES_DIR / "mewtwo.png"


__all__ = ("make_figure",)


class SortKey(Enum):
    height_desc = 1
    height_asc = 2
    name_desc = 3
    name_asc = 4


def make_figure(inp_: dict[str, float], sort_key: SortKey = SortKey.height_desc) -> BytesIO:
    reverse = False
    if sort_key is SortKey.height_desc or sort_key is SortKey.height_asc:
        key = lambda i: (-i[1], i[0])
        if sort_key is SortKey.height_asc:
            reverse = True
    elif sort_key is SortKey.name_desc or sort_key is SortKey.name_asc:
        key = lambda i: i[0]
        if sort_key is SortKey.name_asc:
            reverse = True

    sort: dict[str, float] = dict(sorted(inp_.items(), key=key, reverse=reverse))
    names: list[str] = [*sort.keys()]
    heights: list[float] = [*sort.values()]

    min_height: float = round(min(heights), -1) - 10
    max_height: float = round(max(heights), -1) + 10

    x = np.arange(len(names))

    # figure and axis
    axes: Axes
    _, axes = plt.subplots(figsize=(33.335, 6), layout="constrained")

    # x axis
    axes.set_xlabel("Person")
    axes.set_xlim(-2, len(names) + 1)
    axes.set_xticks(
        x,
        [f"{'\n' if i % 2 != 0 else ''}{name}" for i, name in enumerate(names)],
    )

    # y axis

    def cm_to_ft_and_in(cm: float, position: int) -> str:
        inches_total = cm / 2.54
        feet = round(inches_total) // 12
        inches = round(inches_total) % 12
        return f"{feet}'{inches}\""

    right_axes = axes.twinx()

    axes.set_ylabel("Height (cm)")
    right_axes.set_ylabel("Height (in)")

    axes.set_ylim(min_height, max_height)
    right_axes.set_ylim(min_height, max_height)

    axes.yaxis.set_major_locator(MultipleLocator(base=1 * 10))  # every 10 cm
    axes.yaxis.set_major_formatter("{x:.0f}")
    axes.yaxis.set_minor_locator(MultipleLocator(base=1 * 2))  # every 2 cm
    axes.yaxis.set_minor_formatter("{x:.0f}")

    right_axes.yaxis.set_major_locator(MultipleLocator(base=2.54 * 12))  # every 12 in
    right_axes.yaxis.set_major_formatter(FuncFormatter(cm_to_ft_and_in))
    right_axes.yaxis.set_minor_locator(MultipleLocator(base=2.54 * 1))  # every 1 in
    right_axes.yaxis.set_minor_formatter(FuncFormatter(cm_to_ft_and_in))

    for tick in axes.get_yticklabels(which="minor"):
        tick.set_fontname("sans-serif")

    for tick in right_axes.get_yticklabels(which="minor"):
        tick.set_fontname("sans-serif")

    # images
    person = Image.open(PERSON).convert("RGBA")

    scaled_heights = [(height - min_height) for height in heights]
    raw_widths = np.array(scaled_heights) * (person.width / person.height)

    w_min, w_max = raw_widths.min(), raw_widths.max()
    scaled_widths = (raw_widths - w_min) / (w_max - w_min) * (3 - 1) + 1

    for xs, height, name in zip(x, heights, names, strict=False):
        image = Image.merge(
            "RGBA",
            (
                *ImageOps.colorize(
                    ImageOps.grayscale(person),
                    white=(0, 0, 0),
                    black=(
                        random.randint(210, 255),
                        random.randint(130, 170),
                        random.randint(225, 255),
                    ),
                ).split(),  # r, g, b
                person.split()[-1],  # alpha
            ),
        )
        if name == "MewTwo":
            image = Image.open(MEWTWO).convert("RGBA")
        elif name == "Steve Jobs":
            image = Image.open(JOBS).convert("RGBA")
        elif name == "Zero Two":
            image = Image.open(ZERO).convert("RGBA")
        axes.imshow(
            np.array(image),
            aspect="auto",
            origin="upper",
            extent=(xs - (scaled_widths[xs] / 2), xs + (scaled_widths[xs] / 2), min_height, height),
        )
        axes.annotate(f"{height}", (xs, height), va="bottom", ha="center")
        axes.annotate(cm_to_ft_and_in(height, 0), (xs, height + 2), va="bottom", ha="center")

    mean = sum(heights) / len(heights)
    axes.axhline(mean, color="red", ls="-.")
    axes.annotate(
        f"Mean: {mean:.1f} cm / {cm_to_ft_and_in(mean, 0)}",
        (len(names) - 1, mean),
        xytext=(len(names) - 1, mean + 10),
        color="red",
        va="top",
        ha="center",
        arrowprops={
            "arrowstyle": "->",
            "color": "red",
            "lw": 1.5,
        },
    )

    # save plot
    buf = BytesIO()
    plt.savefig(buf, dpi=150)

    buf.seek(0)
    return buf
