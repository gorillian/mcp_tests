from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

import brawl_stars
import social_media
import weather

load_dotenv()

mcp = FastMCP("bs-core")

weather.register(mcp)
brawl_stars.register(mcp)
social_media.register(mcp)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
