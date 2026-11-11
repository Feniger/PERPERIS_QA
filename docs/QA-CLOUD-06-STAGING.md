# QA-CLOUD-06 — private staging on Cloud Run with a free Neon database

Status: proposed. Nothing in this change creates cloud resources by itself. It adds
the validated startup checks, a one-time setup script, a manual deploy workflow and
this guide. Production stays blocked.

This follows `docs/RELEASE_PLAN.md` with one deliberate change: **Neon's free
PostgreSQL replaces Cloud SQL**, to keep staging at about $0 a month.

## Expected cost

| Part | Service | Expected monthly cost |
|---|---|---|
| App | Cloud Run, scales to zero, max 2 instances | $0 within the monthly free allowance |
| Database | Neon free plan (1 GB, sleeps after 5 idle minutes) | $0 |
| Sign-in | Identity Platform, email and password | $0 below 50,000 monthly active users |
| Screenshots | Private Cloud Storage bucket | cents |
| Images and secrets | Artifact Registry (keeps 3 images), Secret Manager (2 secrets) | cents |

Free plans have limits and no uptime guarantee; that is acceptable for testing.
Set a budget alert anyway (step 2).

## Steps (about 45 minutes, once)

You need: the Google account that will pay, a GitHub account with admin rights on
`Feniger/PERPERIS_QA`, and a free Neon account. Nothing has to be installed;
everything happens in the browser.

### 1. Neon database
1. Sign up at neon.com and create a project named `perperis-qa-staging`, in an EU region.
2. The project's owner role is the **migration role**. Copy its connection string
   from **Connect**. Use the **direct** connection (not the pooled one). It ends in
   `?sslmode=require...`. Keep it private.
3. Under **Roles**, create a second role named exactly **`qa_runtime`** and copy
   *its* connection string. This is the **runtime role**: the app logs in with it
   and gets only the rights in `infra/staging/runtime-grants.sql`.

### 2. Google Cloud project
1. In console.cloud.google.com, create a **new project** for QA only (for example
   `perperis-qa-staging`). Never use the training app's project.
2. Link billing to it (**Billing**) and set a budget alert (**Billing > Budgets &
   alerts**, for example 50 SEK a month).
3. Open **Cloud Shell** (the `>_` icon at the top right). Then run:
   ```bash
   gcloud config set project YOUR-QA-PROJECT-ID
   git clone https://github.com/Feniger/PERPERIS_QA.git && cd PERPERIS_QA
   bash infra/staging/setup.sh
   ```
   Type `yes` to confirm. When asked, paste the **runtime role** (`qa_runtime`)
   connection string. It stays hidden and is stored only in Secret Manager. At
   the end the script prints a list of values headed "Copy these into GitHub".

### 3. Sign-in (Identity Platform)
1. In the console, open **Identity Platform** and enable it for this project.
2. **Providers > Add a provider > Email / Password**. Enable it.
3. **Settings > Security > Authorized domains**: add the `...run.app` address that
   the script printed as `QA_ORIGIN`, without `https://`.
4. **APIs & Services > Credentials**: copy the **Browser key** (API key). Restrict
   it to the Identity Toolkit API and to the `QA_ORIGIN` address. It is public
   configuration, not a secret.

### 4. GitHub
In `Feniger/PERPERIS_QA`, open **Settings > Environments**, create **`staging`**, and add:
- **Variables:** every value the script printed, plus
  `QA_IDENTITY_WEB_API_KEY` (the browser key from step 3).
- **Secret:** `QA_MIGRATION_DATABASE_URL`, the **migration role** connection string from step 1.
- Optional: under **Deployment branches**, allow only `main`.

### 5. Deploy
**Actions > Deploy staging > Run workflow.** The workflow:
1. builds the app image;
2. updates the database structure with the migration role;
3. gives `qa_runtime` its rights;
4. deploys to Cloud Run.

The run summary shows the address.

### 6. First admin
1. Open the address and choose **Create verified account** with your email. Click
   the link in the verification email, then sign in once. You will see "pending
   approval"; that is expected.
2. In **Identity Platform > Users**, copy your account's **User UID**.
3. In Cloud Shell, inside the `PERPERIS_QA` folder:
   ```bash
   python3 -m venv .venv && . .venv/bin/activate && pip install -q -r requirements.txt
   read -r -s -p "Migration-role URL: " PERPERIS_QA_DATABASE_URL && export PERPERIS_QA_DATABASE_URL && echo
   python -m identity --subject PASTE-USER-UID --apply
   ```
   This works once, only while no Admin exists. Then sign in again. As Admin you
   approve everyone else under **Members**.

## Decisions for the owner

- **Public address with the app's own sign-in (default) or locked down.** By
  default the workflow deploys with `--allow-unauthenticated`. Anyone can reach the
  sign-in page, but nobody gets in without a verified email *and* Admin approval.
  The release plan's stricter option ("private staging invokers") blocks browsers
  without extra Google sign-in setup. To choose it, set the GitHub variable
  `QA_PUBLIC_URL=false`.
- **Database location.** Neon is reached over the internet with TLS and a
  password, not through a private Google path as the release plan proposed. The
  startup checks refuse any database URL without `sslmode=require`, `verify-ca`
  or `verify-full`.
- **Data.** Staging must hold test data only until backup and restore have been
  rehearsed. Neon's free plan keeps a short history window; it is not a backup policy.

## What changed in the code

- `runtime_guard.py`: replaces the blanket cloud block with the validated checks
  the release plan asks for.
  - `local` still runs only off Cloud Run.
  - `staging` runs only with identity sign-in, a TLS PostgreSQL URL, and the
    private bucket or no screenshots.
  - Every other stage, including production, is refused.
  - Error messages never include the database URL.
  - Covered by `tests/test_runtime_guard.py`, which needs no web stack.
- `app.py`: `check_runtime()` calls the guard. `.dockerignore` admits `runtime_guard.py`.
- `infra/staging/setup.sh`: idempotent Cloud Shell setup. It creates:
  - an image store with a cleanup policy;
  - a private bucket with uniform access and enforced public access prevention;
  - separate runtime and deployer service accounts, each with least privilege;
  - keyless GitHub federation, restricted to this repository and the `staging` environment;
  - two secrets: a random HMAC secret, and the runtime database URL, read hidden.

  It creates no service-account keys. It refuses project IDs that look like the training app's.
- `infra/staging/runtime-grants.sql`: least-privilege rights for `qa_runtime`. The
  audit tables are append-only for the app.
- `.github/workflows/deploy-staging.yml`: manual trigger only. It never runs on pull requests or merges.
  - Deploys an immutable image digest.
  - Migrations and grants use the separate migration role, inside a read-only,
    capability-dropped container.
  - Cloud Run settings: min 0, max 2, concurrency 4, 1 GiB memory, pool size 3,
    so at most 6 database connections.

## Not yet verified

This change was written without access to Google Cloud, Neon or GitHub Actions
runners. The guard tests ran. The script and workflow were checked for syntax
only. The first real run of each step is its test. Watch the first deploy, and
run the creator's evidence list in `RELEASE_PLAN.md` ("Private staging evidence
required") before relying on staging. The Python suite should pass unchanged:
the existing cloud-block tests still expect refusal for local mode on Cloud Run
and for `staging` with shared-password sign-in.

## Rollback

- **Stop staging:** delete the Cloud Run service, or set its max instances to 0.
- **Remove everything:** delete the QA Google project and the Neon project.
- **Restore the old lock:** revert this change. It only touches startup checks,
  never stored data.
