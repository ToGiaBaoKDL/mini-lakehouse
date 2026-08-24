# Lightdash analytics

Lightdash analytics is split by responsibility:

| Path | Owner |
|---|---|
| `projects/<domain>/content/` | Versioned charts, dashboards, and spaces for one analytics domain |
| `deploy/sync-ci-secret` | Explicit operator handoff of the CI personal access token |
| `deploy/` | OCI runtime, database reconciliation, CI-secret handoff, and service startup |
| `../../.github/workflows/deploy-lightdash-projects.yml` | Project inventory and protected semantic/content delivery |
| `../../infra/terraform/github/environments/dev/` | Environment URL, secret reference, and workload identities |

Run `make lightdash-validate` without cloud access. The protected GitHub workflow is the only
normal dev delivery path after bootstrap; it uses the official Lightdash CLI directly and keeps
the repository-managed content authoritative with reviewed, forced uploads. Add a matrix entry to
onboard another project; the shared workflow applies the same compile, deploy, upload, and validate
contract without another script or copied deployment block.
