"""Loaders for the two local speech models that TRACER runs from files on disk: Parakeet (ONNX) and Qwen3-ASR (PyTorch).
Whisper is loaded by the openai-whisper package itself (asr_bench_whisper_pt.py).

Model files are not part of this repo. Download them once with `python scripts/get_models.py`. They are looked for in
OLC_MODELS_DIR if set, otherwise <project>/models.
Model licences: Parakeet TDT 0.6B v3, NVIDIA, CC-BY-4.0; Qwen3-ASR-1.7B, Qwen team (Alibaba), Apache-2.0.
"""
import os
import pathlib
import subprocess

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLE_RATE = 16000

# name -> (onnx-asr model type, Hugging Face repo, folder name)
MODELS = {
    "parakeet": ("nemo-parakeet-tdt-0.6b-v3", "istupakov/parakeet-tdt-0.6b-v3-onnx", "parakeet-tdt-0.6b-v3"),
    "qwen": (None, "Qwen/Qwen3-ASR-1.7B", "qwen3-asr-1.7b"),
}


def models_dir():
    if os.environ.get("OLC_MODELS_DIR"):
        return pathlib.Path(os.environ["OLC_MODELS_DIR"])
    return ROOT / "models"


def model_path(name):
    p = models_dir() / MODELS[name][2]
    if not p.exists():
        raise SystemExit(f"Speech model '{name}' not found at {p}. Run: python scripts/get_models.py")
    return p


def load_onnx_model(name, use_cpu):
    """Parakeet through onnx-asr. Uses the GPU when onnxruntime-gpu can see one. Long recordings are split on silence."""
    import onnx_asr
    import onnxruntime as ort

    ort.set_default_logger_severity(3)  # errors only
    providers = ["CPUExecutionProvider"]
    if not use_cpu and "CUDAExecutionProvider" in ort.get_available_providers():
        ort.preload_dlls()  # find the CUDA/cuDNN DLLs installed by pip
        providers.insert(0, "CUDAExecutionProvider")
    # a plain local folder: onnxruntime rejects the symlinked Hugging Face cache layout
    model = onnx_asr.load_model(MODELS[name][0], model_path(name), providers=providers)
    model = model.with_vad(onnx_asr.load_vad("silero", providers=providers))
    return lambda audio: " ".join(seg.text.strip() for seg in model.recognize(audio, sample_rate=SAMPLE_RATE)).strip()


def load_qwen_model(use_cpu):
    import torch
    from qwen_asr import Qwen3ASRModel

    gpu = not use_cpu and torch.cuda.is_available()
    model = Qwen3ASRModel.from_pretrained(str(model_path("qwen")), dtype=torch.bfloat16 if gpu else torch.float32,
                                          device_map="cuda:0" if gpu else "cpu", max_new_tokens=4096)
    return lambda audio: model.transcribe(audio=(audio, SAMPLE_RATE), language=None)[0].text.strip()


def decode_audio(path):
    """Decode any audio or video file to 16 kHz mono float32 with ffmpeg."""
    out = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path), "-f", "f32le", "-ac", "1",
                          "-ar", str(SAMPLE_RATE), "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.float32)
