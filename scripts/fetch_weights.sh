#!/usr/bin/env bash
# Fetch model weights. Nothing in this repository downloads anything at run time: the pipeline must run cold on a
# machine with no network, because the walk-in test is a cold run, so every fetch is here and explicit. Each file
# is pinned to a Hugging Face commit and checked against its sha256; a mismatch fails the script.
#
# Disclosure, as the brief requires. The LiDAR tier uses none of these.
#
#   VGGT-1B            facebook/VGGT-1B                  photo and video frames reconstructed together   CC BY-NC 4.0   4.7 GB
#   MoGe-2 ViT-L       Ruicheng/moge-2-vitl-normal       metric scale, told the camera's field of view   MIT            1.2 GB
#   Depth Anything V2  depth-anything/...-Small-hf       monocular depth, used only if the two above     Apache-2.0      95 MB
#                                                        are absent
#
# VGGT-1B's weights are licensed for non-commercial research, which a case study is; a product would need Meta's
# commercial checkpoint, facebook/VGGT-1B-Commercial, which is gated.
#
#     scripts/fetch_weights.sh           fetch what is missing and verify everything
#     scripts/fetch_weights.sh --check   verify only
set -euo pipefail
cd "$(dirname "$0")/.."

WEIGHTS_DIR="${COZMO_WEIGHTS_DIR:-weights}"
PYTHON="${PYTHON:-.venv/bin/python}"
MODE="${1:-fetch}"

"$PYTHON" - "$WEIGHTS_DIR" "$MODE" <<'PYEOF'
import hashlib
import sys
from pathlib import Path

root, mode = Path(sys.argv[1]), sys.argv[2]
DA2 = ("depth-anything-v2-metric-indoor-small", "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf",
       "8078d68a9c75a972131914f6afd0c1723be0da7f")
# (local folder, repository, revision, file, sha256; None for configuration files)
FILES = [
    ("vggt-1b", "facebook/VGGT-1B", "860abec7937da0a4c03c41d3c269c366e82abdf9", "model.safetensors",
     "f164acf60724910d8fe1578bb499d800850c7bb0948db7555c413f9fbe60467e"),
    ("moge-2-vitl-normal", "Ruicheng/moge-2-vitl-normal", "cb0e8bbd6b1e243589717c78e750b1ba4c093acf", "model.pt",
     "280741fd09bc3f403ccff9967784c2a391b52d2c0742ae3efdb21d9f90cc1a01"),
    (*DA2, "model.safetensors", "e990eb82fbf11b05b7813261196a2b841bdcf5a05f64396724a8987fa90504a3"),
    (*DA2, "config.json", None),
    (*DA2, "preprocessor_config.json", None),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 24), b""):
            digest.update(block)
    return digest.hexdigest()


failed = False
for folder, repo, revision, name, expected in FILES:
    target = root / folder / name
    if not target.exists():
        if mode == "--check":
            print(f"missing  {folder}/{name}")
            failed = True
            continue
        from huggingface_hub import hf_hub_download

        print(f"fetching {folder}/{name} from {repo}@{revision[:8]}", flush=True)
        hf_hub_download(repo_id=repo, filename=name, revision=revision, local_dir=str(root / folder))
    if expected is not None and sha256(target) != expected:
        print(f"MISMATCH {folder}/{name}: expected sha256 {expected}")
        failed = True
        continue
    print(f"ok       {folder}/{name}")
sys.exit(1 if failed else 0)
PYEOF
