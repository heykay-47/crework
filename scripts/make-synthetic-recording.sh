#!/usr/bin/env bash
set -euo pipefail

output="${1:-inbox/synthetic-feedback.mp4}"
mkdir -p "$(dirname "$output")"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "color=c=0x18212f:s=1280x720:d=8" \
  -f lavfi -i "sine=frequency=440:duration=8" \
  -vf "drawtext=text='Owned synthetic feedback recording':fontcolor=white:fontsize=42:x=(w-text_w)/2:y=(h-text_h)/2" \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$output"
printf 'Created %s\n' "$output"
