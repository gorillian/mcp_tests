import os
import re
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

API_BASE = "https://api.brawlstars.com/v1"


def _normalize_tag(player_tag: str) -> str:
    tag = player_tag.strip().upper()
    if not tag.startswith("#"):
        tag = f"#{tag}"
    return tag


def _mode_key(mode: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (mode or "").lower())


def _pretty_mode(mode: str) -> str:
    labels = {
        "knockout": "Knockout",
        "knockout5v5": "Knockout 5v5",
        "heist": "Heist",
        "hotzone": "Hot Zone",
    }
    key = _mode_key(mode)
    if key in labels:
        return labels[key]
    spaced = re.sub(r"([a-z])([A-Z0-9])", r"\1 \2", mode or "")
    return spaced[:1].upper() + spaced[1:] if spaced else "Unknown"


def _event_location(item: dict) -> dict:
    return item.get("event") or {}


def _event_mode(item: dict) -> str:
    return _event_location(item).get("mode") or ""


def _event_map(item: dict) -> str:
    name = _event_location(item).get("map")
    if isinstance(name, dict):
        return name.get("name") or name.get("en") or "Unknown map"
    return name or "Unknown map"


def _parse_api_time(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in (
        "%Y%m%dT%H%M%S.%fZ",
        "%Y%m%dT%H%M%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
    ):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _format_remaining(end: datetime) -> str:
    total = int((end - datetime.now(timezone.utc)).total_seconds())
    if total <= 0:
        return "already ended"
    hours, rem = divmod(total, 3600)
    minutes, _ = divmod(rem, 60)
    if hours:
        return f"in {hours}h {minutes}m"
    return f"in {minutes}m"


def _is_active(item: dict, now: datetime) -> bool:
    start = _parse_api_time(item.get("startTime"))
    end = _parse_api_time(item.get("endTime"))
    if start and now < start:
        return False
    if end and now >= end:
        return False
    return True


async def _fetch_event_rotation() -> tuple[list[dict] | None, str | None]:
    key = os.environ.get("BRAWLSTARS_API_KEY")
    if not key:
        return None, "BRAWLSTARS_API_KEY is not set. Add it to .env or mcp.json env."

    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(f"{API_BASE}/events/rotation", headers=headers)
            response.raise_for_status()
            data: Any = response.json()
        except Exception as exc:
            return None, f"Failed to fetch event rotation: {exc}"

    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            return items, None
    return None, "Unexpected event rotation response."


async def get_winrates(player_tag: str) -> str:
    """Get recent win rates by brawler from a player's battle log.

    Args:
        player_tag: Brawl Stars player tag, e.g. #QLYP829J
    """
    key = os.environ.get("BRAWLSTARS_API_KEY")
    if not key:
        return "BRAWLSTARS_API_KEY is not set. Add it to .env or mcp.json env."

    tag = _normalize_tag(player_tag)
    encoded = urllib.parse.quote(tag)
    url = f"https://api.brawlstars.com/v1/players/{encoded}/battlelog"
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            return f"Failed to fetch battle log for {tag}: {exc}"

    wins: Counter[str] = Counter()
    games: Counter[str] = Counter()
    decided = 0

    for item in data.get("items", []):
        battle = item.get("battle") or {}
        result = battle.get("result")  # victory | defeat | draw (team modes)
        if result not in {"victory", "defeat"}:
            continue

        my_brawler = None
        for team in battle.get("teams") or []:
            for player in team:
                if (player.get("tag") or "").upper() == tag:
                    my_brawler = (player.get("brawler") or {}).get("name")
                    break
            if my_brawler:
                break

        if not my_brawler and battle.get("players"):
            for player in battle["players"]:
                if (player.get("tag") or "").upper() == tag:
                    my_brawler = (player.get("brawler") or {}).get("name")
                    break

        if not my_brawler:
            continue

        decided += 1
        games[my_brawler] += 1
        if result == "victory":
            wins[my_brawler] += 1

    if not games:
        return f"No recent decided team battles found for {tag}."

    lines = [f"Recent win rates for {tag} ({decided} decided battles):"]
    for brawler, count in games.most_common():
        rate = 100.0 * wins[brawler] / count
        lines.append(f"- {brawler}: {wins[brawler]}/{count} ({rate:.0f}%)")
    return "\n".join(lines)


async def get_knockout_heist_hotzone() -> str:
    """Get current Knockout map(s) and whether Heist or Hot Zone is in rotation.

    Reports active Knockout maps and, separately, whether the current rotation
    has Heist or Hot Zone (and which map).
    """
    events, error = await _fetch_event_rotation()
    if error:
        return error

    now = datetime.now(timezone.utc)
    active = [item for item in events if _is_active(item, now)]
    if not active:
        return "No active events found in the current rotation."

    knockouts = [
        item
        for item in active
        if _mode_key(_event_mode(item)).startswith("knockout")
    ]
    heists = [item for item in active if _mode_key(_event_mode(item)) == "heist"]
    hotzones = [item for item in active if _mode_key(_event_mode(item)) == "hotzone"]

    lines = ["Knockout:"]
    if knockouts:
        for item in knockouts:
            mode = _pretty_mode(_event_mode(item))
            lines.append(f"- {_event_map(item)} ({mode})")
    else:
        lines.append("- No Knockout maps are currently up.")

    lines.append("")
    lines.append("Heist / Hot Zone:")
    if heists or hotzones:
        for item in heists + hotzones:
            lines.append(f"- {_pretty_mode(_event_mode(item))} on {_event_map(item)}")
    else:
        lines.append("- Neither Heist nor Hot Zone is currently in rotation.")

    return "\n".join(lines)


async def get_next_expiring_map() -> str:
    """Get the map in the current event rotation that expires soonest."""
    events, error = await _fetch_event_rotation()
    if error:
        return error

    now = datetime.now(timezone.utc)
    timed: list[tuple[datetime, dict]] = []
    for item in events:
        end = _parse_api_time(item.get("endTime"))
        if end and end > now:
            timed.append((end, item))

    if not timed:
        return "No upcoming map expirations found in the current rotation."

    soonest = min(end for end, _ in timed)
    expiring = [item for end, item in timed if end == soonest]
    when = soonest.strftime("%Y-%m-%d %H:%M UTC")
    remaining = _format_remaining(soonest)

    if len(expiring) == 1:
        item = expiring[0]
        return (
            f"Next map to expire: {_event_map(item)} "
            f"({_pretty_mode(_event_mode(item))})\n"
            f"Ends: {when} ({remaining})"
        )

    lines = [f"Next maps to expire ({when}, {remaining}):"]
    for item in expiring:
        lines.append(
            f"- {_event_map(item)} ({_pretty_mode(_event_mode(item))})"
        )
    return "\n".join(lines)


def register(mcp: FastMCP) -> None:
    mcp.tool()(get_winrates)
    mcp.tool()(get_knockout_heist_hotzone)
    mcp.tool()(get_next_expiring_map)
