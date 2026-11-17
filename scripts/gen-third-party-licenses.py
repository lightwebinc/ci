#!/usr/bin/env python3
"""Generate LICENSE-THIRD-PARTY for a Go repository, from what it actually links.

Why this exists rather than a hand-written file: nearly every licence a Go
binary pulls in requires its own text to travel with a BINARY distribution, not
only with source. Apache-2.0 section 4 requires a copy of the licence be given
to recipients of the Work; MIT requires the notice "in all copies or
substantial portions"; BSD-3 requires binary redistributions to reproduce the
notice; Open BSV requires its text and an attribution line. Static linking
discards every one of those files, so an image built from a Go binary carries
the code and none of the notices.

A hand-written list goes stale silently the first time a dependency is added.
This reads `go list -deps`, so it describes the artefact rather than someone's
memory of it.

Handles both a Go module and an npm package. The Go side reads `go list -deps`,
so a module in go.mod that nothing imports is correctly absent and an INDIRECT
dependency that IS imported is correctly present. The npm side reads
`npm ls --omit=dev --omit=peer --all`, which is the tree an install of the
package brings with it; a peer dependency is the installer's own.

A module's NOTICE file is reproduced after its licence texts. Section 4(d) of
the Apache License has the attribution notices in a NOTICE travel with the
code, and a binary drops that file as surely as it drops the licence.

This is the one copy. It lives in github.com/lightwebinc/ci, and a repository
runs it through a small scripts/gen-third-party-licenses.py that fetches it at
a pinned commit, so every repository generates its file the same way.

Usage:  gen-third-party-licenses.py <repo-dir> [--check]
        --check exits 1 if the file on disk differs from what would be written.
"""
import os
import subprocess
import sys

# Matched case-insensitively against the file's stem. Publishers use every
# spelling there is: LICENSE, LICENSE.md, license, license.md, COPYING. A
# case-sensitive list silently reports a package as having no licence when it
# ships one, which reads as an upstream failing rather than a bug here.
LICENSE_STEMS = ("license", "licence", "copying")
LICENSE_EXTS = ("", ".txt", ".md", ".rst")

# A package may ship its licences in a directory rather than a flat file, which
# is the REUSE layout and what the BSV packages use. The text still has to
# travel with a binary, so a directory counts.
LICENSE_DIRS = ("licenses", "licences")

# The attribution file Apache-2.0 section 4(d) speaks of. Looked for at the top
# of a package only, where the licence asks for it to be.
NOTICE_STEMS = ("notice",)


