"""Tkinter GUI for the keyword crawler."""

from __future__ import annotations

import os
import re
import sys
import queue
import threading
import tkinter as tk
import webbrowser
from datetime import datetime, timedelta
from tkinter import ttk, messagebox

# Support both `python -m crawler --gui` and `py gui.py` direct execution
if __name__ == "__main__" or __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "crawler"

from .analyzer import KeywordAnalyzer
from .article_fetcher import ArticleFetcher
from .detail_analyzer import DEFAULT_COMPANIES, DetailAnalyzer
from .models import CrawlResult, KeywordResult, NewsArticle, PageData
from .google_news import GoogleNewsCrawler
from .naver_news import NaverNewsCrawler
from .storage import Storage

_STORAGE = Storage()


class CrawlerGUI:
    """Main GUI application."""

    def __init__(self) -> None:
        self._root = tk.Tk()
        self._root.title("Keyword Crawler")
        self._root.geometry("1000x750")
        self._root.minsize(850, 650)

        # Windows taskbar: show app's own icon instead of generic Python icon
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "crawler.keywordcrawler",
            )
        except Exception:
            pass

        # Window icon (supports PyInstaller frozen mode via sys._MEIPASS)
        _icon_path = os.path.join(
            getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))),
            "icon.ico",
        )
        if os.path.exists(_icon_path):
            self._root.iconbitmap(_icon_path)

        self._msg_queue: queue.Queue = queue.Queue()
        self._cancel_event = threading.Event()
        self._crawl_thread: threading.Thread | None = None
        self._crawl_result: CrawlResult | None = None
        self._keyword_result: KeywordResult | None = None
        self._graph_canvas = None

        # Detail analysis state
        self._naver_articles: list[NewsArticle] = []
        self._detail_result: dict | None = None
        self._detail_graph_canvas = None

        # Hidden keywords state (right-click to hide)
        self._hidden_keywords: set[str] = set()

        # Score weight for combined ranking (0-100 = TF-IDF percentage)
        self._tfidf_weight_var = tk.IntVar(value=70)

        # Article popup state
        self._article_popup: tk.Toplevel | None = None
        self._popup_tree: ttk.Treeview | None = None
        self._popup_info_label: ttk.Label | None = None
        self._popup_sort_col: str = ""
        self._popup_sort_reverse: bool = False

        self._build_ui()
        self._on_mode_change()  # set initial field states
        self._load_history()
        self._poll_queue()

    def run(self) -> None:
        self._root.mainloop()

    # ── UI construction ──────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_settings_frame()
        self._build_notebook()
        self._build_status_bar()

    def _build_settings_frame(self) -> None:
        frame = ttk.LabelFrame(self._root, text="Settings", padding=10)
        frame.pack(fill="x", padx=10, pady=(10, 5))

        # Row 0: Mode selector + Keyword
        row0 = ttk.Frame(frame)
        row0.pack(fill="x", pady=2)

        ttk.Label(row0, text="Mode:").pack(side="left")
        self._mode_var = tk.StringVar(value="google")
        ttk.Radiobutton(
            row0, text="Google News", variable=self._mode_var,
            value="google", command=self._on_mode_change,
        ).pack(side="left", padx=(5, 5))
        ttk.Radiobutton(
            row0, text="Naver News", variable=self._mode_var,
            value="naver", command=self._on_mode_change,
        ).pack(side="left", padx=(0, 15))

        ttk.Label(row0, text="Keyword:").pack(side="left")
        self._keyword_var = tk.StringVar()
        kw_entry = ttk.Entry(row0, textvariable=self._keyword_var, width=20)
        kw_entry.pack(side="left", padx=(5, 0), fill="x", expand=True)
        kw_entry.bind("<Return>", lambda e: self._on_start())

        # Dynamic container — holds mode-specific rows (stays packed, children swap)
        self._dynamic = ttk.Frame(frame)
        self._dynamic.pack(fill="x")

        # Row: Date range (for Naver/Google news modes)
        self._row_naver = ttk.Frame(self._dynamic)

        today = datetime.now()
        week_ago = today - timedelta(days=7)

        ttk.Label(self._row_naver, text="Date From:").pack(side="left")
        self._date_from_var = tk.StringVar(value=week_ago.strftime("%Y.%m.%d"))
        ttk.Entry(self._row_naver, textvariable=self._date_from_var, width=12).pack(
            side="left", padx=(5, 0),
        )
        ttk.Label(self._row_naver, text="(YYYY.MM.DD)", foreground="gray").pack(
            side="left", padx=(2, 15),
        )

        ttk.Label(self._row_naver, text="Date To:").pack(side="left")
        self._date_to_var = tk.StringVar(value=today.strftime("%Y.%m.%d"))
        ttk.Entry(self._row_naver, textvariable=self._date_to_var, width=12).pack(
            side="left", padx=(5, 0),
        )
        ttk.Label(self._row_naver, text="(YYYY.MM.DD)", foreground="gray").pack(
            side="left", padx=(2, 10),
        )

        # Date preset buttons
        for label, days in [("1주", 7), ("1개월", 30), ("3개월", 90)]:
            ttk.Button(
                self._row_naver, text=label, width=5,
                command=lambda d=days: self._set_date_preset(d),
            ).pack(side="left", padx=2)

        # Row: Buttons + progress
        row4 = ttk.Frame(frame)
        row4.pack(fill="x", pady=(5, 0))

        self._start_btn = ttk.Button(row4, text="Start", command=self._on_start)
        self._start_btn.pack(side="left", padx=(0, 5))

        self._stop_btn = ttk.Button(
            row4, text="Stop", command=self._on_stop, state="disabled",
        )
        self._stop_btn.pack(side="left", padx=(0, 15))

        self._progress_var = tk.DoubleVar(value=0.0)
        self._progress_bar = ttk.Progressbar(
            row4, variable=self._progress_var, maximum=100, length=300,
        )
        self._progress_bar.pack(side="left", padx=(0, 10), fill="x", expand=True)

        self._progress_label = ttk.Label(row4, text="0%")
        self._progress_label.pack(side="left")

    def _on_mode_change(self) -> None:
        """Show/hide settings rows based on selected mode."""
        self._row_naver.pack_forget()
        self._row_naver.pack(fill="x", pady=2)

    def _build_notebook(self) -> None:
        self._notebook = ttk.Notebook(self._root)
        self._notebook.pack(fill="both", expand=True, padx=10, pady=5)

        # Tab 1: Headlines
        hl_frame = ttk.Frame(self._notebook)
        self._notebook.add(hl_frame, text="Headlines")

        hl_top = ttk.Frame(hl_frame)
        hl_top.pack(fill="x", pady=5, padx=5)
        self._hl_info_label = ttk.Label(
            hl_top, text="Article/post titles will appear here after crawling.",
        )
        self._hl_info_label.pack(side="left")

        hl_columns = ("#", "Title", "Source", "Date", "URL")
        self._hl_tree = ttk.Treeview(
            hl_frame, columns=hl_columns, show="headings", height=15,
        )
        self._hl_tree.heading("#", text="#")
        self._hl_tree.heading("Title", text="Title")
        self._hl_tree.heading("Source", text="Source")
        self._hl_tree.heading("Date", text="Date")
        self._hl_tree.heading("URL", text="URL")

        self._hl_tree.column("#", width=40, stretch=False)
        self._hl_tree.column("Title", width=400, stretch=True)
        self._hl_tree.column("Source", width=120, stretch=False)
        self._hl_tree.column("Date", width=100, stretch=False)
        self._hl_tree.column("URL", width=200, stretch=True)

        hl_sb = ttk.Scrollbar(hl_frame, orient="vertical", command=self._hl_tree.yview)
        self._hl_tree.configure(yscrollcommand=hl_sb.set)

        self._hl_tree.pack(side="left", fill="both", expand=True, padx=(5, 0), pady=(0, 5))
        hl_sb.pack(side="right", fill="y", pady=(0, 5))
        self._hl_tree.bind("<Double-1>", self._on_article_double_click)

        # Tab 2: Keywords
        kw_frame = ttk.Frame(self._notebook)
        self._notebook.add(kw_frame, text="Keywords")

        kw_top = ttk.Frame(kw_frame)
        kw_top.pack(fill="x", pady=5, padx=5)
        self._kw_info_label = ttk.Label(kw_top, text="Run a crawl to see keyword analysis.")
        self._kw_info_label.pack(side="left")

        ttk.Button(kw_top, text="Export JSON", command=self._export_keywords_json).pack(
            side="right", padx=5,
        )
        ttk.Button(kw_top, text="Export CSV", command=self._export_keywords_csv).pack(
            side="right", padx=5,
        )

        kw_columns = ("Rank", "Keyword", "Frequency", "Co-occurrence", "TF-IDF")
        self._kw_tree = ttk.Treeview(
            kw_frame, columns=kw_columns, show="headings", height=15,
        )
        self._kw_tree.heading("Rank", text="Rank")
        self._kw_tree.heading("Keyword", text="Keyword")
        self._kw_tree.heading("Frequency", text="Frequency")
        self._kw_tree.heading("Co-occurrence", text="Co-occurrence")
        self._kw_tree.heading("TF-IDF", text="TF-IDF Score")

        self._kw_tree.column("Rank", width=50, stretch=False)
        self._kw_tree.column("Keyword", width=200, stretch=True)
        self._kw_tree.column("Frequency", width=100, stretch=False)
        self._kw_tree.column("Co-occurrence", width=120, stretch=False)
        self._kw_tree.column("TF-IDF", width=100, stretch=False)

        kw_sb = ttk.Scrollbar(kw_frame, orient="vertical", command=self._kw_tree.yview)
        self._kw_tree.configure(yscrollcommand=kw_sb.set)

        self._kw_tree.pack(side="left", fill="both", expand=True, padx=(5, 0), pady=(0, 5))
        kw_sb.pack(side="right", fill="y", pady=(0, 5))
        self._kw_tree.bind("<<TreeviewSelect>>", self._on_keyword_select)
        self._kw_tree.bind("<Button-3>", self._on_kw_right_click)

        # Tab 3: Detail Analysis
        detail_frame = ttk.Frame(self._notebook)
        self._notebook.add(detail_frame, text="Detail Analysis")

        # Keyword settings panel
        kw_settings = ttk.LabelFrame(detail_frame, text="Keyword Settings", padding=8)
        kw_settings.pack(fill="x", padx=5, pady=5)

        preset_row = ttk.Frame(kw_settings)
        preset_row.pack(fill="x", pady=2)

        ttk.Label(preset_row, text="Preset:").pack(side="left")
        self._detail_preset_var = tk.StringVar(value="Companies")
        self._preset_combo = ttk.Combobox(
            preset_row, textvariable=self._detail_preset_var,
            values=["Companies"], state="readonly", width=15,
        )
        self._preset_combo.pack(side="left", padx=5)
        ttk.Button(
            preset_row, text="Load", command=self._load_detail_preset,
        ).pack(side="left", padx=2)
        ttk.Button(
            preset_row, text="Save Preset", command=self._save_detail_preset,
        ).pack(side="left", padx=2)
        ttk.Button(
            preset_row, text="Delete Preset", command=self._delete_detail_preset,
        ).pack(side="left", padx=2)
        self._refresh_preset_list()

        kw_input_row = ttk.Frame(kw_settings)
        kw_input_row.pack(fill="x", pady=2)

        ttk.Label(kw_input_row, text="Keywords (comma separated):").pack(side="left")
        self._detail_kw_var = tk.StringVar()
        ttk.Entry(
            kw_input_row, textvariable=self._detail_kw_var, width=60,
        ).pack(side="left", padx=5, fill="x", expand=True)

        btn_row = ttk.Frame(kw_settings)
        btn_row.pack(fill="x", pady=(5, 0))

        self._detail_fetch_btn = ttk.Button(
            btn_row, text="Fetch Articles & Analyze",
            command=self._on_detail_analyze,
        )
        self._detail_fetch_btn.pack(side="left", padx=(0, 10))

        self._detail_reanalyze_btn = ttk.Button(
            btn_row, text="Re-Analyze (no fetch)",
            command=self._on_detail_reanalyze, state="disabled",
        )
        self._detail_reanalyze_btn.pack(side="left", padx=(0, 10))

        ttk.Button(
            btn_row, text="Export CSV", command=self._export_detail_csv,
        ).pack(side="right", padx=5)
        ttk.Button(
            btn_row, text="Export JSON", command=self._export_detail_json,
        ).pack(side="right", padx=5)

        # Detail results table
        detail_results = ttk.LabelFrame(detail_frame, text="Results", padding=5)
        detail_results.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        self._detail_info_label = ttk.Label(
            detail_results,
            text="Run a News search first, then click 'Fetch Articles & Analyze'.",
        )
        self._detail_info_label.pack(anchor="w", pady=(0, 3))

        # Treeview (columns built dynamically on analyze)
        tree_container = ttk.Frame(detail_results)
        tree_container.pack(fill="both", expand=True)

        self._detail_tree = ttk.Treeview(tree_container, show="headings", height=12)
        detail_xsb = ttk.Scrollbar(
            detail_results, orient="horizontal", command=self._detail_tree.xview,
        )
        detail_ysb = ttk.Scrollbar(
            tree_container, orient="vertical", command=self._detail_tree.yview,
        )
        self._detail_tree.configure(
            xscrollcommand=detail_xsb.set, yscrollcommand=detail_ysb.set,
        )

        self._detail_tree.pack(side="left", fill="both", expand=True)
        detail_ysb.pack(side="right", fill="y")
        detail_xsb.pack(fill="x")
        self._detail_tree.bind("<Button-1>", self._on_detail_cell_click)
        self._detail_tree.bind("<Double-1>", self._on_detail_double_click)

        # Tab 4: Graph (bar chart only)
        graph_frame = ttk.Frame(self._notebook)
        self._notebook.add(graph_frame, text="Graph")

        graph_controls = ttk.Frame(graph_frame)
        graph_controls.pack(fill="x", pady=5, padx=5)
        ttk.Button(
            graph_controls, text="Save Graph PNG", command=self._save_graph,
        ).pack(side="right", padx=5)

        ttk.Label(graph_controls, text="Score Weight:").pack(side="left")
        self._weight_scale = ttk.Scale(
            graph_controls, from_=0, to=100,
            variable=self._tfidf_weight_var,
            command=self._on_weight_slider_move,
        )
        self._weight_scale.pack(side="left", padx=5, fill="x", expand=True)
        self._weight_scale.bind("<ButtonRelease-1>", lambda e: self._on_weight_change())
        self._weight_label = ttk.Label(
            graph_controls, text="TF-IDF 70% / Co-occ 30%", width=24,
        )
        self._weight_label.pack(side="left")

        ttk.Label(
            graph_frame,
            text="  TF-IDF = word importance across articles  |"
                 "  Co-occurrence = appears alongside search keyword",
            font=("", 8), foreground="gray",
        ).pack(fill="x", padx=5, pady=(0, 2))

        self._graph_container = ttk.Frame(graph_frame)
        self._graph_container.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        # Tab 5: History
        hist_frame = ttk.Frame(self._notebook)
        self._notebook.add(hist_frame, text="History")

        hist_top = ttk.Frame(hist_frame)
        hist_top.pack(fill="x", pady=5, padx=5)
        self._hist_info_label = ttk.Label(hist_top, text="Crawl history")
        self._hist_info_label.pack(side="left")
        ttk.Button(hist_top, text="Clear History", command=self._clear_history).pack(
            side="right", padx=5,
        )
        ttk.Button(hist_top, text="Reload", command=self._load_history).pack(
            side="right", padx=5,
        )

        hist_columns = ("#", "Date", "Mode", "Keyword", "Period", "Articles", "Top Keywords")
        self._hist_tree = ttk.Treeview(
            hist_frame, columns=hist_columns, show="headings", height=15,
        )
        self._hist_tree.heading("#", text="#")
        self._hist_tree.heading("Date", text="Date")
        self._hist_tree.heading("Mode", text="Mode")
        self._hist_tree.heading("Keyword", text="Keyword")
        self._hist_tree.heading("Period", text="Period")
        self._hist_tree.heading("Articles", text="Articles")
        self._hist_tree.heading("Top Keywords", text="Top Keywords")

        self._hist_tree.column("#", width=35, stretch=False)
        self._hist_tree.column("Date", width=130, stretch=False)
        self._hist_tree.column("Mode", width=70, stretch=False)
        self._hist_tree.column("Keyword", width=100, stretch=False)
        self._hist_tree.column("Period", width=160, stretch=False)
        self._hist_tree.column("Articles", width=60, stretch=False)
        self._hist_tree.column("Top Keywords", width=300, stretch=True)

        hist_sb = ttk.Scrollbar(hist_frame, orient="vertical", command=self._hist_tree.yview)
        self._hist_tree.configure(yscrollcommand=hist_sb.set)

        self._hist_tree.pack(side="left", fill="both", expand=True, padx=(5, 0), pady=(0, 5))
        hist_sb.pack(side="right", fill="y", pady=(0, 5))

    def _build_status_bar(self) -> None:
        self._status_var = tk.StringVar(value="Ready")
        ttk.Label(
            self._root, textvariable=self._status_var,
            relief="sunken", anchor="w", padding=5,
        ).pack(fill="x", padx=10, pady=(0, 10))

    # ── Actions ──────────────────────────────────────────────────

    def _set_date_preset(self, days: int) -> None:
        """Set date range to today minus *days* through today."""
        today = datetime.now()
        self._date_to_var.set(today.strftime("%Y.%m.%d"))
        self._date_from_var.set((today - timedelta(days=days)).strftime("%Y.%m.%d"))

    def _validate_dates(self) -> bool:
        """Validate date format, real date, and range order. Returns True if OK."""
        date_re = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
        for label, var in [("Date From", self._date_from_var), ("Date To", self._date_to_var)]:
            val = var.get().strip()
            if not date_re.match(val):
                messagebox.showwarning(
                    "Invalid Date",
                    f"{label} must be in YYYY.MM.DD format.\nCurrent value: {val}",
                )
                return False
            try:
                datetime.strptime(val, "%Y.%m.%d")
            except ValueError:
                messagebox.showwarning(
                    "Invalid Date",
                    f"{label} is not a valid date: {val}",
                )
                return False

        d_from = datetime.strptime(self._date_from_var.get().strip(), "%Y.%m.%d")
        d_to = datetime.strptime(self._date_to_var.get().strip(), "%Y.%m.%d")
        if d_from > d_to:
            messagebox.showwarning(
                "Invalid Date Range",
                "Date From must be earlier than or equal to Date To.",
            )
            return False
        return True

    def _on_start(self) -> None:
        keyword = self._keyword_var.get().strip()
        if not keyword:
            messagebox.showwarning("Input Required", "Please enter a keyword.")
            return

        if not self._validate_dates():
            return

        mode = self._mode_var.get()

        # Clear previous results
        for tree in (self._hl_tree, self._kw_tree):
            for item in tree.get_children():
                tree.delete(item)
        self._clear_graph()
        self._crawl_result = None
        self._keyword_result = None
        self._naver_articles = []
        self._detail_result = None
        self._clear_detail_tree()
        self._detail_reanalyze_btn.configure(state="disabled")

        # UI state
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._progress_var.set(0)
        self._progress_label.configure(text="0%")
        self._cancel_event.clear()

        if mode == "naver":
            self._status_var.set(f"Searching Naver News: '{keyword}' ...")
            self._crawl_thread = threading.Thread(
                target=self._naver_worker, daemon=True,
            )
        elif mode == "google":
            self._status_var.set(f"Searching Google News: '{keyword}' ...")
            self._crawl_thread = threading.Thread(
                target=self._google_worker, daemon=True,
            )
        self._crawl_thread.start()

    def _on_stop(self) -> None:
        self._cancel_event.set()
        self._status_var.set("Stopping...")
        self._stop_btn.configure(state="disabled")

    # ── Workers ──────────────────────────────────────────────────

    def _naver_worker(self) -> None:
        """Naver News search in background thread."""
        try:
            crawler = NaverNewsCrawler(
                keyword=self._keyword_var.get().strip(),
                start_date=self._date_from_var.get().strip(),
                end_date=self._date_to_var.get().strip(),
                progress_callback=lambda c, m, t: self._msg_queue.put(
                    ("naver_progress", (c, m, t)),
                ),
                cancel_event=self._cancel_event,
            )
            articles = crawler.crawl()
            self._msg_queue.put(("naver_done", articles))
        except Exception as e:
            self._msg_queue.put(("error", str(e)))

    def _google_worker(self) -> None:
        """Google News search in background thread."""
        try:
            crawler = GoogleNewsCrawler(
                keyword=self._keyword_var.get().strip(),
                start_date=self._date_from_var.get().strip(),
                end_date=self._date_to_var.get().strip(),
                delay=1.0,
                progress_callback=lambda c, m, t: self._msg_queue.put(
                    ("naver_progress", (c, m, t)),
                ),
                cancel_event=self._cancel_event,
            )
            articles = crawler.crawl()
            self._msg_queue.put(("naver_done", articles))
        except Exception as e:
            self._msg_queue.put(("error", str(e)))

    # ── Queue polling ────────────────────────────────────────────

    def _poll_queue(self) -> None:
        try:
            while True:
                msg_type, data = self._msg_queue.get_nowait()
                if msg_type == "naver_progress":
                    self._handle_naver_progress(*data)
                elif msg_type == "naver_done":
                    self._handle_naver_done(data)
                elif msg_type == "detail_progress":
                    self._handle_detail_progress(*data)
                elif msg_type == "detail_done":
                    self._handle_detail_done(data)
                elif msg_type == "error":
                    self._handle_error(data)
        except queue.Empty:
            pass
        self._root.after(100, self._poll_queue)

    # ── Naver News handlers ──────────────────────────────────────

    def _handle_naver_progress(self, count: int, max_results: int, title: str) -> None:
        pct = (count / max_results * 100) if max_results else 0
        self._progress_var.set(min(pct, 100))
        self._progress_label.configure(text=f"{int(min(pct, 100))}%")
        self._status_var.set(f"[{count}/{max_results}] {title}")

    def _handle_naver_done(self, articles: list[NewsArticle]) -> None:
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._progress_var.set(100)
        self._progress_label.configure(text="100%")

        # Store articles for detail analysis
        self._naver_articles = list(articles)
        self._detail_result = None
        self._hidden_keywords.clear()

        cancelled = self._cancel_event.is_set()

        if not articles:
            self._progress_var.set(0)
            self._progress_label.configure(text="0%")
            self._status_var.set("No articles found. Try different keyword or date range.")
            messagebox.showinfo("No Results", "No news articles found for this search.")
            return

        # Populate headlines tab
        for i, art in enumerate(articles, 1):
            self._hl_tree.insert("", "end", values=(
                i, art.title, art.source, art.date, art.link,
            ))
        mode = self._mode_var.get()
        mode_label = "Google News" if mode == "google" else "Naver News"
        self._hl_info_label.configure(text=f"{mode_label}: {len(articles)} articles found")

        # Switch to Headlines tab automatically
        self._notebook.select(0)

        # Convert articles to PageData for keyword analysis
        pages: list[PageData] = []
        for art in articles:
            pages.append(PageData(
                url=art.link,
                status_code=200,
                title=art.title,
                full_text=f"{art.title} {art.description}",
                headlines=[art.title],
            ))

        # Build a CrawlResult so exports work
        seed = "google_news_search" if mode == "google" else "naver_news_search"
        self._crawl_result = CrawlResult(seed_url=seed, pages=pages)

        # Keyword analysis (always headlines mode for Naver)
        keyword = self._keyword_var.get().strip()
        if keyword:
            analyzer = KeywordAnalyzer()
            self._keyword_result = analyzer.analyze(pages, keyword, headlines_only=True)
            self._populate_keywords()
            self._refresh_graph()

        status = "Cancelled" if cancelled else "Complete"
        kw_count = len(self._keyword_result.related_keywords) if self._keyword_result else 0
        self._status_var.set(
            f"{mode_label} {status}: {len(articles)} articles | "
            f"Keywords: {kw_count} found"
        )

        # Save to history
        history_mode = "Google" if mode == "google" else "Naver"
        date_from = self._date_from_var.get().strip()
        date_to = self._date_to_var.get().strip()
        self._save_history_record(
            mode=history_mode,
            keyword=keyword,
            period=f"{date_from} ~ {date_to}",
            article_count=len(articles),
        )

    # ── Shared handlers ──────────────────────────────────────────

    def _handle_error(self, error_msg: str) -> None:
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._status_var.set(f"Error: {error_msg}")
        messagebox.showerror("Crawl Error", error_msg)

    # ── Keywords tab ─────────────────────────────────────────────

    def _populate_keywords(self) -> None:
        for item in self._kw_tree.get_children():
            self._kw_tree.delete(item)

        if not self._keyword_result:
            return

        self._kw_info_label.configure(
            text=f"Query: '{self._keyword_result.query_keyword}' | "
                 f"Analyzed: {self._keyword_result.total_pages_analyzed} pages | "
                 f"Pages with query: {self._keyword_result.pages_containing_query}",
        )

        for rank, kw in enumerate(self._get_weighted_keywords(), 1):
            self._kw_tree.insert("", "end", values=(
                rank,
                kw["keyword"],
                kw["frequency"],
                kw["co_occurrence"],
                f"{kw.get('tfidf_score', 0):.4f}",
            ))

    def _export_keywords_csv(self) -> None:
        if not self._keyword_result:
            messagebox.showinfo("No Data", "Run a crawl first.")
            return
        path = _STORAGE.save_keywords_csv(self._keyword_result)
        self._status_var.set(f"Exported CSV: {path}")
        messagebox.showinfo("Export", f"Saved to {path}")

    def _export_keywords_json(self) -> None:
        if not self._keyword_result:
            messagebox.showinfo("No Data", "Run a crawl first.")
            return
        path = _STORAGE.save_keywords_json(self._keyword_result)
        self._status_var.set(f"Exported JSON: {path}")
        messagebox.showinfo("Export", f"Saved to {path}")

    # ── Graph tab ────────────────────────────────────────────────

    def _clear_graph(self) -> None:
        if self._graph_canvas:
            self._graph_canvas.get_tk_widget().destroy()
            self._graph_canvas = None
        for widget in self._graph_container.winfo_children():
            widget.destroy()

    def _refresh_graph(self) -> None:
        self._clear_graph()

        if not self._keyword_result or not self._keyword_result.related_keywords:
            ttk.Label(
                self._graph_container, text="No keyword data to visualize.", font=("", 12),
            ).pack(expand=True)
            return

        from .visualizer import KeywordVisualizer

        filtered_keywords = self._get_weighted_keywords()
        if not filtered_keywords:
            ttk.Label(
                self._graph_container, text="All keywords hidden.", font=("", 12),
            ).pack(expand=True)
            return

        viz = KeywordVisualizer()
        fig = viz.create_bar_chart(
            self._keyword_result.query_keyword,
            filtered_keywords,
        )
        self._graph_canvas = viz.embed_in_tkinter(fig, self._graph_container)
        self._graph_canvas.mpl_connect("button_press_event", self._on_graph_button_press)

    def _save_graph(self) -> None:
        if not self._graph_canvas:
            messagebox.showinfo("No Graph", "Generate a graph first.")
            return
        out = _STORAGE._output_dir
        os.makedirs(out, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(out, f"graph_{ts}.png")
        self._graph_canvas.figure.savefig(path, dpi=150, bbox_inches="tight")
        self._status_var.set(f"Graph saved: {path}")
        messagebox.showinfo("Saved", f"Graph saved to {path}")

    def _on_weight_slider_move(self, value: str) -> None:
        """Update weight label while dragging slider."""
        w = int(float(value))
        self._weight_label.configure(text=f"TF-IDF {w}% / Co-occ {100 - w}%")

    def _on_weight_change(self) -> None:
        """Refresh graph and keywords table after weight adjustment."""
        self._populate_keywords()
        self._refresh_graph()

    def _get_weighted_keywords(self) -> list[dict]:
        """Return keywords recomputed/sorted by current weight, excluding hidden."""
        if not self._keyword_result:
            return []
        w = self._tfidf_weight_var.get() / 100.0
        cw = 1.0 - w
        result = []
        for kw in self._keyword_result.related_keywords:
            if kw["keyword"] in self._hidden_keywords:
                continue
            updated = dict(kw)
            nt = kw.get("norm_tfidf", 0.0)
            nc = kw.get("norm_cooc", 0.0)
            updated["combined_score"] = round(w * nt + cw * nc, 4)
            result.append(updated)
        result.sort(key=lambda x: x["combined_score"], reverse=True)
        return result

    # ── History tab ──────────────────────────────────────────────

    def _save_history_record(
        self, mode: str, keyword: str, period: str, article_count: int,
    ) -> None:
        top_kws = ""
        if self._keyword_result and self._keyword_result.related_keywords:
            top5 = [kw["keyword"] for kw in self._keyword_result.related_keywords[:5]]
            top_kws = ", ".join(top5)

        record = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": mode,
            "keyword": keyword,
            "period": period,
            "article_count": article_count,
            "top_keywords": top_kws,
        }
        _STORAGE.append_history(record)
        self._load_history()

    def _load_history(self) -> None:
        for item in self._hist_tree.get_children():
            self._hist_tree.delete(item)

        history = _STORAGE.load_history()
        for i, rec in enumerate(reversed(history), 1):
            self._hist_tree.insert("", "end", values=(
                i,
                rec.get("timestamp", ""),
                rec.get("mode", ""),
                rec.get("keyword", ""),
                rec.get("period", ""),
                rec.get("article_count", ""),
                rec.get("top_keywords", ""),
            ))
        self._hist_info_label.configure(text=f"Crawl history: {len(history)} records")

    def _clear_history(self) -> None:
        if not messagebox.askyesno("Clear History", "Delete all crawl history?"):
            return
        import json
        path = os.path.join(_STORAGE._output_dir, "history.json")
        if os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump([], f)
        self._load_history()
        self._status_var.set("History cleared.")

    # ── Detail Analysis tab ──────────────────────────────────────

    def _refresh_preset_list(self) -> None:
        """Refresh the preset combobox with built-in + user presets."""
        user_presets = _STORAGE.load_presets()
        names = ["Companies"] + [n for n in sorted(user_presets) if n != "Companies"]
        self._preset_combo["values"] = names

    def _load_detail_preset(self) -> None:
        name = self._detail_preset_var.get()
        if name == "Companies":
            self._detail_kw_var.set(", ".join(DEFAULT_COMPANIES))
            return
        user_presets = _STORAGE.load_presets()
        keywords = user_presets.get(name)
        if keywords:
            self._detail_kw_var.set(", ".join(keywords))
        else:
            messagebox.showwarning("Preset Not Found", f"Preset '{name}' not found.")

    def _save_detail_preset(self) -> None:
        """Save current keywords as a named preset."""
        keywords = self._parse_detail_keywords()
        if not keywords:
            messagebox.showwarning("No Keywords", "Enter at least one keyword to save.")
            return
        from tkinter import simpledialog
        name = simpledialog.askstring(
            "Save Preset", "Preset name:", parent=self._root,
        )
        if not name or not name.strip():
            return
        name = name.strip()
        if name == "Companies":
            messagebox.showwarning(
                "Reserved Name", "'Companies' is a built-in preset and cannot be overwritten.",
            )
            return
        _STORAGE.save_preset(name, keywords)
        self._refresh_preset_list()
        self._detail_preset_var.set(name)
        self._status_var.set(f"Preset '{name}' saved.")

    def _delete_detail_preset(self) -> None:
        """Delete the selected preset."""
        name = self._detail_preset_var.get()
        if name == "Companies":
            messagebox.showwarning(
                "Cannot Delete", "'Companies' is a built-in preset and cannot be deleted.",
            )
            return
        if not messagebox.askyesno("Delete Preset", f"Delete preset '{name}'?"):
            return
        if _STORAGE.delete_preset(name):
            self._refresh_preset_list()
            self._detail_preset_var.set("Companies")
            self._status_var.set(f"Preset '{name}' deleted.")
        else:
            messagebox.showwarning("Not Found", f"Preset '{name}' not found.")

    def _parse_detail_keywords(self) -> list[str]:
        raw = self._detail_kw_var.get().strip()
        if not raw:
            return []
        return [kw.strip() for kw in raw.split(",") if kw.strip()]

    def _on_detail_analyze(self) -> None:
        if not self._naver_articles:
            messagebox.showwarning(
                "No Articles",
                "Run a Naver or Google News search first to collect articles.",
            )
            return

        keywords = self._parse_detail_keywords()
        if not keywords:
            messagebox.showwarning("No Keywords", "Enter at least one keyword.")
            return

        # Check if bodies are already fetched
        bodies_fetched = all(art.body for art in self._naver_articles)
        if bodies_fetched:
            # Skip fetch, just analyze
            self._run_detail_analysis(keywords)
            return

        # Disable buttons during fetch
        self._detail_fetch_btn.configure(state="disabled")
        self._detail_reanalyze_btn.configure(state="disabled")
        self._start_btn.configure(state="disabled")
        self._cancel_event.clear()
        self._stop_btn.configure(state="normal")
        self._status_var.set("Fetching article bodies...")

        thread = threading.Thread(
            target=self._detail_worker, args=(keywords,), daemon=True,
        )
        thread.start()

    def _on_detail_reanalyze(self) -> None:
        """Re-analyze with new keywords without re-fetching bodies."""
        keywords = self._parse_detail_keywords()
        if not keywords:
            messagebox.showwarning("No Keywords", "Enter at least one keyword.")
            return

        if not self._naver_articles or not any(art.body for art in self._naver_articles):
            messagebox.showwarning(
                "No Bodies",
                "Article bodies have not been fetched yet. Use 'Fetch Articles & Analyze'.",
            )
            return

        self._run_detail_analysis(keywords)

    def _detail_worker(self, keywords: list[str]) -> None:
        """Background thread: fetch bodies then analyze."""
        try:
            fetcher = ArticleFetcher(
                delay=0.5,
                cancel_event=self._cancel_event,
                progress_callback=lambda c, t, title: self._msg_queue.put(
                    ("detail_progress", (c, t, title)),
                ),
            )
            fetcher.fetch_bodies(self._naver_articles)

            if self._cancel_event.is_set():
                self._msg_queue.put(("detail_done", None))
                return

            analyzer = DetailAnalyzer()
            result = analyzer.analyze(self._naver_articles, keywords)
            self._msg_queue.put(("detail_done", result))
        except Exception as e:
            self._msg_queue.put(("error", str(e)))

    def _handle_detail_progress(self, count: int, total: int, title: str) -> None:
        pct = (count / total * 100) if total else 0
        self._progress_var.set(min(pct, 100))
        self._progress_label.configure(text=f"{int(min(pct, 100))}%")
        self._status_var.set(f"Fetching [{count}/{total}] {title[:60]}")

    def _handle_detail_done(self, result: dict | None) -> None:
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._detail_fetch_btn.configure(state="normal")
        self._progress_var.set(100)
        self._progress_label.configure(text="100%")

        if result is None:
            self._status_var.set("Detail analysis cancelled.")
            return

        self._detail_result = result
        self._detail_reanalyze_btn.configure(state="normal")
        self._populate_detail_tree(result)
        self._refresh_detail_graph(result)
        total_mentions = sum(result["totals"].values())
        self._status_var.set(
            f"Detail analysis complete: {len(result['articles'])} articles, "
            f"{len(result['keywords'])} keywords, {total_mentions} total mentions"
        )

    def _run_detail_analysis(self, keywords: list[str]) -> None:
        """Run analysis only (no fetch) — called on main thread."""
        analyzer = DetailAnalyzer()
        result = analyzer.analyze(self._naver_articles, keywords)
        self._detail_result = result
        self._detail_reanalyze_btn.configure(state="normal")
        self._populate_detail_tree(result)
        self._refresh_detail_graph(result)
        total_mentions = sum(result["totals"].values())
        self._status_var.set(
            f"Detail re-analysis complete: {len(result['articles'])} articles, "
            f"{len(result['keywords'])} keywords, {total_mentions} total mentions"
        )

    def _clear_detail_tree(self) -> None:
        for item in self._detail_tree.get_children():
            self._detail_tree.delete(item)
        self._detail_tree["columns"] = ()

    def _populate_detail_tree(self, result: dict) -> None:
        self._clear_detail_tree()

        keywords = result["keywords"]
        columns = ("#", "Title") + tuple(keywords) + ("Total",)
        self._detail_tree["columns"] = columns

        self._detail_tree.heading("#", text="#")
        self._detail_tree.heading("Title", text="Title")
        self._detail_tree.heading("Total", text="Total")
        self._detail_tree.column("#", width=35, stretch=False)
        self._detail_tree.column("Title", width=250, stretch=True)
        self._detail_tree.column("Total", width=55, stretch=False)

        for kw in keywords:
            self._detail_tree.heading(kw, text=kw)
            self._detail_tree.column(kw, width=55, stretch=False, anchor="center")

        for i, art in enumerate(result["articles"], 1):
            values = [i, art["title"]]
            for kw in keywords:
                values.append(art["counts"].get(kw, 0))
            values.append(art["total"])
            self._detail_tree.insert("", "end", values=values)

        # Totals row
        totals_values = ["", "TOTAL"]
        for kw in keywords:
            totals_values.append(result["totals"].get(kw, 0))
        totals_values.append(sum(result["totals"].values()))
        self._detail_tree.insert("", "end", values=totals_values, tags=("totals",))
        self._detail_tree.tag_configure("totals", font=("", 9, "bold"))

        self._detail_info_label.configure(
            text=f"Articles: {len(result['articles'])} | "
                 f"Keywords: {len(keywords)} | "
                 f"Total mentions: {sum(result['totals'].values())}",
        )

    def _refresh_detail_graph(self, result: dict) -> None:
        """Show detail keyword bar chart in Graph tab."""
        self._clear_graph()

        if not result or not result["totals"]:
            return

        from .visualizer import KeywordVisualizer

        viz = KeywordVisualizer()
        fig = viz.create_detail_bar_chart(result["totals"])
        self._graph_canvas = viz.embed_in_tkinter(fig, self._graph_container)
        self._graph_canvas.mpl_connect("button_press_event", self._on_graph_button_press)

    def _on_article_double_click(self, event: tk.Event) -> None:
        """Headlines tab: double-click opens article URL in browser."""
        item = self._hl_tree.identify_row(event.y)
        if not item:
            return
        values = self._hl_tree.item(item, "values")
        url = values[4] if len(values) > 4 else ""
        if url:
            webbrowser.open(url)

    def _on_keyword_select(self, event: tk.Event) -> None:
        """Keywords tab: selecting a keyword shows matching articles."""
        selection = self._kw_tree.selection()
        if not selection:
            return
        values = self._kw_tree.item(selection[0], "values")
        keyword = values[1] if len(values) > 1 else ""
        if keyword:
            self._show_articles_for_keyword(keyword)

    def _on_detail_cell_click(self, event: tk.Event) -> None:
        """Detail tab: click on a keyword column header/cell shows articles."""
        region = self._detail_tree.identify_region(event.x, event.y)
        col_id = self._detail_tree.identify_column(event.x)
        if not col_id:
            return

        # Convert column identifier (#1, #2, ...) to column name
        try:
            col_index = int(col_id.replace("#", "")) - 1
        except ValueError:
            return
        columns = self._detail_tree["columns"]
        if col_index < 0 or col_index >= len(columns):
            return
        col_name = columns[col_index]

        # Only respond to keyword columns (not #, Title, or Total)
        if col_name in ("#", "Title", "Total"):
            return

        if region == "heading":
            self._show_articles_for_keyword(col_name)
        elif region == "cell":
            item = self._detail_tree.identify_row(event.y)
            if item and "totals" not in self._detail_tree.item(item, "tags"):
                self._show_articles_for_keyword(col_name)

    def _on_detail_double_click(self, event: tk.Event) -> None:
        """Detail tab: double-click article row opens its URL in browser."""
        item = self._detail_tree.identify_row(event.y)
        if not item:
            return
        if "totals" in self._detail_tree.item(item, "tags"):
            return
        if not self._detail_result:
            return

        values = self._detail_tree.item(item, "values")
        title = values[1] if len(values) > 1 else ""

        # Look up URL from detail_result (stored as "link")
        for art in self._detail_result["articles"]:
            if art["title"] == title:
                url = art.get("link", "")
                if url:
                    webbrowser.open(url)
                return

    # ── Right-click keyword hiding ──────────────────────────────

    def _on_kw_right_click(self, event: tk.Event) -> None:
        """Keywords tab: right-click to hide a keyword."""
        row_id = self._kw_tree.identify_row(event.y)
        if not row_id:
            return
        values = self._kw_tree.item(row_id, "values")
        keyword = values[1] if len(values) > 1 else ""
        if not keyword:
            return

        menu = tk.Menu(self._root, tearoff=0)
        menu.add_command(
            label=f"Hide '{keyword}'",
            command=lambda: self._hide_keyword(keyword),
        )
        if self._hidden_keywords:
            menu.add_command(
                label=f"Show All Hidden ({len(self._hidden_keywords)})",
                command=self._show_all_keywords,
            )
        menu.add_separator()
        menu.add_command(label="Cancel")
        menu.tk_popup(event.x_root, event.y_root)

    def _on_graph_button_press(self, event) -> None:
        """Graph tab: double-click (button 1) opens articles, right-click (button 3) hides."""
        if not self._graph_canvas:
            return

        ax = self._graph_canvas.figure.axes[0] if self._graph_canvas.figure.axes else None
        if not ax:
            return

        keyword = None
        for bar in ax.patches:
            contains, _ = bar.contains(event)
            if contains:
                keyword = getattr(bar, "_keyword", None)
                break

        if not keyword:
            return

        # Double-click left button → show articles
        if event.dblclick and event.button == 1:
            self._show_articles_for_keyword(keyword)
            return

        # Right-click → hide keyword menu
        if event.button == 3:
            widget = self._graph_canvas.get_tk_widget()
            x_root = widget.winfo_rootx() + int(event.x)
            y_root = widget.winfo_rooty() + widget.winfo_height() - int(event.y)

            menu = tk.Menu(self._root, tearoff=0)
            menu.add_command(
                label=f"Hide '{keyword}'",
                command=lambda: self._hide_keyword(keyword),
            )
            if self._hidden_keywords:
                menu.add_command(
                    label=f"Show All Hidden ({len(self._hidden_keywords)})",
                    command=self._show_all_keywords,
                )
            menu.add_separator()
            menu.add_command(label="Cancel")
            menu.tk_popup(x_root, y_root)

    def _hide_keyword(self, keyword: str) -> None:
        """Add keyword to hidden set and refresh views."""
        self._hidden_keywords.add(keyword)
        self._populate_keywords()
        self._refresh_graph()

    def _show_all_keywords(self) -> None:
        """Clear all hidden keywords and refresh views."""
        self._hidden_keywords.clear()
        self._populate_keywords()
        self._refresh_graph()

    def _on_popup_article_double_click(self, event: tk.Event) -> None:
        """Popup: double-click article opens URL in browser."""
        if not self._popup_tree:
            return
        item = self._popup_tree.identify_row(event.y)
        if not item:
            return
        values = self._popup_tree.item(item, "values")
        url = values[4] if len(values) > 4 else ""
        if url:
            webbrowser.open(url)

    def _create_article_popup(self) -> None:
        """Create (or recreate) the article popup Toplevel window."""
        self._article_popup = tk.Toplevel(self._root)
        self._article_popup.geometry("800x400")
        self._article_popup.protocol(
            "WM_DELETE_WINDOW", self._on_popup_close,
        )

        self._popup_info_label = ttk.Label(
            self._article_popup, text="", padding=5,
        )
        self._popup_info_label.pack(anchor="w")

        container = ttk.Frame(self._article_popup)
        container.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        popup_columns = ("#", "Title", "Source", "Date", "URL")
        self._popup_tree = ttk.Treeview(
            container, columns=popup_columns, show="headings", height=15,
        )
        for col in popup_columns:
            self._popup_tree.heading(
                col, text=col,
                command=lambda c=col: self._sort_popup(c),
            )

        self._popup_tree.column("#", width=35, stretch=False)
        self._popup_tree.column("Title", width=300, stretch=True)
        self._popup_tree.column("Source", width=100, stretch=False)
        self._popup_tree.column("Date", width=90, stretch=False)
        self._popup_tree.column("URL", width=200, stretch=True)

        popup_sb = ttk.Scrollbar(
            container, orient="vertical", command=self._popup_tree.yview,
        )
        self._popup_tree.configure(yscrollcommand=popup_sb.set)
        self._popup_tree.pack(side="left", fill="both", expand=True)
        popup_sb.pack(side="right", fill="y")

        self._popup_tree.bind("<Double-1>", self._on_popup_article_double_click)

    def _on_popup_close(self) -> None:
        """Handle popup window close."""
        if self._article_popup:
            self._article_popup.destroy()
            self._article_popup = None
            self._popup_tree = None
            self._popup_info_label = None

    def _sort_popup(self, col: str) -> None:
        """Sort the article popup table by clicked column."""
        if not self._popup_tree:
            return

        if col == self._popup_sort_col:
            self._popup_sort_reverse = not self._popup_sort_reverse
        else:
            self._popup_sort_col = col
            self._popup_sort_reverse = False

        col_index = {"#": 0, "Title": 1, "Source": 2, "Date": 3, "URL": 4}
        idx = col_index.get(col, 0)

        items = []
        for item_id in self._popup_tree.get_children():
            items.append(self._popup_tree.item(item_id, "values"))

        if col == "#":
            items.sort(key=lambda x: int(x[0]), reverse=self._popup_sort_reverse)
        else:
            items.sort(key=lambda x: x[idx], reverse=self._popup_sort_reverse)

        for item_id in self._popup_tree.get_children():
            self._popup_tree.delete(item_id)
        for vals in items:
            self._popup_tree.insert("", "end", values=vals)

        # Update heading with arrow indicator
        arrow = "\u25bc" if self._popup_sort_reverse else "\u25b2"
        for c in ("#", "Title", "Source", "Date", "URL"):
            self._popup_tree.heading(c, text=c)
        self._popup_tree.heading(col, text=f"{col} {arrow}")

    def _show_articles_for_keyword(self, keyword: str) -> None:
        """Show popup with articles matching the given keyword."""
        # Reuse existing popup or create new one
        if self._article_popup is None or not self._article_popup.winfo_exists():
            self._create_article_popup()

        self._article_popup.title(f"Articles containing: {keyword}")

        # Reset sort state
        self._popup_sort_col = ""
        self._popup_sort_reverse = False
        for col in ("#", "Title", "Source", "Date", "URL"):
            self._popup_tree.heading(col, text=col)

        # Clear existing rows
        for item in self._popup_tree.get_children():
            self._popup_tree.delete(item)

        articles = self._find_articles_for_keyword(keyword)

        self._popup_info_label.configure(
            text=f"Keyword: '{keyword}' -- {len(articles)} articles found",
        )

        for i, art in enumerate(articles, 1):
            self._popup_tree.insert("", "end", values=(
                i,
                art.get("title", ""),
                art.get("source", ""),
                art.get("date", ""),
                art.get("url", ""),
            ))

        self._article_popup.lift()
        self._article_popup.focus_force()

    def _find_articles_for_keyword(self, keyword: str) -> list[dict]:
        """Find articles containing the given keyword.

        Priority:
        1. Detail result with precomputed counts
        2. News articles (substring search)
        """
        kw_lower = keyword.lower()

        # Path 1: Detail result exists and keyword is in the analysis
        if self._detail_result and keyword in self._detail_result.get("keywords", []):
            matches = []
            for art in self._detail_result["articles"]:
                if art["counts"].get(keyword, 0) > 0:
                    # Look up source/date from _naver_articles by link
                    link = art.get("link", "")
                    source, date = "", ""
                    for na in self._naver_articles:
                        if na.link == link:
                            source, date = na.source, na.date
                            break
                    matches.append({
                        "title": art["title"],
                        "source": source,
                        "date": date,
                        "url": link,
                    })
            return matches

        # Path 2: Naver articles exist — substring search
        if self._naver_articles:
            matches = []
            for art in self._naver_articles:
                text = f"{art.title} {art.description} {art.body or ''}".lower()
                if kw_lower in text:
                    matches.append({
                        "title": art.title,
                        "source": art.source,
                        "date": art.date,
                        "url": art.link,
                    })
            return matches

        return []

    def _export_detail_csv(self) -> None:
        if not self._detail_result:
            messagebox.showinfo("No Data", "Run detail analysis first.")
            return
        path = _STORAGE.save_detail_csv(self._detail_result)
        self._status_var.set(f"Exported detail CSV: {path}")
        messagebox.showinfo("Export", f"Saved to {path}")

    def _export_detail_json(self) -> None:
        if not self._detail_result:
            messagebox.showinfo("No Data", "Run detail analysis first.")
            return
        path = _STORAGE.save_detail_json(self._detail_result)
        self._status_var.set(f"Exported detail JSON: {path}")
        messagebox.showinfo("Export", f"Saved to {path}")


def main() -> None:
    """Entry point for the GUI."""
    app = CrawlerGUI()
    app.run()


if __name__ == "__main__":
    main()
