from __future__ import annotations

import pathlib
import random
from io import BytesIO
from typing import TYPE_CHECKING

import matplotlib.pyplot as plt
import numpy as np
from discord import Enum
from matplotlib.ticker import FuncFormatter, MultipleLocator, NullFormatter
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


def make_figure(
    inp_: dict[str, float],
    sort_key: SortKey = SortKey.height_desc,
    person_spacing_in: float = 1,
    min_fig_width_in: float = 6.0,
) -> BytesIO:
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

    min_height: float = 0.0
    max_height: float = round(max(heights), -1) + 25

    x = np.arange(len(names))

    x_span = len(names) + 3
    fig_width = max(min_fig_width_in, person_spacing_in * x_span)

    # figure and axis
    axes: Axes
    fig, axes = plt.subplots(figsize=(fig_width, 6), layout="constrained")

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
    axes.yaxis.set_minor_locator(MultipleLocator(base=1 * 5))  # every 5 cm
    axes.yaxis.set_minor_formatter("{x:.0f}")
    axes.yaxis.set_minor_formatter(NullFormatter())

    right_axes.yaxis.set_major_locator(MultipleLocator(base=2.54 * 12))  # every 12 in
    right_axes.yaxis.set_major_formatter(FuncFormatter(cm_to_ft_and_in))
    right_axes.yaxis.set_minor_locator(MultipleLocator(base=2.54 * 6))  # every 6 in
    right_axes.yaxis.set_minor_formatter(FuncFormatter(cm_to_ft_and_in))
    right_axes.yaxis.set_minor_formatter(NullFormatter())

    for tick in axes.get_yticklabels(which="minor"):
        tick.set_fontname("sans-serif")

    for tick in right_axes.get_yticklabels(which="minor"):
        tick.set_fontname("sans-serif")

    fig.canvas.draw()
    ax_bbox = axes.get_position()
    ax_width_in = ax_bbox.width * fig.get_figwidth()
    ax_height_in = ax_bbox.height * fig.get_figheight()

    x_range = axes.get_xlim()[1] - axes.get_xlim()[0]
    y_range = axes.get_ylim()[1] - axes.get_ylim()[0]

    inches_per_xunit = ax_width_in / x_range
    inches_per_yunit = ax_height_in / y_range

    person = Image.open(PERSON).convert("RGBA")

    for xs, height, name in zip(x, heights, names, strict=False):
        if name == "MewTwo":
            image = Image.open(MEWTWO).convert("RGBA")
        elif name == "Steve Jobs":
            image = Image.open(JOBS).convert("RGBA")
        elif name == "Zero Two":
            image = Image.open(ZERO).convert("RGBA")
        else:
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

        height_extent = height - min_height
        physical_height_in = height_extent * inches_per_yunit
        physical_width_in = physical_height_in * (image.width / image.height)
        width_extent = physical_width_in / inches_per_xunit

        axes.imshow(
            np.array(image),
            aspect="auto",
            origin="upper",
            extent=(
                xs - (width_extent / 2),
                xs + (width_extent / 2),
                min_height,
                height,
            ),
        )

        label_text = f"{cm_to_ft_and_in(height, 0)}\n{height}"
        axes.annotate(label_text, (xs, height + 3), va="bottom", ha="center")  # pyright: ignore[reportArgumentType]

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
