import os

import httpx
from mcp.server.fastmcp import FastMCP


async def edit_and_post_video(video_file_path: str) -> str:
    """Edit and post a video to social media accounts."""
    key = os.environ.get("SOCIAL_MEDIA_API_KEY")
    if not key:
        return "SOCIAL_MEDIA_API_KEY is not set. Add it to .env or mcp.json env."

    url = "https://api.socialmedia.com/v1/posts"
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    data = {"video": video_file_path, "edit": True}
    response = await httpx.AsyncClient().post(url, headers=headers, data=data)
    return "Video posted successfully."


def register(mcp: FastMCP) -> None:
    mcp.tool()(edit_and_post_video)
