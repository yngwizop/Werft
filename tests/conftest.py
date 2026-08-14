import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="werft-test-"))
os.environ["WERFT_MASTER_KEY_PATH"] = str(_tmp / "master.key")
os.environ["WERFT_ENV_IMPORT"] = str(_tmp / "no-such.env")
