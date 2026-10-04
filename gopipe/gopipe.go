// Package gopipe is the shared Dagger pipeline for the Go service repos.
//
// A repo's ci/main.go is reduced to its configuration:
//
//	func main() { gopipe.Main(gopipe.Config{Repo: "shard-manifest"}) }
//
// and is run as before:
//
//	go run ./ci <subcommand> [flags]
//
// Subcommands:
//
//	unit       go test -race ./...
//	lint       go vet + golangci-lint
//	vuln       govulncheck
//	tidy       go mod tidy diff check
//	build      go build of Config.BuildTargets
//	image      build the OCI image from the repo Dockerfile (optionally export/publish)
//	all        tidy, lint, vuln, unit, build, then image, in that order
//	dev-shell  interactive shell in the builder container
//
// Flags:
//
//	-src     path to repo source (default ".")
//	-common  path to the shared-module sibling checkout (default "../shard-common")
//	-version version ldflag value (default "dev")
//	-address registry ref for `image` publish (e.g. ghcr.io/foo/bar:tag)
//	-export  tarball path for `image` export
//
// Toolchain and tool versions live here, once, instead of in every repo. A
// version bump is one release of this module; the repos pick it up as an
// ordinary dependency update of github.com/lightwebinc/ci in ci/go.mod.
package gopipe

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"

	"dagger.io/dagger"
)

// Pinned toolchain. Each line carries a renovate annotation so the dependency
// updater bumps it here and nowhere else.
const (
	// renovate: datasource=docker depName=golang
	GoImage = "golang:1.27.1-alpine"
	// renovate: datasource=docker depName=golangci/golangci-lint
	LintImage = "golangci/golangci-lint:v2.13.2-alpine"
	// The govulncheck pin tracks the Go toolchain in both directions: a
	// release built against older x/tools cannot read newer export data and
	// crashes instead of scanning. Re-check it on every toolchain bump.
	// renovate: datasource=go depName=golang.org/x/vuln
	GovulncheckVersion = "v1.7.0"
)

// DefaultCommonModule is the shared module that consumer repos build against
// from a sibling checkout, so that an untagged change to it can be tested
// against every consumer before it is released.
const DefaultCommonModule = "github.com/lightwebinc/shard-common"

// Config is everything that differs between repos.
type Config struct {
	// Repo names the cache volumes and log lines. Required.
	Repo string
	// CommonModule is replaced by the -common sibling checkout in every
	// stage except image. Empty means DefaultCommonModule; set NoCommon for a
	// repo that does not depend on it.
	CommonModule string
	NoCommon     bool
	// BuildTargets are the package paths the build stage compiles. The
	// Dockerfile stays the source of truth for the runtime image; this is a
	// source-level compile check. Empty means "./...".
	BuildTargets []string
	// Exclude adds host paths to leave out of the source upload (a binary
	// built in the repo root, for example).
	Exclude []string
}

type pipeline struct {
	cfg     Config
	c       *dagger.Client
	src     string
	common  string
	version string
}

