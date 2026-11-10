#!/usr/bin/env bash
# PERPERIS QA — one-time private staging setup (QA-CLOUD-06, Neon variant).
#
# Run in Google Cloud Shell (shell.cloud.google.com) by the project owner, inside
# a NEW, dedicated QA project that already has billing linked. It is safe to
# re-run: every step checks whether its resource already exists.
#
# It creates: an image store, a private screenshot bucket, two service accounts
# (runtime and deployer), keyless GitHub sign-in for the deploy workflow, and two
# secrets. It does NOT create the database (Neon), enable Identity Platform's
# email provider, or deploy the app. See docs/QA-CLOUD-06-STAGING.md.
#
# Never paste the output of this script anywhere public except the values it
# lists under "Copy these into GitHub"; those are identifiers, not secrets.
set -euo pipefail

REGION="${REGION:-europe-north1}"
SERVICE="${SERVICE:-perperis-qa-staging}"
AR_REPO="${AR_REPO:-perperis-qa}"
GITHUB_REPO="${GITHUB_REPO:-Feniger/PERPERIS_QA}"
GITHUB_ENVIRONMENT="${GITHUB_ENVIRONMENT:-staging}"
POOL="${POOL:-github}"
PROVIDER="${PROVIDER:-perperis-qa}"

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
if [[ -z "${PROJECT_ID}" ]]; then
  echo "No project selected. Run: gcloud config set project YOUR-QA-PROJECT-ID" >&2
  exit 1
fi
case "${PROJECT_ID}" in
  *kattens*|*perperis-app*)
    echo "Refusing: '${PROJECT_ID}' looks like the training app's project. QA must use its own project." >&2
    exit 1 ;;
esac

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
BUCKET="${BUCKET:-${PROJECT_ID}-qa-screenshots}"
RUNTIME_SA="qa-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOYER_SA="qa-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
ORIGIN="https://${SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"

echo "Project:  ${PROJECT_ID} (${PROJECT_NUMBER})"
echo "Region:   ${REGION}"
echo "Service:  ${SERVICE}"
echo "GitHub:   ${GITHUB_REPO} (environment '${GITHUB_ENVIRONMENT}')"
read -r -p "Continue with this project? Type yes: " answer
[[ "${answer}" == "yes" ]] || { echo "Stopped."; exit 1; }

step() { printf '\n== %s\n' "$*"; }

step "Enabling the services QA needs"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com iam.googleapis.com \
  iamcredentials.googleapis.com sts.googleapis.com secretmanager.googleapis.com \
  identitytoolkit.googleapis.com storage.googleapis.com cloudresourcemanager.googleapis.com \
  --project "${PROJECT_ID}"

step "Image store (Artifact Registry) in ${REGION}"
if ! gcloud artifacts repositories describe "${AR_REPO}" --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${AR_REPO}" --repository-format=docker --location "${REGION}" \
    --description="PERPERIS QA staging images" --project "${PROJECT_ID}"
fi
# Keep only recent images so storage stays small. Best effort: a failure here is not fatal.
policy="$(mktemp)"
cat > "${policy}" <<'JSON'
[
  {"name": "keep-recent", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 3}},
  {"name": "delete-old", "action": {"type": "Delete"}, "condition": {"olderThan": "14d"}}
]
JSON
gcloud artifacts repositories set-cleanup-policies "${AR_REPO}" --location "${REGION}" \
  --policy "${policy}" --no-dry-run --project "${PROJECT_ID}" \
  || echo "Note: could not set the image cleanup policy; set it in the console later."
rm -f "${policy}"

step "Private screenshot bucket gs://${BUCKET}"
if ! gcloud storage buckets describe "gs://${BUCKET}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUCKET}" --location "${REGION}" \
    --uniform-bucket-level-access --public-access-prevention --project "${PROJECT_ID}"
fi
# The app refuses to start unless both protections are on; enforce them on re-runs too.
gcloud storage buckets update "gs://${BUCKET}" --uniform-bucket-level-access --public-access-prevention

step "Service accounts"
for sa in qa-runtime qa-deployer; do
  if ! gcloud iam service-accounts describe "${sa}@${PROJECT_ID}.iam.gserviceaccount.com" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud iam service-accounts create "${sa}" --project "${PROJECT_ID}" \
      --display-name "PERPERIS QA ${sa#qa-}"
  fi
done

step "Runtime permissions (least privilege)"
# Screenshots: read/write/delete objects, and read bucket settings (startup check).
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member "serviceAccount:${RUNTIME_SA}" --role roles/storage.objectUser >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member "serviceAccount:${RUNTIME_SA}" --role roles/storage.legacyBucketReader >/dev/null
# Sign-in: read user state to honour revoked or disabled accounts.
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${RUNTIME_SA}" --role roles/firebaseauth.viewer --condition=None >/dev/null

