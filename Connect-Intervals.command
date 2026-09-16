#!/bin/zsh
set -eu
COACH_ROOT="${0:A:h}"
export CLOUDSDK_CONFIG="$COACH_ROOT/.local/gcloud"
export GCLOUD_BIN="$COACH_ROOT/.tools/google-cloud-sdk/bin/gcloud"
python3 "$COACH_ROOT/infra/put_secret.py" --project=magne-ai-coach-20260915
printf '\nConnection key saved in Google Secret Manager. You can close this window.\n'
read '?Press Enter to close. '
