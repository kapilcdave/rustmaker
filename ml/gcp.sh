#!/usr/bin/env bash
# GCP plumbing for the gate model. Training is a self-deleting Spot GPU VM, so a forgotten window
# cannot bill a GPU overnight; inference is a small CPU VM that stays up next to the trader.
#
#   ./ml/gcp.sh bucket                  create the artifact bucket
#   ./ml/gcp.sh push ml/data/v1         upload a dataset
#   ./ml/gcp.sh train v1                launch the Spot GPU trainer (deletes itself when done)
#   ./ml/gcp.sh train-cpu v1            same, on CPU only -- a FREE-TIER billing account cannot
#                                       attach any non-TPU accelerator, whatever the GPU quota says
#   ./ml/gcp.sh logs                    tail the trainer's serial console
#   ./ml/gcp.sh pull v1                 download the adapter + report
#   ./ml/gcp.sh serve                   create the CPU inference VM
#   ./ml/gcp.sh down                    delete both VMs
set -euo pipefail

PROJECT="${GATE_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${GATE_REGION:-us-central1}"
ZONE="${GATE_ZONE:-us-central1-a}"
BUCKET="${GATE_BUCKET:-gs://${PROJECT}-mm15-gate}"
TRAIN_VM="${GATE_TRAIN_VM:-mm15-gate-train}"
SERVE_VM="${GATE_SERVE_VM:-mm15-gate-serve}"
# Quota in this project is 1 GPU of each type in us-central1: L4 (g2-standard-4) or T4 (n1).
GPU_MACHINE="${GATE_GPU_MACHINE:-g2-standard-4}"
GPU_TYPE="${GATE_GPU_TYPE:-nvidia-l4}"
REPO_URL="${GATE_REPO_URL:-}"   # optional git URL; otherwise the trainer only needs ml/ from GCS

say() { printf '\n=== %s\n' "$*"; }

cmd_bucket() {
  gcloud storage buckets create "$BUCKET" --project "$PROJECT" --location "$REGION" \
    --uniform-bucket-level-access 2>/dev/null || say "bucket exists: $BUCKET"
}

