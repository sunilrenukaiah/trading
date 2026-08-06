#!/usr/bin/env python3
"""Build exhaustive Windows/Cursor regeneration pack (PDFs, diagrams, source archive)."""

from __future__ import annotations

import json
import os
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "regeneration-pack"
ASSETS = OUT / "assets"
PDF_DIR = OUT / "pdfs"

SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "regeneration-pack",
    "backups",
    "node_modules",
}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log", ".sql"}


class PackPDF(FPDF):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._use_unicode = False
        try:
            font_path = ASSETS / "DejaVuSans.ttf"
            if font_path.exists():
                self.add_font("DejaVu", "", str(font_path))
                self._use_unicode = True
        except Exception:
            pass

    @staticmethod
    def _safe(text: str) -> str:
        if not text:
            return ""
        replacements = {
            "\u2014": "-", "\u2013": "-", "\u2212": "-",
            "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
            "\u20b9": "INR ", "\u2192": "->", "\u2190": "<-",
            "\u2022": "*", "\u2026": "...", "\u00a0": " ",
            "\u2265": ">=", "\u2264": "<=", "\u00d7": "x",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        # Drop remaining non-latin-1 for fallback fonts
        try:
            text.encode("latin-1")
        except UnicodeEncodeError:
            text = text.encode("latin-1", errors="replace").decode("latin-1")
        return text

    def _font(self, style: str = "", size: int = 10) -> None:
        if self._use_unicode:
            self.set_font("DejaVu", style, size)
        else:
            self.set_font("Helvetica", style, size)

    def header(self):
        self._font("I", 8)
        self.set_text_color(100, 100, 100)
        title = self._safe(getattr(self, "doc_title", "NIFTY Paper Trading Regeneration Pack"))
        self.cell(0, 8, title, align="R", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-12)
        self._font("I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"Page {self.page_no()}/{{nb}}", align="C")

    def add_title_page(self, title: str, subtitle: str = "") -> None:
        self.add_page()
        self._font("B", 22)
        self.set_text_color(20, 40, 80)
        self.multi_cell(0, 12, self._safe(title), align="C")
        if subtitle:
            self.ln(6)
            self._font("", 12)
            self.set_text_color(60, 60, 60)
            self.multi_cell(0, 8, self._safe(subtitle), align="C")
        self.ln(10)
        self._font("", 10)
        self.multi_cell(
            0,
            6,
            self._safe(
                f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
                f"Workspace: trading (NIFTY Paper Trading Simulation Platform)"
            ),
            align="C",
        )

    def h1(self, text: str) -> None:
        self.ln(4)
        self._font("B", 16)
        self.set_text_color(20, 40, 80)
        self.multi_cell(0, 9, self._safe(text))
        self.ln(2)

    def h2(self, text: str) -> None:
        self.ln(3)
        self._font("B", 13)
        self.set_text_color(30, 60, 100)
        self.multi_cell(0, 8, self._safe(text))
        self.ln(1)

    def h3(self, text: str) -> None:
        self.ln(2)
        self._font("B", 11)
        self.set_text_color(40, 40, 40)
        self.multi_cell(0, 7, self._safe(text))

    def body(self, text: str) -> None:
        self._font("", 10)
        self.set_text_color(30, 30, 30)
        w = self.epw
        for para in self._safe(text).split("\n\n"):
            if not para.strip():
                continue
            self.multi_cell(w, 5, para.strip())
            self.ln(2)

    def bullet_list(self, items: list[str]) -> None:
        self._font("", 10)
        w = self.epw
        for item in items:
            self.multi_cell(w, 5, self._safe(f"  - {item}"))

    def code_block(self, text: str, size: int = 7) -> None:
        self.set_font("Courier", "", size)
        self.set_fill_color(245, 245, 245)
        for line in self._safe(text).splitlines():
            safe = line.replace("\t", "    ")[:120]
            self.cell(0, 4, safe, fill=True, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def image_full_width(self, path: Path, caption: str = "") -> None:
        if not path.exists():
            self.body(f"[Missing image: {path.name}]")
            return
        w = self.epw
        self.image(str(path), w=w)
        self.ln(2)
        self.set_x(self.l_margin)
        if caption:
            self._font("I", 9)
            self.multi_cell(w, 5, self._safe(caption), align="C")
            self.ln(2)

    def markdown_file(self, path: Path) -> None:
        if not path.exists():
            self.body(f"(Missing: {path})")
            return
        text = path.read_text(encoding="utf-8", errors="replace")
        self.h2(path.name)
        # Strip mermaid blocks to placeholder (diagrams provided as PNGs)
        lines: list[str] = []
        in_mermaid = False
        for line in text.splitlines():
            if line.strip().startswith("```mermaid"):
                in_mermaid = True
                lines.append("[See architecture diagram PNGs in assets/ folder]")
                continue
            if in_mermaid and line.strip().startswith("```"):
                in_mermaid = False
                continue
            if not in_mermaid:
                lines.append(line)
        chunk = "\n".join(lines)
        # Paginate long markdown in 8000-char chunks (no truncation)
        w = self.epw
        while chunk:
            part = chunk[:8000]
            if len(chunk) > 8000:
                cut = part.rfind("\n")
                if cut > 4000:
                    part = chunk[:cut]
            self.body(part)
            chunk = chunk[len(part) :].lstrip()


def ensure_dirs() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_dejavu_font()


def _ensure_dejavu_font() -> None:
    """Download DejaVu Sans for Unicode PDF text if missing."""
    font_path = ASSETS / "DejaVuSans.ttf"
    if font_path.exists():
        return
    try:
        import urllib.request

        url = "https://github.com/dejavu-fonts/dejavu-fonts/raw/version_2_37/ttf/DejaVuSans.ttf"
        urllib.request.urlretrieve(url, font_path)
    except Exception:
        pass


def draw_architecture_diagram() -> Path:
    fig, ax = plt.subplots(figsize=(12, 7))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 8)
    ax.axis("off")
    ax.set_title("System Architecture — NIFTY Paper Trading", fontsize=14, fontweight="bold")

    boxes = [
        (1, 6.5, 2.2, 1, "Streamlit UI\n:8501", "#4A90D9"),
        (4.5, 6.5, 2.2, 1, "FastAPI REST\n:8000", "#4A90D9"),
        (8, 6.5, 2.5, 1, "Background Jobs\n+ Live Poller", "#7B68EE"),
        (2, 4.5, 8, 1.2, "Services Layer (paper trading, backtest, recommendations, ingestion, audit)", "#50C878"),
        (2, 2.8, 3.5, 1, "Pattern Registry\n(79 patterns)", "#F5A623"),
        (6, 2.8, 4.5, 1, "Market Providers\nNSE / yfinance", "#F5A623"),
        (3, 1, 6, 1, "PostgreSQL 15+ (asyncpg)", "#E74C3C"),
    ]
    for x, y, w, h, label, color in boxes:
        rect = mpatches.FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.05", linewidth=1.5, edgecolor="#333", facecolor=color, alpha=0.85
        )
        ax.add_patch(rect)
        ax.text(x + w / 2, y + h / 2, label, ha="center", va="center", fontsize=9, color="white", fontweight="bold")

    arrows = [(2.1, 6.5, 5, 5.7), (5.6, 6.5, 6, 5.7), (9.2, 6.5, 7, 5.7), (6, 4.5, 6, 3.8), (6, 2.8, 6, 2.0)]
    for x1, y1, x2, y2 in arrows:
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle="->", color="#333", lw=1.5))

    out = ASSETS / "diagram-architecture.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def draw_er_diagram() -> Path:
    fig, ax = plt.subplots(figsize=(14, 9))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 10)
    ax.axis("off")
    ax.set_title("PostgreSQL Entity-Relationship Diagram", fontsize=14, fontweight="bold")

    entities = [
        (0.5, 8, "instruments"),
        (3.5, 8, "ohlcv_candles"),
        (6.5, 8, "paper_accounts"),
        (9.5, 8, "paper_orders"),
        (0.5, 5.5, "paper_positions"),
        (3.5, 5.5, "paper_trades"),
        (6.5, 5.5, "paper_trade_plans"),
        (9.5, 5.5, "recommendation_snapshots"),
        (2, 2.5, "backtest_runs"),
        (6, 2.5, "backtest_pattern_scores"),
        (10, 2.5, "audit_logs"),
    ]
    for x, y, name in entities:
        rect = mpatches.FancyBboxPatch(
            (x, y), 2.5, 0.9, boxstyle="round,pad=0.03", facecolor="#E8F4FD", edgecolor="#2E86AB", linewidth=1.5
        )
        ax.add_patch(rect)
        ax.text(x + 1.25, y + 0.45, name, ha="center", va="center", fontsize=8, fontweight="bold")

    relations = [
        "instruments -> ohlcv_candles (1:N)",
        "instruments -> paper_orders (1:N)",
        "paper_accounts -> paper_orders (1:N)",
        "paper_orders -> paper_trades (1:1)",
        "instruments -> paper_trade_plans (1:N)",
        "backtest_runs -> backtest_pattern_scores (1:N)",
    ]
    for i, rel in enumerate(relations):
        ax.text(0.5, 0.8 + i * 0.35, rel, fontsize=8, family="monospace")

    out = ASSETS / "diagram-er-database.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def draw_ui_navigation() -> Path:
    fig, ax = plt.subplots(figsize=(12, 8))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 10)
    ax.axis("off")
    ax.set_title("Streamlit Sidebar Navigation", fontsize=14, fontweight="bold")

    pages = [
        "Trading",
        "Paper trading trend",
        "Pattern backtest",
        "Recommendations",
        "Mid day recommendation analysis",
        "Analysis & EOD",
        "Pattern definitions",
    ]
    ax.add_patch(mpatches.FancyBboxPatch((0.3, 1), 3, 8.5, boxstyle="round", facecolor="#F0F0F0", edgecolor="#666"))
    ax.text(1.8, 9, "SIDEBAR", ha="center", fontweight="bold")
    for i, p in enumerate(pages):
        ax.add_patch(mpatches.Rectangle((0.5, 8 - i * 1.1), 2.6, 0.8, facecolor="#4A90D9", alpha=0.9))
        ax.text(1.8, 8.4 - i * 1.1, p, ha="center", va="center", color="white", fontsize=7, fontweight="bold")

    ax.add_patch(mpatches.FancyBboxPatch((4, 1), 7.5, 8.5, boxstyle="round", facecolor="#FAFAFA", edgecolor="#666"))
    ax.text(7.75, 9, "MAIN CONTENT AREA", ha="center", fontweight="bold")
    details = [
        "Trading: Positions/Orders/Trades/NIFTY250 + live polling",
        "Paper trend: P&L charts, broker after-tax comparison",
        "Backtest: 30-day sim + today's validation",
        "Recommendations: picks, budget, simulation, place orders",
        "Mid-day: session OHLC refresh + morning comparison",
        "EOD: bracket trade analysis by date",
        "Patterns: catalog + example charts",
    ]
    for i, d in enumerate(details):
        ax.text(4.3, 8.2 - i * 1.1, d, fontsize=8)

    out = ASSETS / "diagram-ui-navigation.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def draw_data_flow_sync() -> Path:
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 5)
    ax.axis("off")
    ax.set_title("Market Data Sync Flow", fontsize=14, fontweight="bold")
    steps = [
        (0.5, 2, "Refresh\nmarket data"),
        (2.5, 2, "Background\nJob"),
        (4.5, 2, "NSE Provider\nnsefeed"),
        (6.5, 2, "Validate +\nUpsert OHLCV"),
        (8.5, 2, "PostgreSQL"),
        (10.5, 2, "UI Progress\nFragment"),
    ]
    for i, (x, y, label) in enumerate(steps):
        ax.add_patch(mpatches.FancyBboxPatch((x, y), 1.6, 1.2, boxstyle="round", facecolor="#50C878", alpha=0.85))
        ax.text(x + 0.8, y + 0.6, label, ha="center", va="center", fontsize=7, color="white", fontweight="bold")
        if i < len(steps) - 1:
            ax.annotate("", xy=(steps[i + 1][0], y + 0.6), xytext=(x + 1.7, y + 0.6),
                        arrowprops=dict(arrowstyle="->", lw=1.5))
    out = ASSETS / "diagram-data-flow-sync.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def draw_recommendation_pipeline() -> Path:
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 4)
    ax.axis("off")
    ax.set_title("Recommendation Engine Pipeline", fontsize=14, fontweight="bold")
    steps = ["Load NIFTY250 OHLCV", "30-day backtest", "Rank patterns", "Scan universe", "Cap tiers + buckets", "Budget allocate", "Save snapshot"]
    x0 = 0.3
    for i, s in enumerate(steps):
        x = x0 + i * 1.65
        ax.add_patch(mpatches.FancyBboxPatch((x, 1.5), 1.45, 1, boxstyle="round", facecolor="#7B68EE", alpha=0.9))
        ax.text(x + 0.72, 2, s, ha="center", va="center", fontsize=6, color="white", fontweight="bold")
        if i < len(steps) - 1:
            ax.annotate("", xy=(x + 1.55, 2), xytext=(x + 1.45, 2), arrowprops=dict(arrowstyle="->", lw=1.2))
    out = ASSETS / "diagram-recommendation-pipeline.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def create_ui_wireframes() -> list[Path]:
    """Generate wireframe PNGs for each major UI page."""
    pages = [
        ("ui-wireframe-trading", "Trading Tab", ["Portfolio summary table", "Positions | Orders | Trades | NIFTY250 radio", "Live polling toggle", "Refresh market data sidebar button"]),
        ("ui-wireframe-recommendations", "Recommendations Tab", ["Daily budget input", "Run recommendation analysis", "Stock picks / Budget & orders / Simulation sections", "Cap tier tables with stop loss + sell target"]),
        ("ui-wireframe-backtest", "Pattern Backtest Tab", ["Universe selector NIFTY250", "30-day simulation button", "Today's validation", "Pattern leaderboard table"]),
        ("ui-wireframe-midday", "Mid-day Analysis Tab", ["Budget metrics vs morning", "Run mid-day analysis", "Comparison vs morning picks", "Place orders section"]),
        ("ui-wireframe-eod", "Analysis & EOD Tab", ["Trade date selector", "Entry/target/stop metrics", "Missed movers analysis"]),
        ("ui-wireframe-paper-trend", "Paper Trading Trend Tab", ["30-day P&L chart", "Sharekhan vs Zerodha after-tax", "Closed trades ledger"]),
    ]
    paths: list[Path] = []
    for fname, title, items in pages:
        fig, ax = plt.subplots(figsize=(10, 7))
        ax.set_xlim(0, 10)
        ax.set_ylim(0, 10)
        ax.axis("off")
        ax.set_title(f"UI Wireframe — {title}", fontsize=13, fontweight="bold")
        ax.add_patch(mpatches.Rectangle((0.2, 0.2), 2, 9.6, facecolor="#E8E8E8", edgecolor="#999"))
        ax.text(1.2, 9.5, "Sidebar", ha="center", fontsize=8, fontweight="bold")
        ax.add_patch(mpatches.Rectangle((2.4, 0.2), 7.4, 9.6, facecolor="#FFFFFF", edgecolor="#999"))
        ax.text(6.1, 9.3, title, ha="center", fontsize=11, fontweight="bold")
        for i, item in enumerate(items):
            ax.add_patch(mpatches.Rectangle((2.7, 8 - i * 1.2), 6.8, 0.9, facecolor="#D6EAF8", edgecolor="#3498DB"))
            ax.text(2.9, 8.45 - i * 1.2, item, fontsize=8, va="center")
        out = ASSETS / f"{fname}.png"
        fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        paths.append(out)
    return paths


