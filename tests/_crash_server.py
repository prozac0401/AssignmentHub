"""Test-only real TCP server; process-exit hooks never enter production routing."""
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn

from assignmenthub.api import create_app
from assignmenthub.config import Config
from assignmenthub.launcher import StorageLock

config = Config.load(sys.argv[1])
port = int(sys.argv[2])
crash_at = sys.argv[3] if len(sys.argv) > 3 else ""

with StorageLock(config.root):
    app = create_app(config)
    if crash_at:
        def hard_exit(point):
            if point == crash_at:
                os._exit(73)
        app.state.service.fault_hook = hard_exit
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="error")
