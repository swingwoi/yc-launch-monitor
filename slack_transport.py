"""Slack Bot transport for YC Launch Monitor alerts."""

import os
import time
import logging
from datetime import datetime
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")
MAX_RETRIES = 3
RETRY_DELAY = 2

SOURCE_LABELS = {
    "x": "X (Twitter)",
    "linkedin": "LinkedIn",
    "yc_directory": "YC Directory",
    "yc_speedrun": "YC Speedrun",
}


def _fmt_time(ts: str) -> str:
    """Parse ISO timestamp to readable string."""
    try:
        return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError):
        return str(ts) if ts else datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")


def format_alert(alert: dict) -> list:
    """Format alert dict into Slack Block Kit JSON blocks.

    Args:
        alert: dict with keys: alert_type, company_name, source, batch,
               description, url, detected_at, founder_name, founder_handle,
               original_text
    Returns:
        List of Slack Block Kit blocks.
    """
    atype = alert.get("alert_type", "")
    company = alert.get("company_name", "Unknown Company")
    source = alert.get("source", "unknown")
    batch = alert.get("batch", "N/A")
    description = alert.get("description", "")
    url = alert.get("url", "")
    founder_name = alert.get("founder_name", "")
    founder_handle = alert.get("founder_handle", "")
    original_text = alert.get("original_text", "")

    if atype == "early_founder":
        emoji, title, status = "\U0001f525", "Early Founder Detected", "Pre-launch signal"
    elif atype == "new_yc_company":
        emoji, title, status = "\u2705", "New YC Company Confirmed", "Confirmed in YC directory"
    else:
        emoji, title, status = "\U0001f6a8", "YC Monitor Alert", "Unknown"

    fields = [
        {"type": "mrkdwn", "text": f"*Company:*\n{company}"},
        {"type": "mrkdwn", "text": f"*Batch:*\n{batch}"},
        {"type": "mrkdwn", "text": f"*Source:*\n{SOURCE_LABELS.get(source, source)}"},
        {"type": "mrkdwn", "text": f"*Status:*\n{status}"},
    ]
    if founder_name or founder_handle:
        ftxt = founder_name
        if founder_handle:
            ftxt += f" ({founder_handle})"
        fields.append({"type": "mrkdwn", "text": f"*Founder:*\n{ftxt}"})

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} {title}", "emoji": True}},
        {"type": "divider"},
        {"type": "section", "fields": fields},
    ]

    if description:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Description:*\n{description[:2000]}"}})

    if original_text:
        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"*Original text:* _{original_text[:500]}_"}
        ]})

    if url:
        aid = f"view_{company.replace(' ', '_').lower()}"
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "\U0001f517 View Source", "emoji": True},
             "url": url, "action_id": aid}
        ]})

    detected_str = _fmt_time(alert.get("detected_at", ""))
    blocks.append({"type": "divider"})
    blocks.append({"type": "context", "elements": [
        {"type": "mrkdwn", "text": f"\U0001f550 Detected at {detected_str} | *YC Launch Monitor*"}
    ]})
    return blocks


def send_alert(alert: dict, channel=None) -> dict:
    """Send an alert to Slack with exponential-backoff retries.

    Args:
        alert: Alert dict (same schema as format_alert).
        channel: Override channel ID. Defaults to SLACK_CHANNEL_ID env.
    Returns:
        Slack API response dict on success.
    Raises:
        SlackApiError: After all retries exhausted.
    """
    if not SLACK_BOT_TOKEN:
        raise SlackApiError(message="SLACK_BOT_TOKEN not set", response={"ok": False, "error": "missing_token"})

    target = channel or SLACK_CHANNEL_ID
    if not target:
        raise SlackApiError(message="SLACK_CHANNEL_ID not set", response={"ok": False, "error": "missing_channel"})

    client = WebClient(token=SLACK_BOT_TOKEN)
    blocks = format_alert(alert)
    last_error: SlackApiError | None = None
    delay = RETRY_DELAY

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.chat_postMessage(
                channel=target, blocks=blocks,
                text=f"YC Alert: {alert.get('company_name', 'Unknown')}",
                unfurl_links=False,
            )
            logger.info("Alert sent to %s (attempt %d): %s", target, attempt, alert.get("company_name"))
            return resp.data
        except SlackApiError as exc:
            last_error = exc
            logger.warning("Slack send failed (attempt %d/%d): %s", attempt, MAX_RETRIES,
                           exc.response.get("error", str(exc)))
            if attempt < MAX_RETRIES:
                time.sleep(delay)
                delay *= 2

    assert last_error is not None
    raise last_error