// Main parses flags, runs one subcommand and exits non-zero on failure.
func Main(cfg Config) {
	if cfg.Repo == "" {
		log.Fatal("gopipe: Config.Repo is required")
	}
	if cfg.CommonModule == "" {
		cfg.CommonModule = DefaultCommonModule
	}
	if len(cfg.BuildTargets) == 0 {
		cfg.BuildTargets = []string{"./..."}
	}

	var (
		src     = flag.String("src", ".", "path to repo source")
		common  = flag.String("common", "../shard-common", "path to the shared-module sibling checkout")
		version = flag.String("version", "dev", "version ldflag value")
		address = flag.String("address", "", "registry ref for image publish")
		export  = flag.String("export", "", "tarball path for image export")
	)
	flag.Parse()

	cmd := flag.Arg(0)
	if cmd == "" {
		cmd = "all"
	}

	ctx := context.Background()
	c, err := dagger.Connect(ctx, dagger.WithLogOutput(os.Stderr))
	if err != nil {
		log.Fatalf("dagger connect: %v", err)
	}
	defer c.Close()

	p := &pipeline{cfg: cfg, c: c, src: *src, common: *common, version: *version}

	switch cmd {
	case "unit":
		fail(p.unit(ctx))
	case "lint":
		fail(p.lint(ctx))
	case "vuln":
		fail(p.vuln(ctx))
	case "tidy":
		fail(p.tidy(ctx))
	case "build":
		fail(p.build(ctx))
	case "image":
		fail(p.image(ctx, *address, *export))
	case "all":
		// A slice, not a map: the stages must run in a fixed order so a red
		// run always fails at the same stage.
		for _, step := range []struct {
			name string
			run  func(context.Context) error
		}{
			{"tidy", p.tidy},
			{"lint", p.lint},
			{"vuln", p.vuln},
			{"unit", p.unit},
			{"build", p.build},
		} {
			fmt.Fprintf(os.Stderr, "==> %s\n", step.name)
			fail(step.run(ctx))
		}
		fmt.Fprintln(os.Stderr, "==> image")
		fail(p.image(ctx, "", ""))
	case "dev-shell":
		fail(p.devShell(ctx))
	default:
		log.Fatalf("unknown subcommand %q (try: unit lint vuln tidy build image all dev-shell)", cmd)
	}
}

func fail(err error) {
	if err != nil {
		log.Fatal(err)
	}
}

func (p *pipeline) repoSrc() *dagger.Directory {
	exclude := append([]string{".git", "build", "ci/build", "*.tar"}, p.cfg.Exclude...)
	return p.c.Host().Directory(p.src, dagger.HostDirectoryOpts{Exclude: exclude})
}

func (p *pipeline) commonSrc() *dagger.Directory {
	return p.c.Host().Directory(p.common, dagger.HostDirectoryOpts{
		Exclude: []string{".git", "build"},
	})
}

func (p *pipeline) modCache() *dagger.CacheVolume   { return p.c.CacheVolume("go-mod-" + p.cfg.Repo) }
func (p *pipeline) buildCache() *dagger.CacheVolume { return p.c.CacheVolume("go-build-" + p.cfg.Repo) }
func (p *pipeline) lintCache() *dagger.CacheVolume  { return p.c.CacheVolume("golangci-" + p.cfg.Repo) }

// withSources mounts the repo at /src and, unless NoCommon, the shared module
// at /common with the replace directive applied.
func (p *pipeline) withSources(ctr *dagger.Container) *dagger.Container {
	ctr = ctr.WithDirectory("/src", p.repoSrc())
	if !p.cfg.NoCommon {
		ctr = ctr.WithDirectory("/common", p.commonSrc())
	}
	ctr = ctr.WithWorkdir("/src")
	if !p.cfg.NoCommon {
		ctr = ctr.WithExec([]string{"go", "mod", "edit", "-replace", p.cfg.CommonModule + "=/common"})
	}
	return ctr
}

// goBase is the builder container with sources mounted and modules downloaded.
func (p *pipeline) goBase() *dagger.Container {
	ctr := p.c.Container().From(GoImage).
		WithEnvVariable("CGO_ENABLED", "0").
		WithEnvVariable("GOFLAGS", "-buildvcs=false").
		WithMountedCache("/go/pkg/mod", p.modCache()).
		WithMountedCache("/root/.cache/go-build", p.buildCache()).
		WithExec([]string{"apk", "add", "--no-cache", "git", "ca-certificates"})
	return p.withSources(ctr).WithExec([]string{"go", "mod", "download"})
}

func (p *pipeline) unit(ctx context.Context) error {
	_, err := p.goBase().
		WithEnvVariable("CGO_ENABLED", "1").
		WithExec([]string{"apk", "add", "--no-cache", "gcc", "musl-dev"}).
		WithExec([]string{"go", "test", "-race", "-count=1", "./..."}).
		Sync(ctx)
	return err
}

