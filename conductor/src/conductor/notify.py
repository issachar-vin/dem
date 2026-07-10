"""Best-effort operator notifications. Dispatched by the `notify_mode` config (ntfy / slack /
webhook / none). Notifications never gate the pipeline: a delivery failure is logged and swallowed,
never raised, so a down notifier can't fail a ticket. The full observability wiring is Phase 6; this
is the minimal sender Phase 5's loop needs to announce `ready_for_approval` / `stalled`."""

import logging

import httpx

logger = logging.getLogger("conductor")


async def notify(cfg: dict[str, str], message: str) -> None:
    mode = cfg.get("notify_mode", "none")
    url = {
        "ntfy": cfg.get("notify_ntfy_url"),
        "slack": cfg.get("notify_slack_webhook_url"),
        "webhook": cfg.get("notify_webhook_url"),
    }.get(mode)
    if mode == "none" or not url:
        logger.info("notify[%s]: %s", mode, message)
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if mode == "ntfy":
                await client.post(url, content=message.encode())
            else:  # slack + generic webhook both accept a JSON {"text": …} body
                await client.post(url, json={"text": message})
    except httpx.HTTPError as exc:
        logger.warning("notify delivery via %s failed: %s", mode, exc)
