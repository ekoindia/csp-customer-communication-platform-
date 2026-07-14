"""Fine-tune the synthetic-pretrained model on REAL line-cropped cells.
Mixes real + fresh synthetic (+ weight_decay + early stop) so it adapts without
overfitting. Usage: python finetune.py [epochs]  -> checkpoints/crnn_ft.pt"""
import csv
import os
import random
import sys

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
from PIL import Image

from synth import make_sample, _fit, BLANK, CHARS
from model import CRNN
from train import collate, evaluate

HERE = os.path.dirname(os.path.abspath(__file__))
CELLS = os.path.join(HERE, "real_cells")
CKPT = os.path.join(HERE, "checkpoints", "crnn.pt")
OUT = os.path.join(HERE, "checkpoints", "crnn_ft.pt")


def load_real():
    rows = list(csv.DictReader(open(os.path.join(CELLS, "labels.csv"), encoding="utf-8")))
    out = []
    for r in rows:
        lab = (r.get("label") or "").strip()
        if not lab:
            continue
        digits = "" if lab == "-" else lab
        if any(ch not in CHARS for ch in digits):
            continue
        p = os.path.join(CELLS, r["file"].replace("/", os.sep))
        if not os.path.exists(p):
            continue
        img = _fit(np.array(Image.open(p).convert("L"))).astype(np.float32) / 255.0
        out.append((img, [CHARS.index(c) for c in digits]))
    return out


class MixDataset(Dataset):
    def __init__(self, real, length):
        self.real = real; self.length = length

    def __len__(self):
        return self.length

    def __getitem__(self, i):
        rng = random.Random()
        if self.real and rng.random() < 0.6:
            img, lab = self.real[rng.randrange(len(self.real))]
        else:
            img, lab = make_sample(rng)
        return torch.from_numpy(np.ascontiguousarray(img)).unsqueeze(0), torch.tensor(lab, dtype=torch.long)


def main():
    epochs = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    device = "cuda" if torch.cuda.is_available() else "cpu"
    real = load_real()
    if len(real) < 20:
        print(f"Only {len(real)} labelled real cells — run label_line_cells.py first."); return
    random.Random(0).shuffle(real)
    n_val = max(10, len(real) // 5)
    val, train_real = real[:n_val], real[n_val:]
    print(f"real cells: {len(real)} (train {len(train_real)}, val {n_val}) | {device}")
    model = CRNN().to(device)
    model.load_state_dict(torch.load(CKPT, map_location=device)["model"])
    vx = torch.stack([torch.from_numpy(np.ascontiguousarray(i)).unsqueeze(0) for i, _ in val])
    vy = [l for _, l in val]
    base_acc, _ = evaluate(model, vx, vy, device)
    print(f"BEFORE: real whole-number acc={base_acc*100:.1f}%")
    ctc = nn.CTCLoss(blank=BLANK, zero_infinity=True)
    opt = torch.optim.Adam(model.parameters(), lr=3e-4, weight_decay=1e-4)
    loader = DataLoader(MixDataset(train_real, 128 * 60), batch_size=128,
                        num_workers=min(8, os.cpu_count() or 2), collate_fn=collate,
                        persistent_workers=True, drop_last=True)
    best = base_acc
    for ep in range(1, epochs + 1):
        model.train()
        for imgs, targets, tlens, _ in loader:
            logp = model(imgs.to(device)).log_softmax(2).permute(1, 0, 2)
            in_lens = torch.full((imgs.size(0),), logp.size(0), dtype=torch.long)
            loss = ctc(logp, targets, in_lens, tlens)
            opt.zero_grad(); loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.0); opt.step()
        acc, dig = evaluate(model, vx, vy, device)
        print(f"epoch {ep:2d} real whole={acc*100:.1f}% per-digit={dig*100:.2f}%")
        if acc >= best:
            best = acc
            torch.save({"model": model.state_dict(), "chars": CHARS}, OUT)
    print(f"done real {base_acc*100:.1f}% -> {best*100:.1f}% -> {OUT}")


if __name__ == "__main__":
    main()
