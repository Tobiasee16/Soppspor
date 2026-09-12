#!/usr/bin/env bash
# Build and publish the hosted app.
#
#   ./deploy.sh              build only, then tell you how to upload
#   ./deploy.sh --cloudflare build and push to Cloudflare Pages
#   ./deploy.sh --netlify    build and push to Netlify
#
# There is no CI and no secret in this file. It runs the same commands you would
# run by hand, in the order that matters, and stops on the first failure.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> refreshing the weather (skips if seNorge has not moved)"
python scripts/fetch_recent_weather.py --if-new

echo "==> building"
python scripts/build_hosted.py

echo "==> checking the bundle"
node tests/test_hosted_build.js

case "${1:-}" in
  --cloudflare)
    # The project name decides the free address, <name>.pages.dev. It is not a
    # commitment: the app's real name lives on a custom domain later, and
    # changing this means creating a second Pages project and deploying again,
    # which costs nothing. Override it while the name is undecided:
    #   PAGES_PROJECT=whatever ./deploy.sh --cloudflare
    echo "==> Cloudflare Pages (project: ${PAGES_PROJECT:-soppkart})"
    npx --yes wrangler pages deploy dist --project-name "${PAGES_PROJECT:-soppkart}"
    ;;
  --netlify)
    echo "==> Netlify"
    npx --yes netlify-cli deploy --dir dist --prod
    ;;
  "")
    cat <<'MSG'

Built. dist/ is a complete static site - upload it anywhere that serves files
over HTTPS. HTTPS is not optional: geolocation and service workers both refuse
to run without it, so on plain HTTP the app silently loses GPS and offline.

  ./deploy.sh --cloudflare     unlimited bandwidth on the free plan, reads _headers
  ./deploy.sh --netlify        also reads _headers
  python scripts/serve_interface.py --dist --https   test on your phone first

After the first deploy, two things in the Supabase dashboard:
  * Authentication -> URL Configuration: add the deployed origin to Redirect URLs,
    or magic links will bounce to localhost:3000.
  * Check Settings -> General -> Region. It cannot be changed later without
    migrating every row, and right now that migration is two test accounts.
MSG
    ;;
  *)
    echo "unknown option: $1" >&2; exit 2 ;;
esac
