#!/usr/bin/env bash
# Fails if tracked files include audio, private config, or local data paths.
# Runs in CI (repo-hygiene job) and locally: bash scripts/check-repo-hygiene.sh
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

forbidden_path='(^|/)(config/private|quarantine|data|work|outputs?)/|\.local\.(toml|ya?ml)$|(^|/)\.env$|(^|/)\.claude/settings\.local\.json$'
audio_ext='\.(wav|flac|w4v|mp3|ogg|opus|m4a|aif|aiff)$'

status=0

while IFS= read -r -d '' f; do
  if [[ "$f" =~ $forbidden_path ]]; then
    echo "::error file=$f::private or local-data path must not be committed"
    status=1
  fi
  if [[ "${f,,}" =~ $audio_ext ]]; then
    echo "::error file=$f::audio file must not be committed"
    status=1
  elif [[ -f "$f" ]]; then
    # Catch audio committed under another extension by its magic bytes.
    head4=$(head -c 4 -- "$f" | LC_ALL=C tr -c '[:alnum:]' '.')
    form=$(head -c 12 -- "$f" | tail -c +9 | LC_ALL=C tr -c '[:alnum:]' '.')
    case "$head4:$form" in
      RIFF:WAVE | fLaC:* | OggS:* | ID3*:*)
        echo "::error file=$f::file content looks like audio"
        status=1 ;;
    esac
  fi
done < <(git ls-files -z)

if [[ $status -eq 0 ]]; then
  echo "repo hygiene: OK"
fi
exit $status
