"""Tkinter GUI for the keyword crawler."""

from __future__ import annotations

import os
import sys
import queue
import threading
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk, messagebox

# Support both `python -m crawler --gui` and `py gui.py` direct execution
if __name__ == "__main__" or __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "crawler"

from .analyzer import KeywordAnalyzer
from .article_fetcher import ArticleFetcher
from .config import CrawlConfig
from .detail_analyzer import DEFAULT_COMPANIES, DetailAnalyzer
from .engine import CrawlEngine
from .models import CrawlProgress, CrawlResult, KeywordResult, NewsArticle, PageData
from .naver_news import NaverNewsCrawler
from .storage import Storage

_STORAGE = Storage("output")


class CrawlerGUI:
    """Main GUI application."""

    def __init__(self) -> None:
        self._root = tk.Tk()
        self._root.title("Keyword Crawler")
        self._root.geometry("1000x750")
        self._root.minsize(850, 650)

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
        self._mode_var = tk.StringVar(value="naver")
        ttk.Radiobutton(
            row0, text="Naver News", variable=self._mode_var,
            value="naver", command=self._on_mode_change,
        ).pack(side="left", padx=(5, 5))
        ttk.Radiobutton(
            row0, text="General Crawl", variable=self._mode_var,
            value="general", command=self._on_mode_change,
        ).pack(side="left", padx=(0, 20))

        ttk.Label(row0, text="Keyword:").pack(side="left")
        self._keyword_var = tk.StringVar()
        ttk.Entry(row0, textvariable=self._keyword_var, width=20).pack(
            side="left", padx=(5, 0), fill="x", expand=True,
        )

        # Dynamic container — holds mode-specific rows (stays packed, children swap)
        self._dynamic = ttk.Frame(frame)
        self._dynamic.pack(fill="x")

        # Row 1 (General): URL
        self._row_url = ttk.Frame(self._dynamic)
        ttk.Label(self._row_url, text="URL:").pack(side="left")
        self._url_var = tk.StringVar(value="https://")
        self._url_entry = ttk.Entry(
            self._row_url, textvariable=self._url_var, width=70,
        )
        self._url_entry.pack(side="left", padx=(5, 0), fill="x", expand=True)

        # Row 2 (Naver): Date range only (no Max Results)
        self._row_naver = ttk.Frame(self._dynamic)

        today = datetime.now()
        week_ago = today - timedelta(days=7)

        ttk.Label(self._row_naver, text="Date From:").pack(side="left")
        self._date_from_var = tk.StringVar(value=week_ago.strftime("%Y.%m.%d"))
        ttk.Entry(self._row_naver, textvariable=self._date_from_var, width=12).pack(
            side="left", padx=(5, 15),
        )

        ttk.Label(self._row_naver, text="Date To:").pack(side="left")
        self._date_to_var = tk.StringVar(value=today.strftime("%Y.%m.%d"))
        ttk.Entry(self._row_naver, textvariable=self._date_to_var, width=12).pack(
            side="left", padx=(5, 0),
        )

        # Row 3 (General): Depth, pages, checkboxes
        self._row_general = ttk.Frame(self._dynamic)
        self._row_general.pack(fill="x", pady=2)

        ttk.Label(self._row_general, text="Max Depth:").pack(side="left")
        self._depth_var = tk.IntVar(value=2)
        ttk.Spinbox(
            self._row_general, textvariable=self._depth_var,
            from_=0, to=10, width=5,
        ).pack(side="left", padx=(5, 15))

        ttk.Label(self._row_general, text="Max Pages:").pack(side="left")
        self._pages_var = tk.IntVar(value=50)
        ttk.Spinbox(
            self._row_general, textvariable=self._pages_var,
            from_=1, to=10000, width=7,
        ).pack(side="left", padx=(5, 15))

        self._same_domain_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            self._row_general, text="Same Domain", variable=self._same_domain_var,
        ).pack(side="left", padx=(0, 10))

        self._robots_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            self._row_general, text="Respect robots.txt", variable=self._robots_var,
        ).pack(side="left")

        self._headlines_only_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            self._row_general, text="Headlines Only", variable=self._headlines_only_var,
        ).pack(side="left", padx=(10, 0))

        # Row 4: Buttons + progress
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
        self._row_url.pack_forget()
        self._row_naver.pack_forget()
        self._row_general.pack_forget()

        if self._mode_var.get() == "naver":
            self._row_naver.pack(fill="x", pady=2)
        else:
            self._row_url.pack(fill="x", pady=2)
            self._row_general.pack(fill="x", pady=2)

    def _build_notebook(self) -> None:
        self._notebook = ttk.Notebook(self._root)
        self._notebook.pack(fill="both", expand=True, padx=10, pady=5)

        # Tab 1: Crawl Log
        log_frame = ttk.Frame(self._notebook)
        self._notebook.add(log_frame, text="Crawl Log")

        columns = ("#", "URL", "Title", "Status", "Depth")
        self._log_tree = ttk.Treeview(
            log_frame, columns=columns, show="headings", height=15,
        )
        self._log_tree.heading("#", text="#")
        self._log_tree.heading("URL", text="URL")
        self._log_tree.heading("Title", text="Title")
        self._log_tree.heading("Status", text="Status")
        self._log_tree.heading("Depth", text="Depth")

        self._log_tree.column("#", width=40, stretch=False)
        self._log_tree.column("URL", width=350, stretch=True)
        self._log_tree.column("Title", width=300, stretch=True)
        self._log_tree.column("Status", width=60, stretch=False)
        self._log_tree.column("Depth", width=50, stretch=False)

        log_sb = ttk.Scrollbar(log_frame, orient="vertical", command=self._log_tree.yview)
        self._log_tree.configure(yscrollcommand=log_sb.set)
        self._log_tree.tag_configure("blocked", foreground="#cc6600")
        self._log_tree.tag_configure("failed", foreground="#cc0000")
        self._log_tree.tag_configure("crawled", foreground="#000000")

        self._log_tree.pack(side="left", fill="both", expand=True)
        log_sb.pack(side="right", fill="y")

        # Tab 2: Headlines
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

        # Tab 3: Keywords
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

        # Tab 4: Detail Analysis
        detail_frame = ttk.Frame(self._notebook)
        self._notebook.add(detail_frame, text="Detail Analysis")

        # Keyword settings panel
        kw_settings = ttk.LabelFrame(detail_frame, text="Keyword Settings", padding=8)
        kw_settings.pack(fill="x", padx=5, pady=5)

        preset_row = ttk.Frame(kw_settings)
        preset_row.pack(fill="x", pady=2)

        ttk.Label(preset_row, text="Preset:").pack(side="left")
        self._detail_preset_var = tk.StringVar(value="Companies")
        preset_combo = ttk.Combobox(
            preset_row, textvariable=self._detail_preset_var,
            values=["Companies"], state="readonly", width=15,
        )
        preset_combo.pack(side="left", padx=5)
        ttk.Button(
            preset_row, text="Load Preset", command=self._load_detail_preset,
        ).pack(side="left", padx=5)

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
            text="Run a Naver News search first, then click 'Fetch Articles & Analyze'.",
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

        # Tab 5: Graph (bar chart only)
        graph_frame = ttk.Frame(self._notebook)
        self._notebook.add(graph_frame, text="Graph")

        graph_controls = ttk.Frame(graph_frame)
        graph_controls.pack(fill="x", pady=5, padx=5)
        ttk.Button(
            graph_controls, text="Save Graph PNG", command=self._save_graph,
        ).pack(side="right", padx=5)

        self._graph_container = ttk.Frame(graph_frame)
        self._graph_container.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        # Tab 6: History
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

    def _on_start(self) -> None:
        keyword = self._keyword_var.get().strip()
        if not keyword:
            messagebox.showwarning("Input Required", "Please enter a keyword.")
            return

        mode = self._mode_var.get()
        if mode == "general":
            url = self._url_var.get().strip()
            if not url or url == "https://":
                messagebox.showwarning("Input Required", "Please enter a URL.")
                return

        # Clear previous results
        self._log_row_count = 0
        for tree in (self._log_tree, self._hl_tree, self._kw_tree):
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
        else:
            url = self._url_var.get().strip()
            self._status_var.set(f"Crawling {url} ...")
            self._crawl_thread = threading.Thread(
                target=self._general_worker, daemon=True,
            )
        self._crawl_thread.start()

    def _on_stop(self) -> None:
        self._cancel_event.set()
        self._status_var.set("Stopping...")
        self._stop_btn.configure(state="disabled")

    # ── Workers ──────────────────────────────────────────────────

    def _general_worker(self) -> None:
        """General crawl in background thread."""
        try:
            config = CrawlConfig(
                seed_url=self._url_var.get().strip(),
                max_depth=self._depth_var.get(),
                max_pages=self._pages_var.get(),
                same_domain=self._same_domain_var.get(),
                respect_robots=self._robots_var.get(),
                keyword=self._keyword_var.get().strip(),
            )
            engine = CrawlEngine(
                config,
                progress_callback=lambda p: self._msg_queue.put(("progress", p)),
                cancel_event=self._cancel_event,
            )
            result = engine.run()
            self._msg_queue.put(("done", result))
        except Exception as e:
            self._msg_queue.put(("error", str(e)))

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

    # ── Queue polling ────────────────────────────────────────────

    def _poll_queue(self) -> None:
        try:
            while True:
                msg_type, data = self._msg_queue.get_nowait()
                if msg_type == "progress":
                    self._handle_progress(data)
                elif msg_type == "done":
                    self._handle_done(data)
                elif msg_type == "naver_progress":
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

    # ── General crawl handlers ───────────────────────────────────

    def _handle_progress(self, progress: CrawlProgress) -> None:
        pct = (progress.pages_crawled / progress.max_pages * 100) if progress.max_pages else 0
        self._progress_var.set(pct)
        self._progress_label.configure(text=f"{int(pct)}%")

        event = progress.event_type
        if event == "blocked":
            status_text = "BLOCKED"
            status_msg = f"[robots.txt] {progress.current_url}"
        elif event == "failed":
            status_text = f"FAIL({progress.status_code})"
            status_msg = f"[FAIL {progress.status_code}] {progress.current_url}"
        else:
            status_text = str(progress.status_code)
            status_msg = (
                f"[{progress.pages_crawled}/{progress.max_pages}] "
                f"depth={progress.current_depth}  {progress.current_url}"
            )

        self._status_var.set(status_msg)
        self._log_row_count = getattr(self, "_log_row_count", 0) + 1

        self._log_tree.insert("", "end", values=(
            self._log_row_count,
            progress.current_url,
            progress.current_title or "(no title)",
            status_text,
            progress.current_depth,
        ), tags=(event,))

        children = self._log_tree.get_children()
        if children:
            self._log_tree.see(children[-1])

    def _handle_done(self, result: CrawlResult) -> None:
        self._crawl_result = result
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")

        cancelled = self._cancel_event.is_set()
        status = "Cancelled" if cancelled else "Complete"

        if result.total_crawled == 0:
            self._progress_var.set(0)
            self._progress_label.configure(text="0%")
            self._status_var.set(f"Crawl {status}: 0 pages (check robots.txt or URL)")
            messagebox.showwarning(
                "No Pages Crawled",
                f"Crawl {status}: 0 pages.\n\n"
                "Try unchecking 'Respect robots.txt' or using a different URL.",
            )
            return

        self._progress_var.set(100)
        self._progress_label.configure(text="100%")

        # Populate headlines tab (general mode)
        row = 0
        for page in result.pages:
            for hl in page.headlines:
                row += 1
                self._hl_tree.insert("", "end", values=(row, hl, "", "", page.url))
        self._hl_info_label.configure(
            text=f"Headlines: {row} titles from {result.total_crawled} pages",
        )

        # Keyword analysis
        keyword = self._keyword_var.get().strip()
        headlines_only = self._headlines_only_var.get()
        if keyword and result.pages:
            analyzer = KeywordAnalyzer()
            self._keyword_result = analyzer.analyze(
                result.pages, keyword, headlines_only=headlines_only,
            )
            self._populate_keywords()
            self._refresh_graph()
            mode = "headlines" if headlines_only else "full text"
            self._status_var.set(
                f"Crawl {status}: {result.total_crawled} pages | "
                f"Keywords ({mode}): {len(self._keyword_result.related_keywords)} found"
            )

            # Save to history
            self._save_history_record(
                mode="General",
                keyword=keyword,
                period=self._url_var.get().strip(),
                article_count=result.total_crawled,
            )
        else:
            self._status_var.set(
                f"Crawl {status}: {result.total_crawled} pages, {result.total_failed} failed"
            )

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
        self._hl_info_label.configure(text=f"Naver News: {len(articles)} articles found")

        # Switch to Headlines tab automatically
        self._notebook.select(1)

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
        self._crawl_result = CrawlResult(seed_url="naver_news_search", pages=pages)

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
            f"Naver News {status}: {len(articles)} articles | "
            f"Keywords: {kw_count} found"
        )

        # Save to history
        date_from = self._date_from_var.get().strip()
        date_to = self._date_to_var.get().strip()
        self._save_history_record(
            mode="Naver",
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

        for i, kw in enumerate(self._keyword_result.related_keywords, 1):
            self._kw_tree.insert("", "end", values=(
                i,
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

        viz = KeywordVisualizer()
        fig = viz.create_bar_chart(
            self._keyword_result.query_keyword,
            self._keyword_result.related_keywords,
        )
        self._graph_canvas = viz.embed_in_tkinter(fig, self._graph_container)

    def _save_graph(self) -> None:
        if not self._graph_canvas:
            messagebox.showinfo("No Graph", "Generate a graph first.")
            return
        os.makedirs("output", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join("output", f"graph_{ts}.png")
        self._graph_canvas.figure.savefig(path, dpi=150, bbox_inches="tight")
        self._status_var.set(f"Graph saved: {path}")
        messagebox.showinfo("Saved", f"Graph saved to {path}")

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
        path = os.path.join("output", "history.json")
        if os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump([], f)
        self._load_history()
        self._status_var.set("History cleared.")

    # ── Detail Analysis tab ──────────────────────────────────────

    def _load_detail_preset(self) -> None:
        self._detail_kw_var.set(", ".join(DEFAULT_COMPANIES))

    def _parse_detail_keywords(self) -> list[str]:
        raw = self._detail_kw_var.get().strip()
        if not raw:
            return []
        return [kw.strip() for kw in raw.split(",") if kw.strip()]

    def _on_detail_analyze(self) -> None:
        if not self._naver_articles:
            messagebox.showwarning(
                "No Articles",
                "Run a Naver News search first to collect articles.",
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