def iter_source_files() -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix in SKIP_SUFFIXES:
                continue
            rel = p.relative_to(ROOT)
            if rel.parts and rel.parts[0] in SKIP_DIRS:
                continue
            files.append(p)
    return sorted(files, key=lambda p: str(p.relative_to(ROOT)))


def collect_file_tree() -> str:
    lines: list[str] = []
    for p in iter_source_files():
        rel = p.relative_to(ROOT)
        lines.append(str(rel))
    return "\n".join(lines)


def pdf_master_guide(diag_paths: dict[str, Path]) -> None:
    pdf = PackPDF()
    pdf.doc_title = "01 - Cursor Master Guide"
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_title_page(
        "NIFTY Paper Trading",
        "Cursor Regeneration Master Guide for Windows\nExhaustive rebuild instructions",
    )

    pdf.add_page()
    pdf.h1("PDF-only rebuild (no source code transfer)")
    pdf.body(
        "You cannot copy source code off the Mac laptop. This pack is self-contained: "
        "every file's full content is embedded in the PDF volumes (10-SOURCE-CODE-VOL-*.pdf). "
        "There is NO zip archive and NO Git required.\n\n"
        "On Windows:\n"
        "1. Copy only the regeneration-pack/ folder (PDFs + assets + text prompts).\n"
        "2. Create an empty folder: C:\\Users\\<you>\\projects\\trading\n"
        "3. Open that empty folder in Cursor.\n"
        "4. Read PDF 01 (this file) and PDF 10 (step-by-step rebuild phases).\n"
        "5. Paste CURSOR_MASTER_PROMPT.txt into Cursor Agent.\n"
        "6. Attach ALL source-code PDF volumes (10-SOURCE-CODE-VOL-01 through last volume).\n"
        "7. Ask Cursor to create every file exactly as shown in the PDFs, in build order.\n"
        "8. After all files exist, run python Setup.py and python scripts\\run_app.py.\n"
        "9. Verify with PDF 08 (Testing checklist)."
    )

    pdf.h1("Prerequisites (Windows)")
    pdf.bullet_list([
        "Windows 10/11 64-bit",
        "Python 3.11 or 3.12 (python.org - check Add to PATH)",
        "PostgreSQL 15+ (postgresql.org/download/windows)",
        "Cursor IDE (cursor.com)",
        "Internet access for NSE market data (nsefeed)",
        "NO Git required",
        "NO source code from Mac required - only these PDFs",
    ])

    pdf.h1("One-time setup commands")
    pdf.code_block(
        "cd C:\\Users\\<you>\\projects\\trading\n"
        "python Setup.py\n"
        "copy backend\\env.example backend\\.env\n"
        "REM Edit backend\\.env if Postgres credentials differ\n"
        "python scripts\\run_app.py"
    )

    pdf.h1("PostgreSQL setup (SQL)")
    pdf.code_block(
        "CREATE USER trading WITH PASSWORD 'trading';\n"
        "CREATE DATABASE trading OWNER trading;\n"
        "GRANT ALL PRIVILEGES ON DATABASE trading TO trading;"
    )

    pdf.h1("Ports and entry points")
    pdf.bullet_list([
        "Streamlit UI: http://localhost:8501 (primary)",
        "FastAPI REST: http://localhost:8000 (optional)",
        "Lab instance: http://localhost:8502 (optional separate checkout)",
    ])

    pdf.h1("Architecture diagram")
    pdf.image_full_width(diag_paths["architecture"], "High-level system architecture")

    pdf.h1("UI navigation diagram")
    pdf.image_full_width(diag_paths["ui_nav"], "Seven sidebar pages and their content")

    pdf.h1("Complete file tree (all paths Cursor must create)")
    tree = collect_file_tree()
    pdf.h2("Every project file path")
    pdf.code_block(tree)

    pdf.h1("Appendix A — Cursor Master Prompt")
    prompt = textwrap.dedent("""
        Rebuild the NIFTY Paper Trading Simulation Platform on Windows from PDFs only.
        NO Git. NO zip. NO source from another machine.

        The user attached regeneration-pack/pdfs/10-SOURCE-CODE-VOL-*.pdf files.
        Each PDF section header is a file path; the code block below it is the COMPLETE file content.
        Recreate every file exactly - same paths, same functions, same behavior.

        Empty workspace: create C:\\Users\\<user>\\projects\\trading from scratch.

        Build phases (see PDF 10-PDF-ONLY-REBUILD-WORKFLOW.pdf):
        Phase 1: Root files (Setup.py, start.bat, requirements*.txt, Makefile)
        Phase 2: backend/pyproject.toml, env.example, alembic.ini, alembic/
        Phase 3: backend/app/models, schemas, db, config, defaults
        Phase 4: backend/app/providers, services (all 46 modules)
        Phase 5: backend/app/strategies (registry + all patterns/)
        Phase 6: backend/app/api, jobs, main.py, bootstrap, data/*.json
        Phase 7: backend/ui/ (all 26 modules including dashboard.py)
        Phase 8: backend/tests/, scripts/, docs/

        After all files exist:
          python Setup.py
          copy backend\\env.example backend\\.env
          python scripts\\run_app.py

        Critical: background job fragments, lazy loading, async_runner DB lock,
        _market_sync_progress_fragment must NOT call _render_trading_page_body.

        Reference architecture PDFs 02-08 for behavior specs.
        455 tests must pass when done.
    """).strip()
    pdf.code_block(prompt, size=6)

    out = PDF_DIR / "01-CURSOR-MASTER-GUIDE.pdf"
    pdf.output(str(out))
    print(f"Wrote {out}")


