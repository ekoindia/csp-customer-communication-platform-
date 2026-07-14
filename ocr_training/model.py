"""
Compact CRNN + CTC digit-sequence recogniser (~0.37 M params).
Small enough to run fast on a CPU-only 4 GB i3 after ONNX export.
"""
import torch
import torch.nn as nn

from synth import NUM_CLASSES


class CRNN(nn.Module):
    def __init__(self, num_classes: int = NUM_CLASSES, rnn_hidden: int = 96):
        super().__init__()

        def block(cin, cout, pool):
            return nn.Sequential(
                nn.Conv2d(cin, cout, 3, padding=1),
                nn.BatchNorm2d(cout),
                nn.ReLU(inplace=True),
                nn.MaxPool2d(pool),
            )

        self.cnn = nn.Sequential(
            block(1, 32, (2, 2)),    # 32xW -> 16 x W/2
            block(32, 64, (2, 2)),   # 16 -> 8, W/2 -> W/4
            block(64, 128, (2, 1)),  # 8 -> 4, width kept
            block(128, 128, (2, 1)),  # 4 -> 2, width kept
        )
        self.rnn = nn.GRU(128, rnn_hidden, num_layers=1, bidirectional=True, batch_first=True)
        self.fc = nn.Linear(rnn_hidden * 2, num_classes)

    def forward(self, x):
        f = self.cnn(x)                    # (B,128,2,S)
        # average over height (== AdaptiveAvgPool2d((1,None)) but ONNX-exportable)
        f = f.mean(dim=2, keepdim=True)    # (B,128,1,S)
        f = f.squeeze(2).permute(0, 2, 1)  # (B,S,128)
        f, _ = self.rnn(f)
        return self.fc(f)                  # (B,S,num_classes)


def greedy_decode(logits, blank: int):
    """logits: (B,S,C) -> list[list[int]] collapsed CTC paths."""
    idx = torch.argmax(logits, dim=2)
    out = []
    for row in idx:
        prev, seq = -1, []
        for v in row.tolist():
            if v != prev and v != blank:
                seq.append(v)
            prev = v
        out.append(seq)
    return out
