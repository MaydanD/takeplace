# =============================================================================
# Takeplace frontend image (development).
#
# Runs the Vite dev server. Production builds static assets and serves them from
# Caddy (PROJECT-SPEC §3.6); that image is added in the deploy stage.
# =============================================================================
FROM node:22-alpine

WORKDIR /app

# Install dependencies first for layer caching. `npm ci` requires a lockfile,
# which is committed; fall back to `npm install` only if it is missing.
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