cmd_push() {
  local dir="${1:?dataset dir}" name
  name="$(basename "$dir")"
  gcloud storage cp "$dir"/*.jsonl "$dir"/rows.parquet "$dir"/manifest.json \
    "$BUCKET/data/$name/" --project "$PROJECT"
  gcloud storage cp ml/*.py "$BUCKET/code/" --project "$PROJECT"
  say "pushed dataset $name and ml/*.py"
}

cmd_train() {
  local name="${1:?dataset name}" torch_idx=""
  cat > /tmp/gate-startup.sh <<EOS
#!/bin/bash
set -x
exec > >(tee -a /var/log/gate-train.log) 2>&1
BUCKET="$BUCKET"; NAME="$name"; ZONE="$ZONE"; VM="$TRAIN_VM"
# the deep-learning image ships CUDA + torch; add the model stack
# The image ships a bare python3 with no pip, no conda and no torch (the old deep-learning layout
# with /opt/conda is gone), so the environment is built here rather than assumed.
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq python3-pip python3-venv \
  || { echo "FATAL: apt failed"; exit 1; }
mkdir -p /opt/gate
python3 -m venv /opt/gate/venv || { echo "FATAL: venv failed"; exit 1; }
PY=/opt/gate/venv/bin/python
"\$PY" -m pip install -q --upgrade pip wheel || { echo "FATAL: pip upgrade failed"; exit 1; }
"\$PY" -m pip install -q torch $torch_idx || { echo "FATAL: torch install failed"; exit 1; }
"\$PY" -m pip install -q "gliner2[local]" pandas pyarrow scikit-learn protobuf sentencepiece \
  || { echo "FATAL: pip install failed"; exit 1; }
"\$PY" -c "import numpy, pandas, torch, gliner2; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())" \
  || { echo "FATAL: imports missing"; exit 1; }
mkdir -p /opt/gate/ml/data/\$NAME /opt/gate/ml/runs
PLACEHOLDER
gsutil -m cp -r ml/runs/\$NAME "\$BUCKET/runs/" || true
gsutil cp /var/log/gate-train.log "\$BUCKET/runs/\$NAME/train.log" || true
# never leave a GPU running: the VM removes itself
gcloud -q compute instances delete \$VM --zone \$ZONE
EOS
  gcloud compute instances create "$TRAIN_VM" \
    --project "$PROJECT" --zone "$ZONE" \
    --machine-type "$GPU_MACHINE" \
    --accelerator "type=$GPU_TYPE,count=1" \
    --provisioning-model SPOT --instance-termination-action DELETE \
    --maintenance-policy TERMINATE \
    --image-family "${GATE_GPU_IMAGE:-pytorch-2-9-cu129-ubuntu-2204-nvidia-580}" --image-project deeplearning-platform-release \
    --boot-disk-size 100GB --boot-disk-type pd-balanced \
    --metadata install-nvidia-driver=True \
    --metadata-from-file startup-script=/tmp/gate-startup.sh \
    --max-run-duration "${GATE_MAX_RUN:-8h}" \
    --scopes https://www.googleapis.com/auth/cloud-platform
  say "training on $TRAIN_VM; it deletes itself when done. ./ml/gcp.sh logs to watch"
}

# A free-tier billing account is refused every non-TPU accelerator ("non-TPU accelerators are not
# available"), independently of the per-region GPU quota, so this path exists to run the same job
# without one. Upgrading the billing account keeps the remaining trial credit and unlocks the GPU.
cmd_train_cpu() {
  local name="${1:?dataset name}"
  local torch_idx="--index-url https://download.pytorch.org/whl/cpu"
  local epochs="${GATE_EPOCHS:-2}" batch="${GATE_BATCH:-32}" base="${GATE_BASE:-fastino/gliner2.5-small-v1}"
  cat > /tmp/gate-startup-cpu.sh <<EOS
#!/bin/bash
set -x
exec > >(tee -a /var/log/gate-train.log) 2>&1
BUCKET="$BUCKET"; NAME="$name"; ZONE="$ZONE"; VM="$TRAIN_VM"
# The image ships a bare python3 with no pip, no conda and no torch (the old deep-learning layout
# with /opt/conda is gone), so the environment is built here rather than assumed.
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq python3-pip python3-venv \
  || { echo "FATAL: apt failed"; exit 1; }
mkdir -p /opt/gate
python3 -m venv /opt/gate/venv || { echo "FATAL: venv failed"; exit 1; }
PY=/opt/gate/venv/bin/python
"\$PY" -m pip install -q --upgrade pip wheel || { echo "FATAL: pip upgrade failed"; exit 1; }
"\$PY" -m pip install -q torch $torch_idx || { echo "FATAL: torch install failed"; exit 1; }
"\$PY" -m pip install -q "gliner2[local]" pandas pyarrow scikit-learn protobuf sentencepiece \
  || { echo "FATAL: pip install failed"; exit 1; }
"\$PY" -c "import numpy, pandas, torch, gliner2; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())" \
  || { echo "FATAL: imports missing"; exit 1; }
mkdir -p /opt/gate/ml/data/\$NAME /opt/gate/ml/runs
gsutil -m cp "\$BUCKET/code/*.py" /opt/gate/ml/ || { echo "FATAL: code copy failed"; exit 1; }
gsutil -m cp "\$BUCKET/data/\$NAME/*" /opt/gate/ml/data/\$NAME/ || { echo "FATAL: data copy failed"; exit 1; }
test -s /opt/gate/ml/train_gliner.py || { echo "FATAL: trainer missing"; exit 1; }
cd /opt/gate
"\$PY" ml/train_gliner.py --data ml/data/\$NAME --out ml/runs/\$NAME \
  --base "$base" --epochs $epochs --batch-size $batch || true
gsutil -m cp -r ml/runs/\$NAME "\$BUCKET/runs/" || true
gsutil cp /var/log/gate-train.log "\$BUCKET/runs/\$NAME/train.log" || true
gcloud -q compute instances delete \$VM --zone \$ZONE
EOS
  gcloud compute instances create "$TRAIN_VM" \
    --project "$PROJECT" --zone "$ZONE" \
    --machine-type "${GATE_CPU_TRAIN_MACHINE:-e2-standard-16}" \
    --image-family "${GATE_CPU_IMAGE:-common-cu129-ubuntu-2204-nvidia-580}" \
    --image-project deeplearning-platform-release \
    --boot-disk-size 100GB --boot-disk-type pd-balanced \
    --metadata install-nvidia-driver=False \
    --metadata-from-file startup-script=/tmp/gate-startup-cpu.sh \
    --max-run-duration "${GATE_MAX_RUN:-8h}" --instance-termination-action DELETE \
    --scopes https://www.googleapis.com/auth/cloud-platform
  say "CPU training on $TRAIN_VM ($base, $epochs epochs); it deletes itself when done"
}

cmd_logs() {
  gcloud compute instances tail-serial-port-output "$TRAIN_VM" \
    --project "$PROJECT" --zone "$ZONE" || true
}

cmd_pull() {
  local name="${1:?run name}"
  mkdir -p "ml/runs/$name"
  gcloud storage cp -r "$BUCKET/runs/$name/*" "ml/runs/$name/" --project "$PROJECT"
  say "report:"; cat "ml/runs/$name/report.txt" 2>/dev/null || true
}

cmd_serve() {
  gcloud compute instances create "$SERVE_VM" \
    --project "$PROJECT" --zone "$ZONE" \
    --machine-type "${GATE_CPU_MACHINE:-e2-standard-4}" \
    --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
    --boot-disk-size 50GB --boot-disk-type pd-balanced \
    --no-address \
    --scopes https://www.googleapis.com/auth/devstorage.read_only
  say "no external IP: reach it with  gcloud compute ssh $SERVE_VM --tunnel-through-iap"
  say "then: pip install 'gliner2[local]' fastapi uvicorn protobuf sentencepiece && uvicorn ml.serve:app --host 127.0.0.1 --port 8008"
}

cmd_down() {
  for vm in "$TRAIN_VM" "$SERVE_VM"; do
    gcloud -q compute instances delete "$vm" --project "$PROJECT" --zone "$ZONE" 2>/dev/null \
      && say "deleted $vm" || true
  done
}

case "${1:-}" in
  bucket) cmd_bucket ;;
  push)   shift; cmd_push "$@" ;;
  train)  shift; cmd_train "$@" ;;
  train-cpu) shift; cmd_train_cpu "$@" ;;
  logs)   cmd_logs ;;
  pull)   shift; cmd_pull "$@" ;;
  serve)  cmd_serve ;;
  down)   cmd_down ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
