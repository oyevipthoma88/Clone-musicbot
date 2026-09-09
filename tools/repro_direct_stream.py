import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from melody.core import ytdl


VIDEO_IDS = ["iAIBF2ngbWY", "dGwau9Vcc0o", "gUg0gmEcCqc"]


async def main() -> None:
    for video_id in VIDEO_IDS:
        print(f"\n=== {video_id} ===")
        try:
            raw = await asyncio.get_running_loop().run_in_executor(
                ytdl.YTDL_POOL, ytdl._innertube_streams_sync, video_id
            )
            print("InnerTube:", {
                "client": (raw or {}).get("client"),
                "formats": len((raw or {}).get("formats") or []),
                "hls": bool((raw or {}).get("hlsManifestUrl")),
            })
        except Exception as exc:
            print("InnerTube ERROR:", type(exc).__name__, exc)
        for want_video in (False, True):
            try:
                result = await asyncio.wait_for(
                    ytdl.resolve_stream_urls(video_id, want_video=want_video, force=True),
                    timeout=30,
                )
                print("resolve", "video" if want_video else "audio", {
                    "video": bool(result.get("video")),
                    "audio": bool(result.get("audio")),
                    "client": result.get("client"),
                    "expires_at": result.get("expires_at"),
                })
            except Exception as exc:
                print("resolve", "video" if want_video else "audio", "ERROR:", type(exc).__name__, exc)


if __name__ == "__main__":
    asyncio.run(main())
