"""Download the Parakeet and Qwen3-ASR model files into <project>/models (or OLC_MODELS_DIR). About 6 GB; run once.
Whisper downloads its own model the first time it runs. Run with any Python that has huggingface_hub
(for example the parakeet environment): python scripts/get_models.py"""
import os
import pathlib
import sys

from huggingface_hub import snapshot_download

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from asr_models import MODELS  # noqa: E402  (asr_models imports only numpy at load time)

dest = pathlib.Path(os.environ.get("OLC_MODELS_DIR") or ROOT / "models")
for name, (_, repo, folder) in MODELS.items():
    print(f"{name}: {repo} -> {dest / folder}")
    snapshot_download(repo, local_dir=dest / folder)  # plain files, not cache symlinks (onnxruntime needs real files)
print("done")
