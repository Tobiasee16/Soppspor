#!/usr/bin/env bash
# Build and publish the hosted app.
#
#   ./deploy.sh              build, then tell you how to upload
#   ./deploy.sh --cloudflare build and push to Cloudflare Pages
#   ./deploy.sh --netlify    build and push to Netlify
#
# Builds **Norway** by default. This used to build `dist/` unconditionally,
# which is the Kristiansand-only map: deploying it for a tester in Oslo gives
# them a country with no zones in it and no error to explain why. The build is
# named out loud below for the same reason.
#
#   SOPP_BUILD=kommune ./deploy.sh --cloudflare    the older Kristiansand build
#
# There is no CI and no secret in this file. It runs the same commands you would
# run by hand, in the order that matters, and stops on the first failure.
set -euo pipefail
cd "$(dirname "$0")"

BUILD="${SOPP_BUILD:-norge}"
case "$BUILD" in
  norge)
    FLAG=(--national); OUT="dist_norge"; WHAT="Norway, tiled zones" ;;
  kommune)
    FLAG=();           OUT="dist";       WHAT="Kristiansand only" ;;
  *)
    echo "SOPP_BUILD must be 'norge' or 'kommune', got: $BUILD" >&2; exit 2 ;;
esac

echo "==> refreshing the weather (skips if seNorge has not moved)"
python scripts/fetch_recent_weather.py --if-new

echo "==> building $WHAT  ->  $OUT/"
python scripts/build_hosted.py "${FLAG[@]}"

echo "==> checking the bundle"
node tests/test_hosted_build.js "$OUT"

# The one line worth reading before pushing. A build that says Kristiansand
# when you meant Norway is not going to announce itself any other way.
AREA="$(python - "$OUT" <<'PY'
import io, re, sys
s = io.open(sys.argv[1] + "/index.html", encoding="utf-8").read()
m = re.search(r'"area_label":"([^"]*)"', s)
print(m.group(1) if m else "(no area label in the payload)")
PY
)"
echo "==> this build covers: $AREA"

case "${1:-}" in
  --cloudflare)
    # The project name decides the free address, <name>.pages.dev, and naming a
    # project that does not exist creates it. Override it when you mean a
    # different one:
    #   PAGES_PROJECT=whatever ./deploy.sh --cloudflare
    PROJECT="${PAGES_PROJECT:-soppspor}"
    BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
    echo "==> Cloudflare Pages (project: $PROJECT, branch: $BRANCH)"
    echo "    this will publish to https://$PROJECT.pages.dev"
    # The project must already exist - `pages deploy` will not create one:
    #   npx wrangler pages project create "$PROJECT" --production-branch main --force
    # (--force only on the create, and only once; wrangler says so itself.)
    #
    # And its exit code is not evidence. It has been seen to print "The Pages
    # project does not exist", deploy nothing, and return 0 - which `set -e`
    # cannot catch and this script would have reported as a success. That is
    # why the site is fetched and inspected below rather than trusted.
    npx --yes wrangler pages deploy "$OUT" --project-name "$PROJECT"         --branch "$BRANCH" --commit-dirty=true
    echo "==> checking what is actually live"
    python scripts/check_deployed.py "https://$PROJECT.pages.dev" "$OUT"
    ;;
  --netlify)
    echo "==> Netlify"
    npx --yes netlify-cli deploy --dir "$OUT" --prod
    echo
    echo "Now check it, with the address Netlify printed:"
    echo "  python scripts/check_deployed.py <url> $OUT"
    ;;
  "")
    cat <<MSG

Built. $OUT/ is a complete static site - upload it anywhere that serves files
over HTTPS. HTTPS is not optional: geolocation and service workers both refuse
to run without it, so on plain HTTP the app silently loses GPS and offline.

  ./deploy.sh --cloudflare     unlimited bandwidth on the free plan, reads _headers
  ./deploy.sh --netlify        also reads _headers
  python scripts/serve_interface.py --dist --https   test on your phone first

After the first deploy, three things:
  * Supabase -> Authentication -> URL Configuration: add the deployed origin to
    Redirect URLs, or magic links will bounce to localhost:3000.
  * Run backend/migrations/004_schedule_tombstone_purge.sql. The privacy policy
    says a deleted spot is gone within 30 days; nothing removes it until that
    job is scheduled.
  * Delete any older Pages project still serving the previous build. A stale
    public copy under the old name, with no privacy policy, is worse than no
    copy at all.
MSG
    ;;
  *)
    echo "unknown option: $1" >&2; exit 2 ;;
esac