func (p *pipeline) lint(ctx context.Context) error {
	if _, err := p.goBase().WithExec([]string{"go", "vet", "./..."}).Sync(ctx); err != nil {
		return err
	}
	ctr := p.c.Container().From(LintImage).
		WithMountedCache("/go/pkg/mod", p.modCache()).
		WithMountedCache("/root/.cache/go-build", p.buildCache()).
		WithMountedCache("/root/.cache/golangci-lint", p.lintCache())
	_, err := p.withSources(ctr).
		WithExec([]string{"golangci-lint", "run", "--timeout=5m", "./..."}).
		Sync(ctx)
	return err
}

func (p *pipeline) vuln(ctx context.Context) error {
	_, err := p.goBase().
		WithExec([]string{"go", "install", "golang.org/x/vuln/cmd/govulncheck@" + GovulncheckVersion}).
		WithExec([]string{"sh", "-c", "/go/bin/govulncheck ./..."}).
		Sync(ctx)
	return err
}

// tidy verifies that `go mod tidy` produces no diff against the committed
// go.mod once the local replace is dropped again. go.sum is not diffed: a
// local-path replace legitimately pulls transitive hashes into go.sum that
// the committed file does not carry.
func (p *pipeline) tidy(ctx context.Context) error {
	ctr := p.c.Container().From(GoImage).
		WithMountedCache("/go/pkg/mod", p.modCache()).
		WithDirectory("/src", p.repoSrc()).
		WithWorkdir("/src").
		WithExec([]string{"apk", "add", "--no-cache", "git", "diffutils"}).
		WithExec([]string{"sh", "-c", "cp go.mod go.mod.orig"})
	if !p.cfg.NoCommon {
		ctr = ctr.WithDirectory("/common", p.commonSrc()).
			WithExec([]string{"go", "mod", "edit", "-replace", p.cfg.CommonModule + "=/common"})
	}
	ctr = ctr.WithExec([]string{"go", "mod", "tidy"})
	if !p.cfg.NoCommon {
		ctr = ctr.WithExec([]string{"go", "mod", "edit", "-dropreplace", p.cfg.CommonModule})
	}
	_, err := ctr.WithExec([]string{"sh", "-c", "diff -u go.mod.orig go.mod"}).Sync(ctx)
	return err
}

func (p *pipeline) build(ctx context.Context) error {
	args := append([]string{"go", "build", "-buildvcs=false"}, p.cfg.BuildTargets...)
	_, err := p.goBase().WithExec(args).Sync(ctx)
	return err
}

// image builds the runtime OCI image from the repo Dockerfile, then optionally
// exports it as a tarball or publishes it. The Dockerfile resolves the shared
// module from the module proxy at its committed version, never the sibling, so
// a published image cannot depend on an untagged working tree.
func (p *pipeline) image(ctx context.Context, address, exportPath string) error {
	img := p.repoSrc().DockerBuild(dagger.DirectoryDockerBuildOpts{
		Dockerfile: "Dockerfile",
		BuildArgs:  []dagger.BuildArg{{Name: "VERSION", Value: p.version}},
	})

	if address != "" {
		ref, err := img.Publish(ctx, address)
		if err != nil {
			return fmt.Errorf("publish: %w", err)
		}
		fmt.Println("published:", ref)
		return nil
	}

	if exportPath != "" {
		if err := os.MkdirAll(filepath.Dir(exportPath), 0o755); err != nil {
			return err
		}
		if _, err := img.AsTarball().Export(ctx, exportPath); err != nil {
			return fmt.Errorf("export: %w", err)
		}
		fmt.Println("exported:", exportPath)
		return nil
	}

	_, err := img.Sync(ctx)
	return err
}

func (p *pipeline) devShell(ctx context.Context) error {
	_, err := p.goBase().
		WithExec([]string{"apk", "add", "--no-cache", "bash"}).
		Terminal(dagger.ContainerTerminalOpts{Cmd: []string{"bash"}}).
		Sync(ctx)
	return err
}
