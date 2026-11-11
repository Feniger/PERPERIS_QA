#!/usr/bin/env bash
# PERPERIS QA — one-time private staging setup (milestone 4, GitHub front end, no QA storage).
#
# Run in Google Cloud Shell inside QA's own project (default: perperis-qa-staging),
# which must already have billing linked. Safe to re-run: every step checks whether
# its resource already exists and never replaces an existing secret value.
#
# It creates: an image store, two service accounts (runtime and deployer), keyless
# sign-in for the GitHub "Deploy staging" workflow (no service-account key), Identity
# Platform with email/password sign-in, a browser API key, and the secrets QA reads.
# It does NOT deploy the app, and it never asks for or shows secret values:
#   - perperis-qa-secret, perperis-qa-webhook-secret: random, generated here
#   - perperis-qa-web-api-key: the public browser key, created here
#   - perperis-qa-github-key, perperis-qa-members: created EMPTY; the owner adds their
#     values in the console (Secret Manager > secret > New version). See the doc.
# There is no database. The screenshot bucket has its own script, screenshots-bucket.sh.
# See docs/QA-CLOUD-06-STAGING.md.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-perperis-qa-staging}"
REGION="${REGION:-europe-north1}"
SERVICE="${SERVICE:-perperis-qa-staging}"
AR_REPO="${AR_REPO:-perperis-qa}"
GITHUB_REPO="${GITHUB_REPO:-Feniger/PERPERIS_QA}"
WORKFLOW="${WORKFLOW:-.github/workflows/deploy-staging.yml}"
POOL="${POOL:-github}"
PROVIDER="${PROVIDER:-perperis-qa}"

case "${PROJECT_ID}" in
  *kattens*|*perperis-app*)
    echo "Refusing: '${PROJECT_ID}' looks like the training app's project. QA must use its own project." >&2
    exit 1 ;;
esac

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
RUNTIME_SA="qa-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOYER_SA="qa-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
ORIGIN="https://${SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"

echo "Project:  ${PROJECT_ID} (${PROJECT_NUMBER})"
echo "Region:   ${REGION}"
echo "Service:  ${SERVICE} -> ${ORIGIN}"
echo "Deploys:  only ${GITHUB_REPO} ${WORKFLOW} on main"
if [[ "${QA_SETUP_CONFIRM:-}" != "yes" ]]; then
  read -r -p "Continue with this project? Type yes: " answer
  [[ "${answer}" == "yes" ]] || { echo "Stopped."; exit 1; }
fi

step() { printf '\n== %s\n' "$*"; }

step "Enabling the services QA needs"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com iam.googleapis.com \
  iamcredentials.googleapis.com sts.googleapis.com secretmanager.googleapis.com \
  identitytoolkit.googleapis.com apikeys.googleapis.com cloudresourcemanager.googleapis.com \
  --project "${PROJECT_ID}"

step "Image store (Artifact Registry) in ${REGION}"
if ! gcloud artifacts repositories describe "${AR_REPO}" --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${AR_REPO}" --repository-format=docker --location "${REGION}" \
    --description="PERPERIS QA staging images" --project "${PROJECT_ID}"
fi
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

step "Service accounts"
for sa in qa-runtime qa-deployer; do
  if ! gcloud iam service-accounts describe "${sa}@${PROJECT_ID}.iam.gserviceaccount.com" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud iam service-accounts create "${sa}" --project "${PROJECT_ID}" \
      --display-name "PERPERIS QA ${sa#qa-}"
  fi
done

step "Runtime permissions (least privilege)"
# Sign-in: read account state so disabled or revoked accounts lose their session.
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${RUNTIME_SA}" --role roles/firebaseauth.viewer --condition=None >/dev/null

step "Deployer permissions (used only by the GitHub 'Deploy staging' workflow)"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${DEPLOYER_SA}" --role roles/run.admin --condition=None >/dev/null
# Lets the workflow check that every secret has a value before deploying (never reads values).
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${DEPLOYER_SA}" --role roles/secretmanager.viewer --condition=None >/dev/null
gcloud artifacts repositories add-iam-policy-binding "${AR_REPO}" --location "${REGION}" --project "${PROJECT_ID}" \
  --member "serviceAccount:${DEPLOYER_SA}" --role roles/artifactregistry.writer >/dev/null
gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" --project "${PROJECT_ID}" \
  --member "serviceAccount:${DEPLOYER_SA}" --role roles/iam.serviceAccountUser >/dev/null

step "Keyless GitHub sign-in (only the deploy workflow on main; no service-account key)"
CONDITION="assertion.repository=='${GITHUB_REPO}' && assertion.ref=='refs/heads/main' && assertion.workflow_ref.startsWith('${GITHUB_REPO}/${WORKFLOW}@')"
if ! gcloud iam workload-identity-pools describe "${POOL}" --location global --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "${POOL}" --location global --project "${PROJECT_ID}" \
    --display-name "GitHub Actions"
fi
if ! gcloud iam workload-identity-pools providers describe "${PROVIDER}" --workload-identity-pool "${POOL}" \
    --location global --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER}" \
    --workload-identity-pool "${POOL}" --location global --project "${PROJECT_ID}" \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref,attribute.workflow_ref=assertion.workflow_ref" \
    --attribute-condition "${CONDITION}"
