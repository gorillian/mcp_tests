from typing import Any
import os
import urllib.parse
from collections import Counter

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

# Initialize FastMCP server
mcp = FastMCP("bs-core")

# Constants
NWS_API_BASE = "https://api.weather.gov"
USER_AGENT = "weather-app/1.0"


async def make_nws_request(url: str) -> dict[str, Any] | None:
    """Make a request to the NWS API with proper error handling."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, timeout=30.0)
            response.raise_for_status()
            return response.json()
        except Exception:
            return None


def format_alert(feature: dict) -> str:
    """Format an alert feature into a readable string."""
    props = feature["properties"]
    return f"""
Event: {props.get("event", "Unknown")}
Area: {props.get("areaDesc", "Unknown")}
Severity: {props.get("severity", "Unknown")}
Description: {props.get("description", "No description available")}
Instructions: {props.get("instruction", "No specific instructions provided")}
"""

@mcp.tool()
async def get_alerts(state: str) -> str:
    """Get weather alerts for a US state.

    Args:
        state: Two-letter US state code (e.g. CA, NY)
    """
    url = f"{NWS_API_BASE}/alerts/active/area/{state}"
    data = await make_nws_request(url)

    if not data or "features" not in data:
        return "Unable to fetch alerts or no alerts found."

    if not data["features"]:
        return "No active alerts for this state."

    alerts = [format_alert(feature) for feature in data["features"]]
    return "\n---\n".join(alerts)


@mcp.tool()
async def get_forecast(latitude: float, longitude: float) -> str:
    """Get weather forecast for a location.

    Args:
        latitude: Latitude of the location
        longitude: Longitude of the location
    """
    # First get the forecast grid endpoint
    points_url = f"{NWS_API_BASE}/points/{latitude},{longitude}"
    points_data = await make_nws_request(points_url)

    if not points_data:
        return "Unable to fetch forecast data for this location."

    # Get the forecast URL from the points response
    forecast_url = points_data["properties"]["forecast"]
    forecast_data = await make_nws_request(forecast_url)

    if not forecast_data:
        return "Unable to fetch detailed forecast."

    # Format the periods into a readable forecast
    periods = forecast_data["properties"]["periods"]
    forecasts = []
    for period in periods[:5]:  # Only show next 5 periods
        forecast = f"""
{period["name"]}:
Temperature: {period["temperature"]}°{period["temperatureUnit"]}
Wind: {period["windSpeed"]} {period["windDirection"]}
Forecast: {period["detailedForecast"]}
"""
        forecasts.append(forecast)

    return "\n---\n".join(forecasts)

def _normalize_tag(player_tag: str) -> str:
    tag = player_tag.strip().upper()
    if not tag.startswith("#"):
        tag = f"#{tag}"
    return tag


@mcp.tool()
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

def main():
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()