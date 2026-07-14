"""Train the CRNN+CTC digit recogniser on synthetic phone-scan crops.
Usage: python train.py [epochs] [steps_per_epoch] [batch]  -> checkpoints/crnn.pt
Reports WHOLE-NUMBER accuracy (one wrong digit = wrong number)."""
import os
import random
import sys

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

from synth import make_sample, BLANK, CHARS, IMG_H, IMG_W
from model import CRNN, greedy_decode

HERE = os.path.dirname(os.path.abspath(__file__))
CKPT_DIR = os.path.join(HERE, "checkpoints")
os.makedirs(CKPT_DIR, exist_ok=True)


class SynthDataset(Dataset):
    def __init__(self, n):
        self.n = n

    def __len__(self):
        return self.n

    def __getitem__(self, i):
        x, label = make_sample(random.Random())
        return torch.from_numpy(x).unsqueeze(0), torch.tensor(label, dtype=torch.long)


def collate(batch):
    imgs = torch.stack([b[0] for b in batch])
    labels = [b[1] for b in batch]
    targets = torch.cat(labels)
    target_lengths = torch.tensor([len(l) for l in labels], dtype=torch.long)
    return imgs, targets, target_lengths, labels


def build_val(n=2000, seed=1234):
    rng = random.Random(seed)
    np.random.seed(seed)
    xs, ys = [], []
    for _ in range(n):
        x, label = make_sample(rng)
        xs.append(torch.from_numpy(x).unsqueeze(0)); ys.append(label)
    return torch.stack(xs), ys


@torch.no_grad()
def evaluate(model, val_x, val_y, device, batch=256):
    model.eval()
    exact = dig_ok = dig_tot = 0
    for i in range(0, len(val_x), batch):
        logits = model(val_x[i:i + batch].to(device))
        for p, gt in zip(greedy_decode(logits.cpu(), BLANK), val_y[i:i + batch]):
            if p == gt:
                exact += 1
            for a, b in zip(p, gt):
                dig_tot += 1; dig_ok += int(a == b)
            dig_tot += abs(len(p) - len(gt))
    return exact / len(val_x), dig_ok / max(1, dig_tot)


def main():
    epochs = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    steps = int(sys.argv[2]) if len(sys.argv) > 2 else 350
    batch = int(sys.argv[3]) if len(sys.argv) > 3 else 128
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = CRNN().to(device)
    print(f"device={device} params={sum(p.numel() for p in model.parameters())/1e6:.2f}M")
    ctc = nn.CTCLoss(blank=BLANK, zero_infinity=True)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    sched = torch.optim.lr_scheduler.StepLR(opt, step_size=max(1, epochs // 2), gamma=0.3)
    workers = min(8, os.cpu_count() or 2)
    loader = DataLoader(SynthDataset(steps * batch), batch_size=batch, num_workers=workers,
                        collate_fn=collate, persistent_workers=(workers > 0), drop_last=True)
    val_x, val_y = build_val()
    best = 0.0
    for ep in range(1, epochs + 1):
        model.train()
        run = 0.0
        for imgs, targets, tlens, _ in loader:
            logp = model(imgs.to(device)).log_softmax(2).permute(1, 0, 2)
            in_lens = torch.full((imgs.size(0),), logp.size(0), dtype=torch.long)
            loss = ctc(logp, targets, in_lens, tlens)
            opt.zero_grad(); loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.0); opt.step()
            run += loss.item()
        sched.step()
        acc, dacc = evaluate(model, val_x, val_y, device)
        print(f"epoch {ep:2d} loss={run/steps:.3f} whole={acc*100:.1f}% per-digit={dacc*100:.2f}%")
        if acc >= best:
            best = acc
            torch.save({"model": model.state_dict(), "chars": CHARS,
                        "img_h": IMG_H, "img_w": IMG_W, "blank": BLANK},
                       os.path.join(CKPT_DIR, "crnn.pt"))
    print(f"done best={best*100:.1f}% -> checkpoints/crnn.pt")


if __name__ == "__main__":
    main()