else
  gcloud iam workload-identity-pools providers update-oidc "${PROVIDER}" \
    --workload-identity-pool "${POOL}" --location global --project "${PROJECT_ID}" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref,attribute.workflow_ref=assertion.workflow_ref" \
    --attribute-condition "${CONDITION}" >/dev/null
fi
gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER_SA}" --project "${PROJECT_ID}" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GITHUB_REPO}" >/dev/null

step "Identity Platform: email/password sign-in, ${ORIGIN#https://} as authorised domain"
token="$(gcloud auth print-access-token)"
api() {  # method, url, [json]
  curl -sS -o /tmp/qa-idp.json -w '%{http_code}' -X "$1" "$2" \
    -H "Authorization: Bearer ${token}" -H "x-goog-user-project: ${PROJECT_ID}" \
    -H "Content-Type: application/json" ${3:+--data "$3"}
}
code="$(api POST "https://identitytoolkit.googleapis.com/v2/projects/${PROJECT_ID}/identityPlatform:initializeAuth" '{}')"
case "${code}" in
  200) echo "Identity Platform initialised." ;;
  400|409) echo "Identity Platform already initialised." ;;
  *) echo "Could not initialise Identity Platform (HTTP ${code}); enable it in the console (Identity Platform > Enable)." >&2 ;;
esac
code="$(api PATCH "https://identitytoolkit.googleapis.com/admin/v2/projects/${PROJECT_ID}/config?updateMask=signIn.email.enabled,signIn.email.passwordRequired,authorizedDomains" \
  "{\"signIn\":{\"email\":{\"enabled\":true,\"passwordRequired\":true}},\"authorizedDomains\":[\"${ORIGIN#https://}\"]}")"
if [[ "${code}" == "200" ]]; then echo "Email/password sign-in enabled."; else
  echo "Could not configure sign-in (HTTP ${code}); in the console enable Email/Password and add ${ORIGIN#https://} as an authorised domain." >&2; fi
rm -f /tmp/qa-idp.json
unset token

step "Secrets (values never shown)"
ensure_secret() {  # name: create it (no value) if missing and let the runtime read it
  local name="$1"
  if ! gcloud secrets describe "${name}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud secrets create "${name}" --replication-policy=user-managed --locations "${REGION}" --project "${PROJECT_ID}" >/dev/null
  fi
  gcloud secrets add-iam-policy-binding "${name}" --project "${PROJECT_ID}" \
    --member "serviceAccount:${RUNTIME_SA}" --role roles/secretmanager.secretAccessor >/dev/null
}
has_value() {
  [[ -n "$(gcloud secrets versions list "$1" --filter='state=ENABLED' --limit=1 --format='value(name)' --project "${PROJECT_ID}")" ]]
}
add_secret() {  # name; value from stdin. Never replaces an existing value.
  local name="$1"
  ensure_secret "${name}"
  if has_value "${name}"; then
    cat >/dev/null; echo "${name}: already has a value; kept."
  else
    gcloud secrets versions add "${name}" --data-file=- --project "${PROJECT_ID}" >/dev/null && echo "${name}: value stored."
  fi
}
openssl rand -hex 32 | tr -d '\n' | add_secret perperis-qa-secret
openssl rand -hex 32 | tr -d '\n' | add_secret perperis-qa-webhook-secret
if [[ -z "$(gcloud services api-keys list --project "${PROJECT_ID}" --filter='displayName=perperis-qa-browser' --format='value(name)')" ]]; then
  # The create operation prints the new key on stderr; silence both streams so it never shows.
  gcloud services api-keys create --project "${PROJECT_ID}" --display-name perperis-qa-browser \
    --api-target=service=identitytoolkit.googleapis.com >/dev/null 2>&1 \
    || { echo "Could not create the browser API key." >&2; exit 1; }
fi
key_name="$(gcloud services api-keys list --project "${PROJECT_ID}" --filter='displayName=perperis-qa-browser' --format='value(name)' | head -n1)"
gcloud services api-keys get-key-string "${key_name}" --project "${PROJECT_ID}" --format='value(keyString)' \
  | tr -d '\n' | add_secret perperis-qa-web-api-key
for name in perperis-qa-github-key perperis-qa-members; do
  ensure_secret "${name}"
  if has_value "${name}"; then echo "${name}: has a value."; else echo "${name}: created, waiting for the owner to add its value."; fi
done

cat <<EOF

== Done. Nothing secret was printed.
Address:      ${ORIGIN}
WIF provider: projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}
Deployer:     ${DEPLOYER_SA}
Runtime:      ${RUNTIME_SA}

Owner, before the first deploy (Secret Manager in the console, project ${PROJECT_ID}):
- perperis-qa-github-key > New version > upload the GitHub App .pem file
- perperis-qa-members    > New version > e.g. you@example.com=Admin,creator@example.com=Admin
Then run "Deploy staging" in GitHub Actions (Feniger/PERPERIS_QA).
EOF
