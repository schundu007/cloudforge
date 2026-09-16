#!/usr/bin/env bash
#
# Finish the CloudForge deploy: set the three secrets, redeploy both halves,
# and verify the auth gate. Run from the repo root:
#
#   ./scripts/finish-deploy.sh
#
# Secrets are read from your local .env or prompted for; they are printed only
# where you need to copy them (the API token), never logged elsewhere.
set -euo pipefail

API_URL="https://cloudforge-api-production.up.railway.app"
SITE_URL="https://forc.cariara.com"
FRONTEND_DIR="cloudforge/interfaces/web/frontend"

cd "$(dirname "$0")/.."

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$1" >&2; exit 1; }

[ -f .env ] || die ".env not found — run from the repo root."
command -v railway >/dev/null || die "railway CLI not found."
command -v vercel  >/dev/null || die "vercel CLI not found."

bold "1. Reading ANTHROPIC_API_KEY from .env"
ANTHROPIC_KEY="$(grep -E '^ANTHROPIC_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"'"'"' ' || true)"
[ -n "$ANTHROPIC_KEY" ] || die "ANTHROPIC_API_KEY missing from .env"
ok "found (${#ANTHROPIC_KEY} chars)"

bold "2. GitHub token"
GH_TOKEN=""
if command -v gh >/dev/null && gh auth token >/dev/null 2>&1; then
  CANDIDATE="$(gh auth token 2>/dev/null || true)"
  if [ -n "$CANDIDATE" ] && \
     [ "$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $CANDIDATE" https://api.github.com/user)" = "200" ]; then
    GH_TOKEN="$CANDIDATE"
    ok "using valid token from gh CLI"
  fi
fi
if [ -z "$GH_TOKEN" ]; then
  warn "No valid gh token. Create one with 'repo' scope: https://github.com/settings/tokens"
  read -rsp "  Paste GitHub token (blank to skip PR support): " GH_TOKEN
  echo
fi
if [ -n "$GH_TOKEN" ]; then
  code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $GH_TOKEN" https://api.github.com/user)"
  [ "$code" = "200" ] || die "GitHub rejected that token (HTTP $code)."
  ok "token validated against api.github.com"
else
  warn "skipping GITHUB_TOKEN — 'Accept & open PR' will return 502 until it is set"
fi

bold "3. Generating shared API token"
CF_TOKEN="$(openssl rand -hex 32)"
ok "generated (64 hex chars)"

bold "4. Setting Railway variables"
railway service cloudforge-api >/dev/null 2>&1 || true
railway variables --set "ANTHROPIC_API_KEY=$ANTHROPIC_KEY" --skip-deploys >/dev/null
railway variables --set "CLOUDFORGE_API_TOKEN=$CF_TOKEN"   --skip-deploys >/dev/null
if [ -n "$GH_TOKEN" ]; then
  railway variables --set "GITHUB_TOKEN=$GH_TOKEN" --skip-deploys >/dev/null
fi
ok "ANTHROPIC_API_KEY, CLOUDFORGE_API_TOKEN${GH_TOKEN:+, GITHUB_TOKEN} set"

bold "5. Setting Vercel VITE_API_TOKEN (baked into the bundle at build time)"
(cd "$FRONTEND_DIR" && vercel env rm VITE_API_TOKEN production --yes >/dev/null 2>&1 || true)
(cd "$FRONTEND_DIR" && printf '%s' "$CF_TOKEN" | vercel env add VITE_API_TOKEN production >/dev/null)
ok "VITE_API_TOKEN set on production"

bold "6. Redeploying backend"
railway redeploy --yes >/dev/null 2>&1 || railway up --detach >/dev/null
ok "backend redeploy triggered"

printf '  waiting for API'
for _ in $(seq 1 60); do
  if [ "$(curl -s -o /dev/null -w '%{http_code}' -m 5 "$API_URL/health")" = "200" ]; then break; fi
  printf '.'; sleep 5
done
echo

bold "7. Rebuilding dashboard with the token"
(cd "$FRONTEND_DIR" && vercel deploy --prod --yes >/dev/null)
ok "dashboard redeployed"

bold "8. Verifying"
unauth="$(curl -s -o /dev/null -w '%{http_code}' -m 15 -X POST "$API_URL/runs" \
  -H 'Content-Type: application/json' -d '{"intent":"probe","repo_root":"/app"}')"
if [ "$unauth" = "401" ]; then
  ok "gate ON — unauthenticated POST /runs => 401"
else
  warn "expected 401 without a token, got $unauth"
fi

authed="$(curl -s -o /dev/null -w '%{http_code}' -m 15 "$API_URL/runs/none" \
  -H "Authorization: Bearer $CF_TOKEN")"
if [ "$authed" = "404" ]; then
  ok "token accepted — authenticated request passes the gate"
else
  warn "expected 404 with a token, got $authed"
fi

site="$(curl -s -o /dev/null -w '%{http_code}' -m 20 "$SITE_URL")"
if [ "$site" = "200" ]; then
  ok "$SITE_URL serving (HTTP 200)"
else
  warn "site returned $site"
fi

echo
bold "Done."
echo "  CLOUDFORGE_API_TOKEN = $CF_TOKEN"
echo "  (stored on Railway and baked into the dashboard; save it to call the API directly)"