step "Deployer permissions (used only by the GitHub 'Deploy staging' workflow)"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${DEPLOYER_SA}" --role roles/run.admin --condition=None >/dev/null
gcloud artifacts repositories add-iam-policy-binding "${AR_REPO}" --location "${REGION}" --project "${PROJECT_ID}" \
  --member "serviceAccount:${DEPLOYER_SA}" --role roles/artifactregistry.writer >/dev/null
gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" --project "${PROJECT_ID}" \
  --member "serviceAccount:${DEPLOYER_SA}" --role roles/iam.serviceAccountUser >/dev/null

step "Keyless GitHub sign-in (no service-account key is created)"
if ! gcloud iam workload-identity-pools describe "${POOL}" --location global --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "${POOL}" --location global --project "${PROJECT_ID}" \
    --display-name "GitHub Actions"
fi
if ! gcloud iam workload-identity-pools providers describe "${PROVIDER}" --workload-identity-pool "${POOL}" \
    --location global --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER}" \
    --workload-identity-pool "${POOL}" --location global --project "${PROJECT_ID}" \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.environment=assertion.environment" \
    --attribute-condition "assertion.repository=='${GITHUB_REPO}' && assertion.environment=='${GITHUB_ENVIRONMENT}'"
fi
gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER_SA}" --project "${PROJECT_ID}" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GITHUB_REPO}" >/dev/null

step "Secrets"
create_or_add_secret() {  # name, value from stdin
  local name="$1"
  if gcloud secrets describe "${name}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud secrets versions add "${name}" --data-file=- --project "${PROJECT_ID}" >/dev/null
  else
    gcloud secrets create "${name}" --data-file=- --replication-policy=user-managed \
      --locations "${REGION}" --project "${PROJECT_ID}" >/dev/null
  fi
  gcloud secrets add-iam-policy-binding "${name}" --project "${PROJECT_ID}" \
    --member "serviceAccount:${RUNTIME_SA}" --role roles/secretmanager.secretAccessor >/dev/null
}
if gcloud secrets describe perperis-qa-secret --project "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "perperis-qa-secret already exists; keeping it."
  gcloud secrets add-iam-policy-binding perperis-qa-secret --project "${PROJECT_ID}" \
    --member "serviceAccount:${RUNTIME_SA}" --role roles/secretmanager.secretAccessor >/dev/null
else
  # 64 random hex characters, generated here; never shown.
  openssl rand -hex 32 | tr -d '\n' | create_or_add_secret perperis-qa-secret
  echo "Created perperis-qa-secret (random, not shown)."
fi
echo
echo "Paste the Neon connection string for the RUNTIME role (qa_runtime)."
echo "It is hidden while you paste and is stored only in Secret Manager."
read -r -s -p "Runtime database URL (leave empty to keep the current one): " db_url
echo
if [[ -n "${db_url}" ]]; then
  case "${db_url}" in
    postgresql://*sslmode=require*|postgresql://*sslmode=verify-*|postgres://*sslmode=require*|postgres://*sslmode=verify-*) ;;
    *) echo "That does not look like a Neon URL with sslmode=require; nothing was saved." >&2; exit 1 ;;
  esac
  printf '%s' "${db_url}" | create_or_add_secret perperis-qa-database-url
  echo "Saved perperis-qa-database-url."
  unset db_url
elif ! gcloud secrets describe perperis-qa-database-url --project "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "No database URL saved yet. Re-run this script when you have it." >&2
fi

cat <<EOF

== Done. Copy these into GitHub: Feniger/PERPERIS_QA > Settings > Environments > ${GITHUB_ENVIRONMENT} > Variables
GCP_PROJECT_ID     = ${PROJECT_ID}
GCP_REGION         = ${REGION}
GCP_WIF_PROVIDER   = projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}
GCP_DEPLOYER_SA    = ${DEPLOYER_SA}
GCP_RUNTIME_SA     = ${RUNTIME_SA}
QA_SERVICE         = ${SERVICE}
QA_BUCKET          = ${BUCKET}
QA_ORIGIN          = ${ORIGIN}

Still to do (see docs/QA-CLOUD-06-STAGING.md):
- Identity Platform: enable Email/Password, add ${ORIGIN#https://} as an authorised domain,
  and copy the browser API key into the GitHub variable QA_IDENTITY_WEB_API_KEY.
- GitHub environment secret QA_MIGRATION_DATABASE_URL = the Neon URL for the MIGRATION role.
- Set a budget alert under Billing > Budgets & alerts.
EOF
