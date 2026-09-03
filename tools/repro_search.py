import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from melody.core import ytdl


QUERIES = [
    "Pehle Bhi Main Full Video Ranbir Kapoor",
    "mujhe mera mod do",
    "mujhe mere yaar mod do",
    "jane kyun log mohabbat kiya karte hain",
]


async def main() -> None:
    for query in QUERIES:
        try:
            result = await asyncio.wait_for(ytdl._get_video_info_once(query), 25)
            print(f"{query!r} => {result!r}")
        except Exception as exc:  # diagnostics only
            print(f"{query!r} => {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
