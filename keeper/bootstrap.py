#!/usr/bin/env python3
"""Convert a MULTIDEX git clone into a generation-layout
install, in place beside the clone:

    python3 keeper/bootstrap.py <install-root> [--clone <path>]

The layout the keeper and the updater work with:

    <install-root>/
      repo/                 the clone: fetched from, never run
      releases/<hash>/      one immutable tree per release
      current               symlink to the current release
      keeper/               this frozen base, copied out of the
                            clone so generations never carry it

The clone is the source: --clone names it, or the install
root's own repo/ is used, or the install root itself when it
is a clone. The current generation is materialised from the
clone's HEAD and verified file by file against its commit.
No home is touched: the pointer is written by the keeper on
its first start, from the current symlink.

Bootstrap is a one-time, operator-run step. It is part of
the frozen base: it ships in keeper/ and updates manually,
like the keeper itself.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile


def git(repo, *args):
    proc = subprocess.run(["git", "-C", repo] + list(args),
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("git %s failed: %s"
                         % (args[0], proc.stderr.strip()))
    return proc.stdout.strip()


def materialise(repo, root, gen_hash):
    dest = os.path.join(root, "releases", gen_hash)
    if os.path.isdir(dest):
        return dest
    tmp = os.path.join(root, "releases", ".tmp-" + gen_hash)
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    proc = subprocess.Popen(["git", "-C", repo, "archive",
                             "--format=tar", gen_hash],
                            stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            for member in tar:
                if os.path.isabs(member.name) or ".." in \
                        member.name.split("/"):
                    raise SystemExit(
                        "archive member escapes the tree: %s"
                        % member.name)
                tar.extract(member, tmp)
    finally:
        proc.stdout.close()
        proc.wait()
    if proc.returncode != 0:
        raise SystemExit("git archive of %s failed" % gen_hash)
    expected = {}
    for entry in git(repo, "ls-tree", "-r", "-z",
                     gen_hash).split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        expected[path] = meta.split(" ")[2]
    for dirpath, _dirs, files in os.walk(tmp):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, tmp)
            if os.path.islink(full):
                content = os.readlink(full).encode()
            else:
                with open(full, "rb") as fh:
                    content = fh.read()
            digest = hashlib.sha1(
                b"blob %d\0" % len(content) + content).hexdigest()
            if expected.get(rel) != digest:
                raise SystemExit(
                    "materialised tree does not verify: %s"
                    % rel)
    os.replace(tmp, dest)
    return dest


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    clone = None
    positional = []
    i = 0
    while i < len(argv):
        if argv[i] == "--clone":
            i += 1
            clone = argv[i]
        else:
            positional.append(argv[i])
        i += 1
    if len(positional) != 1:
        sys.stderr.write(
            "usage: bootstrap.py <install-root> "
            "[--clone <path>]\n")
        return 2
    root = os.path.abspath(positional[0])
    os.makedirs(os.path.join(root, "releases"), exist_ok=True)
    repo = os.path.join(root, "repo")
    if clone is None and os.path.exists(os.path.join(root,
                                                     ".git")):
        clone = root
    if not os.path.exists(os.path.join(repo, ".git")):
        if clone is None:
            sys.stderr.write(
                "no clone found: pass --clone <path to a "
                "MULTIDEX clone>, or run inside one\n")
            return 2
        origin = None
        proc = subprocess.run(
            ["git", "-C", clone, "remote", "get-url", "origin"],
            capture_output=True, text=True)
        if proc.returncode == 0:
            origin = proc.stdout.strip()
        subprocess.run(["git", "clone", "--quiet", clone, repo],
                       check=True)
        if origin:
            subprocess.run(["git", "-C", repo, "remote",
                            "set-url", "origin", origin],
                           check=True)
    head = git(repo, "rev-parse", "HEAD")
    src_keeper = os.path.join(repo, "keeper")
    if not os.path.isdir(src_keeper):
        sys.stderr.write(
            "the clone at %s carries no keeper/ directory; "
            "update the clone to a release that ships the "
            "keeper first\n" % repo)
        return 2
    dst_keeper = os.path.join(root, "keeper")
    if os.path.abspath(src_keeper) != dst_keeper:
        shutil.rmtree(dst_keeper, ignore_errors=True)
        shutil.copytree(src_keeper, dst_keeper)
    dest = materialise(repo, root, head)
    link = os.path.join(root, "current")
    tmp = os.path.join(root, ".current.tmp")
    try:
        os.remove(tmp)
    except OSError:
        pass
    os.symlink(os.path.join("releases", head), tmp)
    os.replace(tmp, link)
    print("generation layout ready at %s" % root)
    print("current generation: %s (%s)" % (head, dest))
    print("next steps:")
    print("  1. place the release signers' public keys in the "
          "home as allowed-signers (one 'principal key' line "
          "per signer, the git SSH signing format)")
    print("  2. start the keeper: %s --home <home>"
          % os.path.join(dst_keeper, "multidex-keeper"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