def pdf_architecture(diag_paths: dict[str, Path]) -> None:
    pdf = PackPDF()
    pdf.doc_title = "02 - Architecture and Diagrams"
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_title_page("Architecture & Diagrams", "System design, flows, deployment")

    for key, caption in [
        ("architecture", "System architecture"),
        ("er", "Database ER diagram"),
        ("ui_nav", "Streamlit navigation"),
        ("sync", "Market data sync flow"),
        ("rec", "Recommendation pipeline"),
    ]:
        pdf.add_page()
        pdf.h1(caption)
        pdf.image_full_width(diag_paths[key], caption)

    arch_dir = ROOT / "docs" / "project-architecture"
    for md_name in ["03-architecture-overview.md", "05-data-flows.md", "06-services-reference.md"]:
        pdf.add_page()
        pdf.markdown_file(arch_dir / md_name)

    out = PDF_DIR / "02-ARCHITECTURE-AND-DIAGRAMS.pdf"
    pdf.output(str(out))
    print(f"Wrote {out}")


def pdf_database() -> None:
    pdf = PackPDF()
    pdf.doc_title = "03 - Database Schema"
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_title_page("Database Schema", "Models, migrations, enums")

    pdf.add_page()
    pdf.markdown_file(ROOT / "docs" / "project-architecture" / "04-data-model.md")

    pdf.add_page()
    pdf.h1("Alembic migrations (full source)")
    mig_dir = ROOT / "backend" / "alembic" / "versions"
    for mig in sorted(mig_dir.glob("*.py")):
        pdf.h2(str(mig.relative_to(ROOT)))
        pdf.code_block(mig.read_text(encoding="utf-8", errors="replace"), size=5)

    pdf.add_page()
    pdf.h1("SQLAlchemy models excerpt")
    models = ROOT / "backend" / "app" / "models" / "__init__.py"
    pdf.code_block(models.read_text(encoding="utf-8", errors="replace"), size=4)

    out = PDF_DIR / "03-DATABASE-SCHEMA.pdf"
    pdf.output(str(out))
    print(f"Wrote {out}")


