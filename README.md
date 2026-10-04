# ci

Shared CI for the lightwebinc repositories: reusable GitHub Actions workflows
and the Dagger pipeline package for the Go services. Each tool and action is
pinned here once, so a version bump is one change in this repository and an
ordinary dependency update everywhere else.

## Reusable workflows

Call them from a job with `uses:`, pinned to a commit SHA with the release in a
comment:

```yaml
jobs:
  ci:
    uses: lightwebinc/ci/.github/workflows/go-dagger.yml@<sha> # v0.1.0
```

| workflow | what it runs |
|---|---|
| `go-dagger.yml` | one make target of the repo's Dagger pipeline (`ci`, `ci-vuln`, ...) with the shared module checked out as a sibling |
| `go-host.yml` | host-Go build, vet, race tests, optional gofmt, tidy, cross-OS build, golangci-lint and licence checks |
| `govulncheck.yml` | govulncheck on the host toolchain |
| `codeql-go.yml` | CodeQL for Go (skips on private repositories) |
| `gh-release.yml` | GitHub release with generated notes for a tag |
| `image-publish.yml` | build the Dockerfile at git tag `v<tag>` and push to GHCR (`dry-run` builds only) |
| `helm-lint.yml` | `helm lint`, then render the defaults, every `ci/*.yaml` scenario and every `examples/*.yaml` |
| `helm-release.yml` | package a chart and push it to the OCI registry (`dry-run` packages only) |
| `ansible-lint.yml` | yamllint and ansible-lint |
| `terraform.yml` | terraform init, validate and fmt per directory, plus a trivy config scan |

Triggers, schedules, permissions and anything specific to one repository stay
in the calling workflow. Inputs and their defaults are documented at the top of
each file.

## Dagger pipeline package

`gopipe` is the whole pipeline; a repository's `ci/main.go` holds only its
configuration:

```go
package main

import "github.com/lightwebinc/ci/gopipe"

func main() { gopipe.Main(gopipe.Config{Repo: "shard-manifest"}) }
```

Subcommands and flags are listed in the package documentation
(`go doc github.com/lightwebinc/ci/gopipe`).

## Releases

Tags are plain semver (`v0.1.0`). Callers move to a new release through their
dependency updates: workflow refs as GitHub Actions updates, `gopipe` as a Go
module update in `ci/go.mod`.

## License

Apache 2.0, see [LICENSE](LICENSE).
