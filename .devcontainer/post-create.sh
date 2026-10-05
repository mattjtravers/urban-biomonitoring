#!/usr/bin/env bash
set -e

echo "Installing System audio libraries"
sudo apt-get update
sudo apt-get install -y --no-install-recommends ffmpeg libsndfile1 sox
sudo rm -rf /var/lib/apt/lists/*

echo "Installing python packages via uv..."
uv sync --all-groups

echo "Installing LID plugins for Claude..."
claude plugin marketplace add jszmajda/lid || true
claude plugin install linked-intent-dev@jszmajda-lid || true
claude plugin install arrow-maintenance@jszmajda-lid || true
