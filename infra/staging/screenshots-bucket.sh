#!/usr/bin/env bash
# PERPERIS QA — private screenshot bucket for staging (screenshots step 1).
#
# Run in Google Cloud Shell in QA's own project after setup.sh. Safe to re-run.
# It creates or updates exactly:
#   - bucket gs://${BUCKET} in ${REGION}: Standard storage, uniform access,
#     public access prevention enforced, soft delete off (deleted = gone)
#   - two lifecycle rules: delete 30 days after an object's custom time
#     (set by QA on Verify), and delete anything older than 365 days
#   - bucket-level access for qa-runtime only: read/write/delete objects
#     (roles/storage.objectUser) and read the bucket's settings
#     (roles/storage.legacyBucketReader, for the start-up privacy check)
# It enables the Cloud Storage API if needed. Nothing else changes, no data is
# uploaded and nothing secret is printed. See docs/QA-CLOUD-06-STAGING.md.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-perperis-qa-staging}"
REGION="${REGION:-europe-north1}"
BUCKET="${BUCKET:-perperis-qa-staging-screenshots}"
RUNTIME_SA="qa-runtime@${PROJECT_ID}.iam.gserviceaccount.com"

case "${PROJECT_ID}" in
  *kattens*|*perperis-app*)
    echo "Refusing: '${PROJECT_ID}' looks like the training app's project. QA must use its own project." >&2
    exit 1 ;;
esac

echo "Project: ${PROJECT_ID}"
echo "Bucket:  gs://${BUCKET} (${REGION}, private)"
echo "Access:  ${RUNTIME_SA} only"
if [[ "${QA_SETUP_CONFIRM:-}" != "yes" ]]; then
  read -r -p "Create or update this bucket? Type yes: " answer
  [[ "${answer}" == "yes" ]] || { echo "Stopped."; exit 1; }
fi

gcloud iam service-accounts describe "${RUNTIME_SA}" --project "${PROJECT_ID}" >/dev/null \
  || { echo "qa-runtime is missing; run setup.sh first." >&2; exit 1; }
gcloud services enable storage.googleapis.com --project "${PROJECT_ID}"

lifecycle="$(mktemp)"
cat > "${lifecycle}" <<'JSON'
{"rule": [
  {"action": {"type": "Delete"}, "condition": {"daysSinceCustomTime": 30}},
  {"action": {"type": "Delete"}, "condition": {"age": 365}}
]}
JSON

if gcloud storage buckets describe "gs://${BUCKET}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "Bucket exists; enforcing its settings."
  gcloud storage buckets update "gs://${BUCKET}" --project "${PROJECT_ID}" \
    --uniform-bucket-level-access --public-access-prevention --soft-delete-duration=0 \
    --lifecycle-file="${lifecycle}" >/dev/null
else
  gcloud storage buckets create "gs://${BUCKET}" --project "${PROJECT_ID}" --location "${REGION}" \
    --default-storage-class=STANDARD --uniform-bucket-level-access --public-access-prevention \
    --soft-delete-duration=0 --lifecycle-file="${lifecycle}"
fi
rm -f "${lifecycle}"

for role in roles/storage.objectUser roles/storage.legacyBucketReader; do
  gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" --project "${PROJECT_ID}" \
    --member "serviceAccount:${RUNTIME_SA}" --role "${role}" >/dev/null
done

gcloud storage buckets describe "gs://${BUCKET}" --project "${PROJECT_ID}" \
  --format='yaml(name,location,storage_class,uniform_bucket_level_access,public_access_prevention,soft_delete_policy,lifecycle_config)'
echo
echo "Done. Next: the deploy workflow passes PERPERIS_QA_SCREENSHOT_BUCKET=${BUCKET} (screenshots PR 2)."
