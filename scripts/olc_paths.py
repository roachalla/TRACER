"""Where the pipeline finds its Python environments: the environment variable if set, otherwise <project>/envs/<name>.
See README, Setup, for how to create each one. Used by run_video.py and olc_gui.py."""
import os, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
_EXE = "Scripts/python.exe" if os.name == "nt" else "bin/python"

# name -> (environment variable that overrides the location, what the environment holds)
ENVS = {
    "whisper": ("OLC_PY_CV", "openai-whisper, PyTorch (CUDA), OpenCV"),
    "parakeet": ("OLC_PY_PARA", "onnx-asr, onnxruntime-gpu"),
    "qwen": ("OLC_PY_QWEN", "qwen-asr, PyTorch (CUDA), transformers"),
    "speaker": ("OLC_PY_SPK", "speechbrain, PyTorch (CPU), soundfile"),
}


def env_python(name):
    var = ENVS[name][0]
    if os.environ.get(var):
        return pathlib.Path(os.environ[var])
    return ROOT / "envs" / name / _EXE
