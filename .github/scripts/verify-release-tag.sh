#!/usr/bin/env bash
# CI: refuses to publish anything from a tag that is not a dont-dry release, the same check
# deploy/install.sh makes on servers: the tag is signed by a key in RELEASE_SIGNERS and names
# itself with that same version. The keys come from master's deploy/install.sh, never from the
# tag's own commit, which whoever made the tag controls.
#
#   verify-release-tag.sh TAG      (in a checkout whose origin is the repository)
set -euo pipefail

TAG="$1"
ALLOWED="$(mktemp)"
trap 'rm -f "$ALLOWED"' EXIT

git fetch -q --depth 1 origin "+refs/heads/master:refs/remotes/origin/master" \
  "+refs/tags/$TAG:refs/tags/$TAG"
git show refs/remotes/origin/master:deploy/install.sh |
  sed -n "s/^RELEASE_SIGNERS='\(.*\)'\$/\1/p" > "$ALLOWED"
[ -s "$ALLOWED" ] || { echo "no RELEASE_SIGNERS on master" >&2; exit 1; }
[ "$(git cat-file tag "refs/tags/$TAG" | sed -n 's/^tag //p')" = "$TAG" ] ||
  { echo "$TAG is not an annotated tag that names itself $TAG" >&2; exit 1; }
git -c gpg.ssh.allowedSignersFile="$ALLOWED" verify-tag "refs/tags/$TAG"
