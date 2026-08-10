"""Format and send evening recommendation summary emails via SMTP."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from app.config import settings
from app.services.budget_allocator import BudgetAllocationReport, is_profitable_allocation_line
from app.services.recommendation_engine import RecommendationReport

log = logging.getLogger(__name__)


def email_configured() -> bool:
    """True when SMTP secrets are present and sending is enabled."""
    if not settings.email_enabled:
        return False
    return bool(
        settings.smtp_host
        and settings.smtp_username
        and settings.smtp_password
        and (settings.email_to or settings.smtp_username)
        and (settings.email_from or settings.smtp_username)
    )


def _inr(value: float) -> str:
    return f"₹{value:,.2f}"


def build_recommendation_email(
    report: RecommendationReport,
    allocation: BudgetAllocationReport,
) -> tuple[str, str, str]:
    """Return (subject, text_body, html_body)."""
    predict = report.prediction_date.strftime("%d %b %Y")
    through = report.data_through_date.strftime("%d %b %Y")
    subject = f"NIFTY paper trades for {predict} — {len(allocation.lines)} picks"

    lines = [ln for ln in allocation.lines if is_profitable_allocation_line(ln)]
    if not lines:
        lines = list(allocation.lines)

    text_rows: list[str] = []
    html_rows: list[str] = []
    for i, line in enumerate(lines, start=1):
        text_rows.append(
            f"{i}. {line.symbol} ({line.cap_tier}) · {line.shares} sh @ {_inr(line.buy_price)} · "
            f"SL {_inr(line.stop_loss)} · tgt {_inr(line.model_target_price)} · "
            f"net {_inr(line.net_profit_after_tax)} · {line.pattern_name}"
        )
        html_rows.append(
            "<tr>"
            f"<td>{i}</td>"
            f"<td><b>{escape(line.symbol)}</b></td>"
            f"<td>{escape(line.cap_tier)}</td>"
            f"<td>{line.shares}</td>"
            f"<td>{escape(_inr(line.buy_price))}</td>"
            f"<td>{escape(_inr(line.stop_loss))}</td>"
            f"<td>{escape(_inr(line.model_target_price))}</td>"
            f"<td>{escape(_inr(line.net_profit_after_tax))}</td>"
            f"<td>{escape(line.pattern_name)}</td>"
            "</tr>"
        )

    if not lines:
        picks_text = "No allocation lines — re-run analysis or check market data."
        picks_html = f"<p>{escape(picks_text)}</p>"
    else:
        picks_text = "\n".join(text_rows)
        picks_html = (
            "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse'>"
            "<thead><tr>"
            "<th>#</th><th>Symbol</th><th>Tier</th><th>Shares</th>"
            "<th>Buy</th><th>Stop</th><th>Target</th><th>Net P/L</th><th>Pattern</th>"
            "</tr></thead>"
            f"<tbody>{''.join(html_rows)}</tbody></table>"
        )

    text = (
        f"NIFTY Paper Trading — plan for {predict}\n"
        f"Data through: {through}\n"
        f"Budget: {_inr(allocation.budget_inr)} · Invested: {_inr(allocation.total_invested)} · "
        f"Cash left: {_inr(allocation.cash_remaining)}\n"
        f"Expected net after tax: {_inr(allocation.total_net_profit_after_tax)} "
        f"({allocation.expected_return_pct:.1f}%)\n"
        f"Max portfolio loss (stops): {_inr(allocation.max_portfolio_loss)}\n\n"
        f"Picks:\n{picks_text}\n\n"
        "Paper trading only — not investment advice.\n"
    )

    html = f"""\
<html><body style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#222">
  <h2>NIFTY Paper Trading — {escape(predict)}</h2>
  <p>Data through <b>{escape(through)}</b>. Trade plan for the <b>next session</b>.</p>
  <ul>
    <li>Budget: <b>{escape(_inr(allocation.budget_inr))}</b></li>
    <li>Invested: <b>{escape(_inr(allocation.total_invested))}</b>
        (cash left {escape(_inr(allocation.cash_remaining))})</li>
    <li>Expected net after tax:
        <b>{escape(_inr(allocation.total_net_profit_after_tax))}</b>
        ({allocation.expected_return_pct:.1f}%)</li>
    <li>Max loss at stops: <b>{escape(_inr(allocation.max_portfolio_loss))}</b></li>
  </ul>
  <h3>Allocation picks</h3>
  {picks_html}
  <p style="color:#666;font-size:12px;margin-top:24px">
    Paper trading only — not investment advice.
  </p>
</body></html>
"""
    return subject, text, html


def send_recommendation_email(
    report: RecommendationReport,
    allocation: BudgetAllocationReport,
) -> bool:
    """
    Send the evening summary. Returns True if sent, False if skipped (not configured).

    Raises on SMTP failure so callers can surface errors in CI logs.
    """
    if not email_configured():
        log.info("Recommendation email skipped — SMTP not configured")
        return False

    subject, text_body, html_body = build_recommendation_email(report, allocation)
    from_addr = settings.email_from or settings.smtp_username or ""
    to_addr = settings.email_to or settings.smtp_username or ""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    log.info("Sending recommendation email to %s via %s", to_addr, settings.smtp_host)
    context = ssl.create_default_context()
    with smtplib.SMTP(settings.smtp_host or "", settings.smtp_port, timeout=60) as server:
        if settings.smtp_use_tls:
            server.starttls(context=context)
        server.login(settings.smtp_username or "", settings.smtp_password or "")
        server.sendmail(from_addr, [addr.strip() for addr in to_addr.split(",") if addr.strip()], msg.as_string())
    log.info("Recommendation email sent")
    return True