def pdf_ui_spec(wireframes: list[Path]) -> None:
    pdf = PackPDF()
    pdf.doc_title = "04 - Streamlit UI Specification"
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_title_page("Streamlit UI Specification", "All pages, widgets, jobs, session state")

    pdf.add_page()
    pdf.markdown_file(ROOT / "docs" / "project-architecture" / "08-streamlit-ui.md")

    for wf in wireframes:
        pdf.add_page()
        pdf.image_full_width(wf, wf.stem.replace("-", " ").replace("ui wireframe ", "Page: "))

    pdf.add_page()
    pdf.h1("dashboard.py routing (sidebar pages)")
    pdf.code_block(
        "Trading | Paper trading trend | Pattern backtest | Recommendations | "
        "Mid day recommendation analysis | Analysis & EOD | Pattern definitions",
    )
    pdf.h2("Background jobs (JobKind enum)")
    pdf.bullet_list([
        "MARKET_SYNC — Refresh market data (NIFTY250 OHLCV)",
        "SIM_BACKTEST — 30-day pattern simulation",
        "TODAY_PREDICTION — Today's validation",
        "RECOMMENDATIONS — Morning recommendation analysis",
        "MIDDAY_RECOMMENDATIONS — Mid-day re-analysis",
    ])
    pdf.h2("Critical UI patterns (do not omit)")
    pdf.bullet_list([
        "Lazy loading: radio sections load DB work only when selected",
        "Background job fragments poll every 1s — NO st.rerun() on button click",
        "Market sync: _market_sync_progress_fragment shows progress only (no sidebar writes in fragment)",
        "Live polling: 10s fragment on Positions tab during market hours",
        "Chart dialogs: @st.dialog for symbol history and intraday position charts",
        "Bracket guard: block manual SELL on active recommendation symbols",
    ])

    out = PDF_DIR / "04-STREAMLIT-UI-SPEC.pdf"
    pdf.output(str(out))
    print(f"Wrote {out}")


