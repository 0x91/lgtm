"""PDF export for code review analysis reports.

Uses fpdf2 for pure-Python PDF generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fpdf import FPDF

from .repo import get_repo


@dataclass
class ReportData:
    """All data needed to render a report."""

    # Summary
    total_prs: int
    first_pr: str | None
    last_pr: str | None
    merged_prs: int
    repo_name: str

    # Approval context
    total_approvals: int
    empty_approvals: int
    expert_approvals: int
    expert_empty: int
    familiar_approvals: int
    familiar_empty: int
    firsttime_approvals: int
    firsttime_empty: int

    # Quick large approvals
    quick_large: list[dict]

    # Review depth by type
    depth_data: list[dict]

    # Module reviewers
    module_data: list[dict]

    # Thread outcomes
    thread_outcomes: dict

    # Iteration stats
    iteration_stats: dict

    # Feedback stats
    feedback_stats: dict

    # Reviewer experience
    reviewer_experience: dict
    first_time_reviews: list[dict]

    # Red flags
    red_flags: list[dict]


def format_pct(value: float | None) -> str:
    """Format percentage."""
    if value is None:
        return "N/A"
    if value == int(value):
        return f"{int(value)}%"
    return f"{value:.1f}%"


def format_hours(value: float | None) -> str:
    """Format hours."""
    if value is None:
        return "N/A"
    if value < 1:
        return f"{int(value * 60)} min"
    if value < 24:
        return f"{value:.1f} hrs"
    return f"{value / 24:.1f} days"


def format_minutes(value: float | None) -> str:
    """Format minutes."""
    if value is None:
        return "N/A"
    if value < 1:
        return "<1 min"
    return f"{int(value)} min"


class ReportPDF(FPDF):
    """Custom PDF class for code review reports."""

    def __init__(self, repo_name: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.repo_name = repo_name
        self.set_auto_page_break(auto=True, margin=15)
        self.set_left_margin(15)
        self.set_right_margin(15)

    def header(self):
        """Add header to each page."""
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"LGTM Report | {self.repo_name}", align="L")
        self.ln(5)

    def footer(self):
        """Add footer to each page."""
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")

    def chapter_title(self, title: str):
        """Add a chapter title."""
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(0, 102, 204)
        self.cell(0, 10, title, ln=True)
        self.ln(2)

    def section_title(self, title: str):
        """Add a section title."""
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(51, 51, 51)
        self.cell(0, 8, title, ln=True)
        self.ln(1)

    def body_text(self, text: str):
        """Add body text."""
        self.set_font("Helvetica", "", 10)
        self.set_text_color(0, 0, 0)
        self.multi_cell(0, 5, text)
        self.ln(2)

    def bullet(self, text: str):
        """Add a bullet point."""
        self.set_font("Helvetica", "", 10)
        self.set_text_color(0, 0, 0)
        # Use cell for simple text output to avoid multi_cell issues
        self.cell(0, 5, f"  - {text}", ln=True)

    def metric(self, label: str, value: str, note: str = ""):
        """Add a metric line."""
        self.set_font("Helvetica", "", 10)
        self.set_text_color(0, 0, 0)
        line = f"  {label}: {value}"
        if note:
            line += f" ({note})"
        self.cell(0, 5, line, ln=True)

    def warning(self, text: str):
        """Add a warning message."""
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(204, 102, 0)
        self.multi_cell(0, 5, f"! {text}")
        self.set_text_color(0, 0, 0)

    def table_header(self, columns: list[tuple[str, int]]):
        """Add a table header row."""
        self.set_font("Helvetica", "B", 9)
        self.set_fill_color(240, 240, 240)
        for col, width in columns:
            self.cell(width, 6, col, border=1, fill=True)
        self.ln()

    def table_row(self, values: list[str], widths: list[int]):
        """Add a table data row."""
        self.set_font("Helvetica", "", 9)
        for val, width in zip(values, widths, strict=False):
            self.cell(width, 5, str(val), border=1)
        self.ln()


def generate_pdf(data: ReportData, output_path: Path | str | None = None) -> Path:
    """Generate a PDF report from the report data.

    Args:
        data: ReportData containing all analysis results
        output_path: Path for output PDF. Defaults to ~/.cache/lgtm/{repo}/report.pdf

    Returns:
        Path to the generated PDF
    """
    if output_path is None:
        repo = get_repo()
        output_path = repo.data_dir / "report.pdf"
    output_path = Path(output_path)

    pdf = ReportPDF(data.repo_name)
    pdf.add_page()

    # Title
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(0, 51, 102)
    pdf.cell(0, 15, "Is Code Review Adding Value?", ln=True, align="C")
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(102, 102, 102)

    # Year range
    year_str = ""
    if data.first_pr and data.last_pr:
        first_year = data.first_pr[:4] if data.first_pr else ""
        last_year = data.last_pr[:4] if data.last_pr else ""
        year_str = first_year if first_year == last_year else f"{first_year}-{last_year}"

    pdf.cell(0, 8, f"{data.repo_name} | {year_str} | {data.total_prs:,} PRs", ln=True, align="C")
    pdf.ln(10)

    # =========================================================================
    # The Short Answer
    # =========================================================================
    pdf.chapter_title("The Short Answer")

    total = data.total_approvals or 1
    empty_pct = 100.0 * data.empty_approvals / total
    pdf.body_text(f"{format_pct(empty_pct)} of approvals have no comment or feedback. But context matters:")
    pdf.ln(2)

    # Expert context
    if data.expert_approvals > 0:
        expert_pct = 100.0 * data.expert_empty / data.expert_approvals
        pdf.bullet(f"From module experts: {format_pct(expert_pct)} empty (probably fine)")

    # Familiar context
    if data.familiar_approvals > 0:
        familiar_pct = 100.0 * data.familiar_empty / data.familiar_approvals
        pdf.bullet(f"From familiar reviewers: {format_pct(familiar_pct)} empty (know the author)")

    # First-time context
    if data.firsttime_approvals > 0:
        firsttime_pct = 100.0 * data.firsttime_empty / data.firsttime_approvals
        pdf.bullet(f"From first-time reviewers: {format_pct(firsttime_pct)} empty (worth checking)")

    if data.quick_large:
        pdf.ln(3)
        pdf.warning(
            f"{len(data.quick_large)} large PRs (500+ lines) were approved in under 5 minutes with no comments."
        )
    pdf.ln(5)

    # =========================================================================
    # Review Depth by Risk
    # =========================================================================
    pdf.chapter_title("Review Depth by Risk")
    pdf.body_text("Are we spending effort where it matters?")

    type_labels = {
        "large-change": "Large (500+)",
        "new-code": "New code",
        "refactor": "Refactors",
        "cleanup": "Cleanup/deletions",
    }

    columns = [("PR Type", 40), ("Count", 25), ("Avg Comments", 30), ("% Feedback", 30), ("Concern?", 35)]
    widths = [c[1] for c in columns]
    pdf.table_header(columns)

    for row in data.depth_data:
        pr_type = type_labels.get(row["type"], row["type"])
        pct = row["pct_feedback"] or 0

        # Determine concern
        if row["type"] == "large-change":
            concern = "Good" if pct >= 60 else "Risk"
        elif row["type"] == "new-code":
            concern = "Good" if pct >= 50 else "Check"
        elif row["type"] == "refactor":
            concern = "OK" if pct >= 20 else "Maybe OK"
        else:
            concern = "Expected"

        pdf.table_row([pr_type, f"{row['prs']:,}", f"{row['avg_comments']:.1f}", format_pct(pct), concern], widths)

    pdf.ln(5)

    # =========================================================================
    # Did Review Lead to Action?
    # =========================================================================
    pdf.chapter_title("Did Review Lead to Action?")
    pdf.body_text("Outcomes matter more than activity")

    # Thread outcomes
    total_threads = data.thread_outcomes.get("total_threads", 0)
    if total_threads > 0:
        has_resolution = data.thread_outcomes.get("has_resolution_data", False)
        pdf.section_title("Review Threads")
        pdf.metric("Total threads", f"{total_threads:,}")

        if has_resolution:
            resolved = data.thread_outcomes.get("resolved", 0)
            outdated = data.thread_outcomes.get("outdated", 0)
            addressed_rate = data.thread_outcomes.get("addressed_rate", 0)
            pdf.metric("Resolved", f"{resolved:,}")
            pdf.metric("Outdated (code changed)", f"{outdated:,}")
            pdf.metric("Addressed rate", format_pct(addressed_rate))
        else:
            discussed = data.thread_outcomes.get("discussed", 0)
            pdf.metric("With back-and-forth", f"{discussed:,}")
        pdf.ln(3)

    # Iteration stats
    total_prs = data.iteration_stats.get("total_prs", 0)
    if total_prs > 0:
        iteration_rate = data.iteration_stats.get("iteration_rate", 0)
        avg_commits = data.iteration_stats.get("avg_commits", 0)
        pdf.section_title("Post-Review Iteration")
        pdf.metric("PRs with post-review commits", format_pct(iteration_rate))
        pdf.metric("Avg commits after review", f"{avg_commits:.1f}")
        pdf.ln(3)

    # Feedback quality
    total_comments = data.feedback_stats.get("total", 0)
    if total_comments > 0:
        code_rate = data.feedback_stats.get("code_rate", 0)
        link_rate = data.feedback_stats.get("link_rate", 0)
        pdf.section_title("Comment Quality")
        pdf.metric("With code suggestions", format_pct(code_rate))
        pdf.metric("With links/references", format_pct(link_rate))
    pdf.ln(5)

    # =========================================================================
    # Reviewer File Experience
    # =========================================================================
    pdf.chapter_title("Reviewer File Experience")
    pdf.body_text("Are reviewers familiar with what they're reviewing?")

    exp = data.reviewer_experience
    total_reviews = exp.get("total_reviews", 0)
    if total_reviews > 0:
        avg_familiarity = exp.get("avg_familiarity", 0)
        pdf.metric("Average file familiarity", format_pct(avg_familiarity), f"across {total_reviews:,} reviews")

        pdf.ln(2)
        pdf.section_title("Breakdown")
        pdf.metric("Fully familiar (100%)", f"{exp.get('fully_familiar', 0):,}")
        pdf.metric("Mostly familiar (75%+)", f"{exp.get('mostly_familiar', 0):,}")
        pdf.metric("Mostly unfamiliar (<25%)", f"{exp.get('mostly_unfamiliar', 0):,}")
        pdf.metric("First-time (0%)", f"{exp.get('fully_unfamiliar', 0):,}")

        if data.first_time_reviews:
            pdf.ln(3)
            pdf.warning(
                f"{len(data.first_time_reviews)} large PRs reviewed by someone who'd never seen the files"
            )
    pdf.ln(5)

    # =========================================================================
    # Module Ownership
    # =========================================================================
    if data.module_data:
        pdf.chapter_title("Who's Actually Reviewing?")
        pdf.body_text("Are experts reviewing their areas?")

        # Sort by activity
        sorted_modules = sorted(
            data.module_data, key=lambda m: sum(r["prs"] for r in m["reviewers"]), reverse=True
        )[:10]

        for mod in sorted_modules:
            reviewers = mod["reviewers"]
            reviewer_parts = [f"{r['login']} {int(r['share'])}%" for r in reviewers[:2]]
            pdf.bullet(f"{mod['module']}: {', '.join(reviewer_parts)}")

    pdf.ln(5)

    # =========================================================================
    # Red Flags
    # =========================================================================
    if data.red_flags:
        pdf.chapter_title("Red Flags")
        pdf.body_text("PRs that might have slipped through:")

        columns = [("PR#", 20), ("Author", 35), ("Lines", 20), ("Time", 25), ("Context", 60)]
        widths = [c[1] for c in columns]
        pdf.table_header(columns)

        for flag in data.red_flags[:10]:
            pdf.table_row(
                [
                    f"#{flag['pr_number']}",
                    flag["author"],
                    f"{flag['lines']:,}",
                    format_minutes(flag["minutes"]),
                    flag["context"],
                ],
                widths,
            )

    # Save PDF
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output_path))
    return output_path
