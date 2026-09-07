"""Private Uvicorn process with a supervisor-requested graceful shutdown."""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

import uvicorn


async def serve(port: int):
    server = uvicorn.Server(uvicorn.Config("assignmenthub.api:app", host="127.0.0.1", port=port,
                                           access_log=False, proxy_headers=True,
                                           forwarded_allow_ips="127.0.0.1", timeout_graceful_shutdown=12))
    stop_file = Path(os.environ["AH_STOP_FILE"])

    async def watch():
        while not server.should_exit:
            if stop_file.exists():
                server.should_exit = True
                return
            await asyncio.sleep(0.2)

    watcher = asyncio.create_task(watch())
    try:
        await server.serve()
    finally:
        watcher.cancel()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("service", choices=["api"])
    parser.add_argument("--port", required=True, type=int)
    arguments = parser.parse_args()
    asyncio.run(serve(arguments.port))


if __name__ == "__main__":
    main()