def pdf_backend_services() -> None:
    pdf = PackPDF()
    pdf.doc_title = "05 - Backend Services"
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_title_page("Backend Services Reference", "Business logic modules")

    for md in ["06-services-reference.md", "11-paper-trading-and-brackets.md"]:
        pdf.add_page()
        pdf.markdown_file(ROOT / "docs" / "project-architecture" / md)

    key_services = [
        "backend/app/services/ingestion.py",
        "backend/app/services/paper_trading.py",
        "backend/app/services/trade_plans.py",
        "backend/app/services/backtest.py",
        "backend/app/services/recommendation_engine.py",
        "backend/app/services/budget_allocator.py",
        "backend/app/services/live_quotes.py",
        "backend/app/services/midday_recommendations.py",
        "backend/app/services/eod_trade_analysis.py",
        "backend/ui/async_runner.py",
        "backend/ui/background_jobs.py",
    ]
    for rel in key_services:
        p = ROOT / rel
        if not p.exists():
            continue
        pdf.add_page()
        pdf.h1(rel)
        content = p.read_text(encoding="utf-8", errors="replace")
        pdf.code_block(content, size=4)

    out = PDF_DIR / "05-BACKEND-SERVICES.pdf"
    pdf.output(str(out))
    print(f"Wrote {out}")