def run(args, cwd):
    env = dict(os.environ, GOWORK="off")
    out = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env)
    if out.returncode != 0:
        sys.exit(f"{' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


def own_module(repo):
    return run(["go", "list", "-m"], repo).strip()


def linked_modules(repo):
    """Modules whose code is actually reachable from this repo's packages.

    `go list -deps` walks imports, so a module in go.mod that nothing imports is
    correctly absent: it is not in the binary and carries no obligation. An
    INDIRECT dependency that is imported IS present, which is why go.mod's
    direct/indirect split is the wrong question.
    """
    mine = own_module(repo)
    pkgs = run(["go", "list", "-deps", "-f", "{{if .Module}}{{.Module.Path}}{{end}}", "./..."], repo)
    mods = {p for p in pkgs.split() if p and p != mine and not p.startswith(mine + "/")}
    return sorted(mods)


def module_dir(repo, mod):
    """Where the module's source sits in the module cache.

    An empty Dir means the module is in the graph but not downloaded, which is
    the normal state on a fresh CI checkout before anything has fetched it. Ask
    for it rather than failing: a licence check that only passes on a warm cache
    is a licence check that gets disabled.
    """
    d = run(["go", "list", "-m", "-f", "{{.Dir}}", mod], repo).strip()
    if not d:
        run(["go", "mod", "download", mod], repo)
        d = run(["go", "list", "-m", "-f", "{{.Dir}}", mod], repo).strip()
    return d or None


def license_files(d):
    """Every licence text a package ships, in a stable order.

    Sorted rather than in the order of a name list, so the generated file is
    reproducible whatever the filesystem hands back.
    """
    found = []
    try:
        entries = sorted(os.listdir(d))
    except OSError:
        return found
    for e in entries:
        p = os.path.join(d, e)
        low = e.lower()
        if os.path.isdir(p):
            if low in LICENSE_DIRS:
                found.extend(
                    os.path.join(p, f)
                    for f in sorted(os.listdir(p))
                    if os.path.isfile(os.path.join(p, f))
                )
            continue
        stem, ext = os.path.splitext(low)
        if stem in LICENSE_STEMS and ext in LICENSE_EXTS:
            found.append(p)
    return found


def notice_files(d):
    """The NOTICE files a package ships at its top level, in a stable order."""
    found = []
    try:
        entries = sorted(os.listdir(d))
    except OSError:
        return found
    for e in entries:
        p = os.path.join(d, e)
        stem, ext = os.path.splitext(e.lower())
        if os.path.isfile(p) and stem in NOTICE_STEMS and ext in LICENSE_EXTS:
            found.append(p)
    return found


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().rstrip()


def body(name, d, files):
    """What is reproduced for one dependency: its licence texts, then any
    NOTICE it ships, under a rule that says which module the notice is from."""
    texts = [read(f) for f in files]
    parts = list(texts)
    for f in notice_files(d):
        head = f"NOTICE shipped with {name}"
        parts.append("-" * 80 + "\n" + head + "\n" + "-" * 80 + "\n\n" + read(f))
    return detect(texts), "\n\n".join(parts)


# A licence names itself in the first lines of its own text, so a name is
# looked for there. Anywhere else a name may be a passing mention, so the
# rest of a text is searched for a licence's own operative wording instead.
TITLES = (
    ("open bsv license version 6", "Open BSV License Version 6"),
    ("open bsv license version 5", "Open BSV License Version 5"),
    ("open bsv license version 4", "Open BSV License Version 4"),
    ("apache license", "Apache License 2.0"),
    ("mit license", "MIT License"),
    ("isc license", "ISC License"),
    ("mozilla public license", "Mozilla Public License 2.0"),
)
TERMS = (
    ("licensed under the apache license, version 2.0", "Apache License 2.0"),
    ("apache license version 2.0, january 2004", "Apache License 2.0"),
    ("redistribution and use in source and binary forms", "BSD License"),
    ("permission is hereby granted, free of charge", "MIT License"),
)


def detect(texts):
    """Every licence the texts carry, in the order each first appears.

    One file may carry several: a project whose files fall under two
    licences, or a licence that reproduces the one the code it is based on
    was released under. Naming only the first would understate what the
    file obliges a distributor to do.
    """
    found = {}
    offset = 0
    for text in texts:
        # Whitespace is collapsed so wording wrapped across lines matches.
        flat = " ".join(text.lower().split())
        head = " ".join("\n".join(text.splitlines()[:6]).lower().split())
        for needles, where in ((TITLES, head), (TERMS, flat)):
            for needle, label in needles:
                i = where.find(needle)
                if i >= 0 and (label not in found or offset + i < found[label]):
                    found[label] = offset + i
        offset += len(flat) + 1
    if not found:
        return "see text below"
    return ", ".join(sorted(found, key=found.get))


def npm_packages(repo):
    """The production dependency tree, which is what a production image holds.

    `tsc` does not bundle: the built JavaScript resolves bare imports against
    node_modules at runtime, so every package here is physically present in the
    image and its licence travels only if it is written down.

    Peer dependencies are left out. A peer is not installed by this package but
    supplied by whoever installs it, as their own copy under their own
    obligation, and npm 7+ would otherwise list it as though this package
    brought it.
    """
    import json as _json

    # NPM_LS_JSON lets a caller supply the tree from elsewhere, for the common
    # case where node lives in a container and this script runs on the host.
    supplied = os.environ.get("NPM_LS_JSON")
    if supplied:
        raw = open(supplied, encoding="utf-8").read()
    else:
        out = subprocess.run(
            ["npm", "ls", "--omit=dev", "--omit=peer", "--all", "--json"],
            cwd=repo, capture_output=True, text=True,
        )
        raw = out.stdout
    try:
        tree = _json.loads(raw)
    except ValueError:
        sys.exit("npm ls produced no JSON; run `npm ci` first, or set NPM_LS_JSON")
    seen = {}

    def walk(node):
        for name, info in (node.get("dependencies") or {}).items():
            key = (name, info.get("version") or "")
            if key in seen:
                continue
            seen[key] = True
            walk(info)

    walk(tree)
    return sorted(seen)


def npm_build(repo, repo_name):
    entries = []
    for name, version in npm_packages(repo):
        d = os.path.join(repo, "node_modules", *name.split("/"))
        if not os.path.isdir(d):
            sys.exit(f"{name}: not installed at {d}; run `npm ci` first")
        files = license_files(d)
        if not files:
            sys.exit(f"{name}: no licence file in {d}. Resolve by hand before shipping.")
        label, text = body(f"{name}@{version}", d, files)
        entries.append((f"{name}@{version}", label, text))
    return entries


def title(repo, npm):
    """The name the file is titled with, taken from what the repository
    declares rather than from where it is checked out: the last element of the
    Go module path, or the unscoped npm package name. A clone into a folder of
    any other name then checks against the same file. A trailing /vN element
    is skipped, because it names the version, not the module.
    """
    if npm:
        import json as _json

        with open(os.path.join(repo, "package.json"), encoding="utf-8") as f:
            name = _json.load(f).get("name") or ""
        name = name.rsplit("/", 1)[-1]
    else:
        parts = own_module(repo).split("/")
        if len(parts) > 1 and parts[-1][:1] == "v" and parts[-1][1:].isdigit():
            parts.pop()
        name = parts[-1]
    if not name:
        sys.exit(f"{repo}: no module or package name to title the file with")
    return name


def build(repo):
    has_docker = os.path.exists(os.path.join(repo, "Dockerfile"))
    # A repository that builds nothing runnable is a library: what is true of
    # it is what a binary or an install built WITH it must carry. That is
    # decided by what it builds, not by whether it has a release workflow,
    # because a release that only publishes notes ships no artefact.
    if os.path.exists(os.path.join(repo, "package.json")) and not os.path.exists(os.path.join(repo, "go.mod")):
        import json as _json

        with open(os.path.join(repo, "package.json"), encoding="utf-8") as f:
            pkg = _json.load(f)
        library = not has_docker and "bin" not in pkg and not pkg.get("private", False)
        repo_name = title(repo, npm=True)
        return render(repo_name, npm_build(repo, repo_name), "npm", has_docker, library=library)
    names = subprocess.run(
        ["go", "list", "-f", "{{.Name}}", "./..."],
        cwd=repo, capture_output=True, text=True, check=True,
        env={**os.environ, "GOWORK": "off"},
    ).stdout.split()
    library = not has_docker and "main" not in names
    repo_name = title(repo, npm=False)
    return render(repo_name, go_build(repo), "go", has_docker, library=library)


def go_build(repo):
    mods = linked_modules(repo)
    entries = []
    for m in mods:
        d = module_dir(repo, m)
        if not d:
            sys.exit(f"{m}: no module directory; run `go mod download` first")
        files = license_files(d)
        if not files:
            sys.exit(f"{m}: no licence file found in {d}. Resolve by hand before shipping.")
        label, text = body(m, d, files)
        entries.append((m, label, text))
    return entries


def render(repo_name, entries, kind, has_dockerfile=False, library=False):
    out = []
    out.append(f"Third-party licences for {repo_name}")
    out.append("=" * (len(repo_name) + 24))
    out.append("")
    out.append("GENERATED. Do not edit by hand.")
    out.append("")
    out.append("  scripts/gen-third-party-licenses.py <this repo>")
    out.append("")
    if kind == "go" and library:
        out.append("Every module below is reachable from this library's packages, so its")
        out.append("code is inside any binary built with them. Nearly all of these licences")
        out.append("require their own text to travel with a BINARY distribution and not")
        out.append("only with source, and static linking discards the files they live in.")
        out.append("That is why this file exists. This repository ships no binary or image")
        out.append("of its own; any binary built with this library must carry this file")
        out.append("(see NOTICE).")
    elif kind == "go":
        out.append("Every module below is reachable from this repository's own packages, so")
        out.append("its code is inside any binary built here and inside any image carrying")
        out.append("one. Nearly all of these licences require their own text to travel with")
        out.append("a BINARY distribution and not only with source, and static linking")
        out.append("discards the files they live in. That is why this file exists.")
        if has_dockerfile:
            out.append("The Dockerfile copies it into the image.")
        else:
            # Asserting a Dockerfile that is not there is the same class of
            # error this file exists to prevent, one level up.
            out.append("Any release artefact or image built from this repository must")
            out.append("carry it, and the release and image workflows both do.")
    elif library:
        out.append("Every package below is in this package's production dependency tree,")
        out.append("so an install of the package installs it too, whatever the installer")
        out.append("builds with it. Peer dependencies are not listed: a peer is supplied by")
        out.append("whoever installs this package, as their own copy under its own licence")
        out.append("(see NOTICE). This repository ships no image of its own.")
    else:
        out.append("Every package below is in the production dependency tree, so it is")
        out.append("physically present in any image built here: the compiler does not")
        out.append("bundle, and the built JavaScript resolves these imports at runtime.")
        out.append("Their own licence files do travel inside node_modules, which a Go")
        out.append("binary's do not, but this file states the set in one place and covers")
        out.append("any distribution that is not the whole tree.")
        if has_dockerfile:
            out.append("The Dockerfile copies it into the image.")
    out.append("")
    out.append("This repository's own source is Apache-2.0 and is in LICENSE, not here.")
    out.append("")
    out.append("Contents:")
    if not entries:
        # Said outright, so that an empty list reads as a result rather than
        # as a generator that found nothing to look at.
        out.append("  none: nothing is linked or installed beyond this repository's own source")
    for i, (m, label, _) in enumerate(entries, 1):
        out.append(f"  {i:2d}. {m}  ({label})")
    out.append("")
    for i, (m, label, text) in enumerate(entries, 1):
        out.append("=" * 80)
        out.append(f"{i}. {m}")
        out.append(f"   {label}")
        out.append("=" * 80)
        out.append("")
        out.append(text)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    repo = os.path.abspath(sys.argv[1])
    check = "--check" in sys.argv[2:]
    content = build(repo)
    target = os.path.join(repo, "LICENSE-THIRD-PARTY")
    if check:
        current = open(target, encoding="utf-8").read() if os.path.exists(target) else ""
        if current != content:
            sys.exit(f"{target} is stale: regenerate with scripts/gen-third-party-licenses.py")
        print(f"{target} is current")
        return
    with open(target, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"wrote {target} ({len(content.splitlines())} lines, {content.count(chr(61)*80)} licences)")


if __name__ == "__main__":
    main()
