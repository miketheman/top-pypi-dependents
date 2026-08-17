# GCP setup for the monthly refresh

This runbook wires up the Google Cloud project that the `Refresh` workflow
(`.github/workflows/refresh.yml`) authenticates to via Workload Identity
Federation, so it can query `bigquery-public-data.pypi.distribution_metadata`
without a long-lived key. Follow the steps in order; each one depends on
values noted by an earlier step.

Replace every `<placeholder>` before running a command. Values used more than
once are called out so you don't have to hunt back through the doc.

## 0. Prerequisites

- The `gcloud` CLI, authenticated as a user with permission to create
  projects (or an existing project you can administer) and to manage IAM on
  it: `gcloud auth login`.
- Repository admin access to `miketheman/top-pypi-dependents`, to set
  repository variables in step 6.

## 1. Create the project and attach billing

```bash
gcloud projects create <PROJECT_ID> --name="top-pypi-dependents"

gcloud billing projects link <PROJECT_ID> \
  --billing-account=<BILLING_ACCOUNT_ID>
```

Find `<BILLING_ACCOUNT_ID>` with `gcloud billing accounts list`. A billing
account must be linked even though this pipeline's expected monthly usage
(one pass over a handful of columns of one public table, plus a small audit
sample) falls inside BigQuery's 1TB/month free query tier — BigQuery refuses
to run any query, free or not, on a project with no billing account attached.

Note the project id (`<PROJECT_ID>`) — every command below uses it.

## 2. Enable the BigQuery API

```bash
gcloud services enable bigquery.googleapis.com --project=<PROJECT_ID>
```

## 3. Create a service account with only `bigquery.jobUser`

```bash
gcloud iam service-accounts create top-pypi-dependents \
  --project=<PROJECT_ID> \
  --display-name="top-pypi-dependents monthly refresh"

gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="serviceAccount:top-pypi-dependents@<PROJECT_ID>.iam.gserviceaccount.com" \
  --role="roles/bigquery.jobUser"
```

`roles/bigquery.jobUser` lets the service account run (and pay for) query
jobs in this project. It grants no dataset-level access, and none is needed:
`bigquery-public-data` is a world-readable public dataset, and this pipeline
never creates, writes, or reads any table of its own. Do not grant
`roles/bigquery.dataEditor`, `roles/bigquery.dataViewer`, or anything wider —
this account should not be able to touch a table anywhere.

## 4. Create a Workload Identity Pool and OIDC provider for GitHub Actions

```bash
gcloud iam workload-identity-pools create github-actions \
  --project=<PROJECT_ID> \
  --location="global" \
  --display-name="GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc github-actions \
  --project=<PROJECT_ID> \
  --location="global" \
  --workload-identity-pool="github-actions" \
  --display-name="GitHub Actions OIDC" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository == 'miketheman/top-pypi-dependents'"
```

**The `--attribute-condition` is the security-critical part of this whole
setup.** Without it, the provider trusts *any* valid GitHub Actions OIDC
token from *any* GitHub repository — meaning any GitHub repository, public or
private, belonging to anyone, could mint a token that impersonates this
service account and run BigQuery jobs billed to this project. The condition
restricts the provider to tokens whose `repository` claim is exactly
`miketheman/top-pypi-dependents`, so only workflow runs in this repository
can authenticate as this identity. Do not omit it, and do not widen it to a
prefix or an organization-level match without deliberately deciding to trust
more repositories.

## 5. Bind the service account to the pool's principal set for this repository

```bash
PROJECT_NUMBER=$(gcloud projects describe <PROJECT_ID> --format="value(projectNumber)")

gcloud iam service-accounts add-iam-policy-binding \
  top-pypi-dependents@<PROJECT_ID>.iam.gserviceaccount.com \
  --project=<PROJECT_ID> \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-actions/attribute.repository/miketheman/top-pypi-dependents"
```

This is what actually lets the token exchange succeed: it says "a token
carrying `attribute.repository = miketheman/top-pypi-dependents`, issued
through this pool, may act as this service account." It's a second,
independent restriction to the same repository — the attribute condition in
step 4 rejects the token outright; this binding is what would reject it even
if the condition were ever loosened.

## 6. Set the repository variables

Get the full provider resource name:

```bash
gcloud iam workload-identity-pools providers describe github-actions \
  --project=<PROJECT_ID> \
  --location="global" \
  --workload-identity-pool="github-actions" \
  --format="value(name)"
```

Then set the three repository variables the workflow reads
(`vars.GCP_WORKLOAD_IDENTITY_PROVIDER`, `vars.GCP_SERVICE_ACCOUNT`,
`vars.GCP_PROJECT_ID`):

```bash
gh variable set GCP_WORKLOAD_IDENTITY_PROVIDER \
  --repo miketheman/top-pypi-dependents \
  --body "<PROVIDER_RESOURCE_NAME_FROM_ABOVE>"

gh variable set GCP_SERVICE_ACCOUNT \
  --repo miketheman/top-pypi-dependents \
  --body "top-pypi-dependents@<PROJECT_ID>.iam.gserviceaccount.com"

gh variable set GCP_PROJECT_ID \
  --repo miketheman/top-pypi-dependents \
  --body "<PROJECT_ID>"
```

These are repository *variables*, set with `gh variable set`, not
*secrets*. None of the three values is a credential: the provider name and
service account email are identifiers, not something that can be replayed
to authenticate as anything by itself, and the project id is public. The
actual short-lived credential is minted per-run by
`google-github-actions/auth`, never stored anywhere.

## 7. Local development

To run `extract` from a laptop against this project:

```bash
gcloud auth application-default login
gcloud config set project <PROJECT_ID>
```

`uv run top-pypi-dependents extract` picks up Application Default
Credentials automatically; pass `--project <PROJECT_ID>` if you want to be
explicit about which project's quota pays for the query.

## 8. First run: measure the actual cost

```bash
uv run top-pypi-dependents extract --dry-run --project <PROJECT_ID>
```

This reports BigQuery's estimated bytes-to-be-scanned without running a
billable query. Compare it against the measured figures in the "Cost model"
section of
`docs/superpowers/specs/2026-08-16-top-pypi-dependents-design.md` — 7.8 GB
for the winners query and 538 MB for the audit sample. If what you see is
materially larger, the query or the upstream table has changed and the
free-tier assumption in that document needs revisiting before you run the
real extract; update the figures there to match.