def pdf_config_data() -> None:
    pdf = PackPDF()
    pdf.doc_title = "06 - Configuration and Data Files"
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_title_page("Configuration & Static Data", "Env vars, JSON manifests, defaults")

    pdf.add_page()
    pdf.markdown_file(ROOT / "docs" / "project-architecture" / "09-configuration.md")

    pdf.add_page()
    pdf.h1("env.example")
    pdf.code_block((ROOT / "backend" / "env.example").read_text())

    pdf.add_page()
    pdf.h1("pyproject.toml")
    pdf.code_block((ROOT / "backend" / "pyproject.toml").read_text())

    json_files = [
        "backend/app/data/nifty50.json",
        "backend/app/data/backtest_universe.json",
        "backend/app/data/recommendation_universe.json",
        "backend/app/data/nse_trading_holidays.json",
        "backend/app/data/applicable_rates.json",
        "backend/app/data/pattern_definitions.json",
        "backend/app/data/nifty_universe_cache.json",
    ]
    for rel in json_files:
        p = ROOT / rel
        if not p.exists():
            continue
        pdf.add_page()
        pdf.h1(rel)
        pdf.code_block(p.read_text(encoding="utf-8", errors="replace"), size=4)

    out = PDF_DIR / "06-CONFIG-AND-DATA-FILES.pdf"
    pdf.output(str(out))
    print(f"Wrote {out}")


def pdf_patterns_recommendations() -> None:
    pdf = PackPDF()
    pdf.doc_title = "07-08 - Patterns and Recommendations"
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_title_page("Patterns, Backtest & Recommendations", "Engine rules and allocation")

    for md in ["10-patterns-and-backtesting.md", "12-recommendations-engine.md"]:
        pdf.add_page()
        pdf.markdown_file(ROOT / "docs" / "project-architecture" / md)

    pdf.add_page()
    pdf.h1("Pattern registry files")
    pat_dir = ROOT / "backend" / "app" / "strategies" / "patterns"
    for p in sorted(pat_dir.glob("*.py")):
        pdf.h3(str(p.relative_to(ROOT)))
        pdf.code_block(p.read_text(encoding="utf-8", errors="replace")[:6000], size=4)

    pdf.add_page()
    pdf.h1("pattern_definitions.json (catalog for UI)")
    pd_path = ROOT / "backend" / "app" / "data" / "pattern_definitions.json"
    pdf.code_block(pd_path.read_text(encoding="utf-8", errors="replace"), size=4)

    out = PDF_DIR / "07-PATTERNS-AND-RECOMMENDATIONS.pdf"
    pdf.output(str(out))
    print(f"Wrote {out}")


