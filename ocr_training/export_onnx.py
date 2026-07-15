"""Export the fine-tuned CRNN checkpoint to the deployed ONNX model.
Backs up the current crnn.onnx first. Input/output names match core/ocr_onnx.py
('image' -> 'logits'); CPU-only, tiny — runs on the 4 GB CSP box via onnxruntime.
Usage: python export_onnx.py [checkpoints/crnn_ft.pt]"""
import os
import shutil
import sys

import torch

from model import CRNN

HERE = os.path.dirname(os.path.abspath(__file__))
CKPT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "checkpoints", "crnn_ft.pt")
OUT = os.path.join(os.path.dirname(HERE), "csp_dashboard", "core", "models", "crnn.onnx")


def main():
    if os.path.exists(OUT):
        bak = OUT + ".bak"
        shutil.copyfile(OUT, bak)
        print(f"backed up current model -> {bak}")
    model = CRNN()
    model.load_state_dict(torch.load(CKPT, map_location="cpu")["model"])
    model.eval()
    dummy = torch.zeros(1, 1, 32, 192)
    torch.onnx.export(
        model, dummy, OUT,
        input_names=["image"], output_names=["logits"],
        opset_version=13, dynamo=False,
    )
    sz = os.path.getsize(OUT) / 1024
    print(f"exported {CKPT} -> {OUT}  ({sz:.0f} KB)")


if __name__ == "__main__":
    main()
