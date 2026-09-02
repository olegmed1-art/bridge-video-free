#!/usr/bin/env sh
set -eu

# Do not permit an implicit Hugging Face download during a production job.  A
# model is populated by a deliberate image/mount preparation step and proven
# below before the resident worker can accept a task.
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

exec python -m universal_video.container_runtime "$@"