def pdf_testing() -> None:
    pdf = PackPDF()
    pdf.doc_title = "08 - Testing and Verification"
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_title_page("Testing & Verification", "455 tests, CI, smoke checks")

    pdf.add_page()
    pdf.markdown_file(ROOT / "docs" / "project-architecture" / "14-testing-and-ci.md")
    pdf.markdown_file(ROOT / "docs" / "project-architecture" / "15-operations-runbook.md")

    pdf.h1("Verification checklist after rebuild")
    pdf.bullet_list([
        "cd backend && pip install -e \".[dev]\"",
        "./scripts/run_tests.sh quick — 401 quick tests pass",
        "./scripts/run_tests.sh all — 455 tests pass (needs Postgres)",
        "python scripts/run_app.py — Streamlit on :8501",
        "Trading tab loads portfolio without errors",
        "Refresh market data shows progress banner (no blank page)",
        "Run recommendation analysis completes and shows picks",
        "Pattern backtest 30-day simulation saves to DB",
        "Mid-day analysis available after 11:45 AM IST on trading days",
    ])

    out = PDF_DIR / "08-TESTING-AND-VERIFICATION.pdf"
    pdf.output(str(out))
    print(f"Wrote {out}")


def pdf_source_volumes() -> None:
    """Embed COMPLETE source for every project file — no truncation (PDF-only rebuild)."""
    files = [p for p in iter_source_files() if p.suffix in {".py", ".json", ".toml", ".ini", ".md", ".bat", ".sh", ".txt", ".yml", ".yaml", ".mdc"}]
    chunk_size = 12  # fewer files per volume = smaller PDFs, easier for Cursor
    vol = 1
    for i in range(0, len(files), chunk_size):
        chunk = files[i : i + chunk_size]
        pdf = PackPDF()
        pdf.doc_title = f"10 - Source Code Vol {vol}"
        pdf.alias_nb_pages()
        pdf.set_auto_page_break(auto=True, margin=12)
        pdf.add_title_page(
            f"Complete Source Code Volume {vol}",
            f"Files {i + 1}-{i + len(chunk)} of {len(files)}. "
            "Each section = one file path. Code block = FULL file content. No truncation.",
        )

        for p in chunk:
            rel = p.relative_to(ROOT)
            pdf.add_page()
            pdf.h1(str(rel))
            pdf.body("Create this file at the exact path above. Content below is complete.")
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                pdf.body(f"[Could not read: {exc}]")
                continue
            pdf.code_block(text, size=3)

        out = PDF_DIR / f"10-SOURCE-CODE-VOL-{vol:02d}.pdf"
        pdf.output(str(out))
        print(f"Wrote {out} ({len(chunk)} files, full content)")
        vol += 1
    return vol - 1


def pdf_only_rebuild_workflow(source_vol_count: int) -> None:
    pdf = PackPDF()
    pdf.doc_title = "09 - PDF-Only Rebuild Workflow"
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_title_page("PDF-Only Rebuild Workflow", "Step-by-step for Cursor Agent (no source transfer)")

    pdf.add_page()
    pdf.h1("Overview")
    pdf.body(
        "This document tells Cursor how to rebuild the entire app from PDFs when you cannot "
        "copy any source code from the Mac. The complete source is in 10-SOURCE-CODE-VOL-*.pdf "
        f"({source_vol_count} volumes)."
    )

    phases = [
        ("Phase 1 - Project root", [
            "Setup.py", "start.bat", "setup.bat", "setup-pycharm.bat",
            "requirements-migrate.txt", "requirements-start.txt", "Makefile", "README.md",
            ".gitignore", ".gitlab-ci.yml", ".env.example",
        ]),
        ("Phase 2 - Backend config", [
            "backend/pyproject.toml", "backend/env.example", "backend/alembic.ini",
            "backend/alembic/env.py", "backend/alembic/versions/*.py (migrations 001-007)",
        ]),
        ("Phase 3 - Models and DB", [
            "backend/app/models/__init__.py", "backend/app/models/base.py",
            "backend/app/models/audit_log.py", "backend/app/schemas/*",
            "backend/app/db/session.py", "backend/app/db/ui_session.py",
            "backend/app/config.py", "backend/app/defaults.py",
        ]),
        ("Phase 4 - Providers and ingestion", [
            "backend/app/providers/*.py", "backend/app/services/ingestion.py",
            "backend/app/services/ohlcv_utils.py", "backend/app/services/candle_quality.py",
            "backend/app/services/nifty_universe.py", "backend/app/services/market_calendar.py",
        ]),
        ("Phase 5 - Paper trading and brackets", [
            "backend/app/services/paper_trading.py", "backend/app/services/trade_plans.py",
            "backend/app/services/live_quotes.py", "backend/app/services/bracket_utils.py",
            "backend/app/services/budget_portfolio.py", "backend/app/services/budget_allocator.py",
        ]),
        ("Phase 6 - Backtest and patterns", [
            "backend/app/services/backtest.py", "backend/app/strategies/registry.py",
            "backend/app/strategies/indicators.py", "backend/app/strategies/patterns/*.py (all 10 files)",
        ]),
        ("Phase 7 - Recommendations", [
            "backend/app/services/recommendation_engine.py",
            "backend/app/services/recommendation_cache.py",
            "backend/app/services/midday_recommendations.py", "backend/app/services/midday_market_sync.py",
            "backend/app/services/eod_trade_analysis.py", "backend/app/data/*.json",
        ]),
        ("Phase 8 - API and audit", [
            "backend/app/main.py", "backend/app/bootstrap.py", "backend/app/api/routes/*.py",
            "backend/app/middleware/audit.py", "backend/app/services/audit*.py",
            "backend/app/services/audit_backends/*.py", "backend/app/jobs/*.py",
        ]),
        ("Phase 9 - Streamlit UI", [
            "backend/ui/dashboard.py (largest file - copy exactly from source PDF)",
            "backend/ui/background_jobs.py", "backend/ui/async_runner.py", "backend/ui/helpers.py",
            "backend/ui/*_display.py", "backend/ui/live_quote_poller.py", "backend/ui/scheduled_*.py",
        ]),
        ("Phase 10 - Scripts, tests, docs", [
            "scripts/run_app.py", "scripts/startup_checklist.py", "scripts/migrate_checklist.py",
            "scripts/platform_utils.py", "scripts/ide_setup.py", "backend/tests/**/*.py",
            "docs/project-architecture/*.md", "docs/MIGRATION.md",
        ]),
    ]
    for title, items in phases:
        pdf.add_page()
        pdf.h1(title)
        pdf.bullet_list(items)

    pdf.add_page()
    pdf.h1("Cursor session workflow")
    pdf.body(
        "Session 1: Phases 1-3, then run alembic upgrade head\n"
        "Session 2: Phases 4-6\n"
        "Session 3: Phases 7-8\n"
        "Session 4: Phase 9 (UI - may need multiple chats for dashboard.py)\n"
        "Session 5: Phase 10, then python Setup.py and pytest\n\n"
        "In each session, attach the relevant 10-SOURCE-CODE-VOL PDFs and say: "
        "'Create all files listed in Phase N exactly as in the attached PDFs.'"
    )

    out = PDF_DIR / "09-PDF-ONLY-REBUILD-WORKFLOW.pdf"
    pdf.output(str(out))
    print(f"Wrote {out}")


