import json
import os
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

API_BASE = "https://api.brawlstars.com/v1"
MY_PLAYER_TAG = "#QLYP829J"
WINRATES_DIR = Path.home() / "Desktop" / "General" / "brawlstars"
WINRATES_MD = WINRATES_DIR / "my-winrates.md"
WINRATES_STATE = WINRATES_DIR / ".winrates-state.json"


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


def _player_brawler(battle: dict, tag: str) -> str | None:
    groups = list(battle.get("teams") or [])
    if battle.get("players"):
        groups.append(battle["players"])
    for group in groups:
        for player in group or []:
            if (player.get("tag") or "").upper() == tag:
                return (player.get("brawler") or {}).get("name")
    return None


def _parse_battles(items: list, tag: str) -> list[tuple[str, str, bool]]:
    battles: list[tuple[str, str, bool]] = []
    for item in items:
        battle = item.get("battle") or {}
        result = battle.get("result")
        if result not in {"victory", "defeat"}:
            continue
        brawler = _player_brawler(battle, tag)
        if not brawler:
            continue
        key = item.get("battleTime") or f"{brawler}|{result}|{len(battles)}"
        battles.append((str(key), brawler, result == "victory"))
    return battles


def _count_brawlers(battles: list[tuple[str, str, bool]]) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = {}
    for _, brawler, won in battles:
        stats = totals.setdefault(brawler, {"games": 0, "wins": 0})
        stats["games"] += 1
        if won:
            stats["wins"] += 1
    return totals


def _rate_lines(brawlers: dict[str, Any]) -> list[str]:
    ranked = sorted(
        brawlers.items(),
        key=lambda item: (-int(item[1].get("games") or 0), item[0]),
    )
    lines: list[str] = []
    for name, stats in ranked:
        games = int(stats.get("games") or 0)
        wins = int(stats.get("wins") or 0)
        if games:
            lines.append(f"- {name.title()}: {wins}/{games} ({100.0 * wins / games:.0f}%)")
    return lines


def _load_my_stats() -> dict[str, Any]:
    empty = {"brawlers": {}, "seen": []}
    if not WINRATES_STATE.exists():
        return empty
    try:
        data = json.loads(WINRATES_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    if "brawlers" in data:
        return data
    mine = (data.get("players") or {}).get(MY_PLAYER_TAG)
    return mine if isinstance(mine, dict) else empty


def _save_my_stats(stats: dict[str, Any]) -> None:
    WINRATES_DIR.mkdir(parents=True, exist_ok=True)
    WINRATES_STATE.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    brawlers = stats.get("brawlers") or {}
    rows = []
    total_games = total_wins = 0
    for name, entry in sorted(brawlers.items(), key=lambda item: (-int(item[1].get("games") or 0), item[0])):
        games = int(entry.get("games") or 0)
        wins = int(entry.get("wins") or 0)
        if not games:
            continue
        total_games += games
        total_wins += wins
        rows.append(f"| {name.title()} | {games} | {wins} | {100.0 * wins / games:.0f}% |")

    now = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    overall = f"{100.0 * total_wins / total_games:.0f}%" if total_games else "—"
    table = (
        "\n".join(
            [
                "| Brawler | Games | Wins | Win Rate |",
                "|:--------|------:|-----:|---------:|",
                *rows,
            ]
        )
        if rows
        else "_No battles tracked yet._"
    )
    WINRATES_MD.write_text(
        "\n".join(
            [
                "# My Brawl Stars Win Rates",
                "",
                f"**Player:** {MY_PLAYER_TAG}  ",
                f"**Updated:** {now}  ",
                f"**Battles:** {total_games} ({total_wins} wins, {overall})",
                "",
                table,
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


async def get_winrates(player_tag: str) -> str:
    """Get win rates by brawler. Only #QLYP829J is saved to my-winrates.md.

    Args:
        player_tag: Brawl Stars player tag, e.g. #QLYP829J
    """
    key = os.environ.get("BRAWLSTARS_API_KEY")
    if not key:
        return "BRAWLSTARS_API_KEY is not set. Add it to .env or mcp.json env."

    tag = _normalize_tag(player_tag)
    encoded = urllib.parse.quote(tag)
    url = f"{API_BASE}/players/{encoded}/battlelog"
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            return f"Failed to fetch battle log for {tag}: {exc}"

    battles = _parse_battles(data.get("items", []), tag)
    if tag != MY_PLAYER_TAG:
        if not battles:
            return f"No recent decided team battles found for {tag}."
        return (
            f"Recent win rates for {tag} ({len(battles)} decided battles). "
            f"Not saved (only {MY_PLAYER_TAG} is tracked).\n\n"
            + "\n".join(_rate_lines(_count_brawlers(battles)))
        )

    stats = _load_my_stats()
    seen = set(stats.get("seen") or [])
    brawlers: dict[str, Any] = stats.setdefault("brawlers", {})
    added = 0
    for key, brawler, won in battles:
        if key in seen:
            continue
        seen.add(key)
        entry = brawlers.setdefault(brawler, {"games": 0, "wins": 0})
        entry["games"] = int(entry.get("games") or 0) + 1
        if won:
            entry["wins"] = int(entry.get("wins") or 0) + 1
        added += 1
    stats["seen"] = sorted(seen)

    try:
        _save_my_stats(stats)
    except OSError as exc:
        return f"Fetched win rates for {tag}, but failed to save {WINRATES_MD}: {exc}"

    if not brawlers:
        return f"No recent decided team battles found for {tag}."

    total = sum(int(entry.get("games") or 0) for entry in brawlers.values())
    return (
        f"Updated ~/Desktop/General/brawlstars/my-winrates.md\n"
        f"Added {added} new battle{'s' if added != 1 else ''} ({total} total tracked).\n\n"
        + "\n".join(_rate_lines(brawlers))
    )


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
