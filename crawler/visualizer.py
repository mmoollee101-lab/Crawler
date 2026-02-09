"""Keyword visualization — bar charts."""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Dict, List

import matplotlib
matplotlib.use("TkAgg")

import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


def configure_korean_font() -> None:
    """Configure matplotlib to render Korean text on Windows."""
    system = platform.system()
    if system == "Windows":
        # Directly register Malgun Gothic font file
        font_path = Path("C:/Windows/Fonts/malgun.ttf")
        if font_path.exists():
            fm.fontManager.addfont(str(font_path))
            prop = fm.FontProperties(fname=str(font_path))
            matplotlib.rcParams["font.family"] = prop.get_name()
        else:
            matplotlib.rcParams["font.family"] = "Malgun Gothic"
    elif system == "Darwin":
        matplotlib.rcParams["font.family"] = "AppleGothic"
    else:
        matplotlib.rcParams["font.family"] = "NanumGothic"
    matplotlib.rcParams["axes.unicode_minus"] = False


class KeywordVisualizer:
    """Create keyword bar chart for embedding in tkinter."""

    def __init__(self) -> None:
        configure_korean_font()

    def create_bar_chart(
        self,
        query: str,
        keywords: List[dict],
        max_bars: int = 20,
    ) -> Figure:
        fig, ax = plt.subplots(figsize=(8, 6))
        fig.patch.set_facecolor("#f8f9fa")

        display_keywords = keywords[:max_bars]
        if not display_keywords:
            ax.text(0.5, 0.5, "No keywords to display",
                    ha="center", va="center", fontsize=14)
            ax.set_axis_off()
            return fig

        # Reverse for horizontal bar chart (top keyword at top)
        display_keywords = list(reversed(display_keywords))

        words = [kw["keyword"] for kw in display_keywords]
        scores = [kw.get("combined_score", 0) for kw in display_keywords]

        colors = plt.cm.Blues(  # type: ignore[attr-defined]
            [0.3 + 0.7 * (s / max(scores)) for s in scores]
        )

        bars = ax.barh(words, scores, color=colors, edgecolor="#339af0", linewidth=0.5)

        ax.set_xlabel("Relevance Score", fontsize=10)
        ax.set_title(f"Related Keywords: '{query}'", fontsize=13, fontweight="bold")
        ax.tick_params(axis="y", labelsize=9)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        fig.tight_layout()
        return fig

    def create_detail_bar_chart(self, totals: Dict[str, int]) -> Figure:
        """Create a vertical bar chart of detail keyword frequencies."""
        fig, ax = plt.subplots(figsize=(8, 6))
        fig.patch.set_facecolor("#f8f9fa")

        # Filter out zero-count keywords and sort descending
        filtered = {k: v for k, v in totals.items() if v > 0}
        if not filtered:
            ax.text(0.5, 0.5, "No keyword matches found",
                    ha="center", va="center", fontsize=14)
            ax.set_axis_off()
            return fig

        sorted_items = sorted(filtered.items(), key=lambda x: x[1], reverse=True)
        keywords = [item[0] for item in sorted_items]
        counts = [item[1] for item in sorted_items]

        max_count = max(counts)
        colors = plt.cm.Oranges(  # type: ignore[attr-defined]
            [0.3 + 0.7 * (c / max_count) for c in counts]
        )

        ax.bar(keywords, counts, color=colors, edgecolor="#e8590c", linewidth=0.5)

        ax.set_ylabel("Frequency", fontsize=10)
        ax.set_title("Detail Keyword Frequency (All Articles)", fontsize=13, fontweight="bold")
        ax.tick_params(axis="x", labelsize=9, rotation=45)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        fig.tight_layout()
        return fig

    @staticmethod
    def embed_in_tkinter(fig: Figure, parent) -> FigureCanvasTkAgg:
        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
        return canvas