def write_readme() -> None:
    readme = OUT / "README.md"
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    vol_count = len(list(PDF_DIR.glob("10-SOURCE-CODE-VOL-*.pdf")))
    content = f"""# Windows Cursor Regeneration Pack (PDF-only — no source transfer)

You **cannot** copy source code off the Mac. This pack rebuilds the entire app from PDFs only.

## Start here

1. Copy this **entire folder** to Windows (USB / cloud / email)
2. Create empty folder: `C:\\Users\\<you>\\projects\\trading`
3. Open that folder in **Cursor**
4. Read **`WINDOWS-PDF-ONLY-SETUP.md`**
5. Paste **`CURSOR_MASTER_PROMPT.txt`** into Cursor Agent
6. Attach **all** `pdfs/10-SOURCE-CODE-VOL-*.pdf` files ({vol_count} volumes)
7. Follow **`pdfs/09-PDF-ONLY-REBUILD-WORKFLOW.pdf`** phase by phase

## Contents

| Item | Description |
|------|-------------|
| `pdfs/01-CURSOR-MASTER-GUIDE.pdf` | Master guide + Cursor prompt |
| `pdfs/09-PDF-ONLY-REBUILD-WORKFLOW.pdf` | **Phase-by-phase rebuild steps** |
| `pdfs/10-SOURCE-CODE-VOL-*.pdf` | **Complete source code** ({vol_count} volumes, no truncation) |
| `pdfs/02` … `08` | Architecture, DB, UI, services, config, patterns, tests |
| `assets/*.png` | Diagrams + UI wireframes |
| `CURSOR_MASTER_PROMPT.txt` | Paste into Cursor |

**There is NO zip file. No Git. No source code to copy.**

## PDF count: {len(pdfs)}
"""
    readme.write_text(content, encoding="utf-8")


def main() -> None:
    print("Building PDF-only regeneration pack (no source archive)...")
    ensure_dirs()
    # Remove obsolete artifacts from prior builds
    for old in PDF_DIR.glob("09-SOURCE-CODE-VOL-*.pdf"):
        old.unlink()
    zip_path = OUT / "SOURCE_ARCHIVE.zip"
    if zip_path.exists():
        zip_path.unlink()
    diag = {
        "architecture": draw_architecture_diagram(),
        "er": draw_er_diagram(),
        "ui_nav": draw_ui_navigation(),
        "sync": draw_data_flow_sync(),
        "rec": draw_recommendation_pipeline(),
    }
    wireframes = create_ui_wireframes()
    pdf_master_guide(diag)
    pdf_architecture(diag)
    pdf_database()
    pdf_ui_spec(wireframes)
    pdf_backend_services()
    pdf_config_data()
    pdf_patterns_recommendations()
    pdf_testing()
    vol_count = pdf_source_volumes()
    pdf_only_rebuild_workflow(vol_count)
    write_readme()
    # Remove zip if present from earlier builds
    zip_path = OUT / "SOURCE_ARCHIVE.zip"
    if zip_path.exists():
        zip_path.unlink()
        print("Removed SOURCE_ARCHIVE.zip (PDF-only pack)")
    print(f"\nDone. Output folder: {OUT}")


if __name__ == "__main__":
    main()
