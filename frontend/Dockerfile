# syntax=docker/dockerfile:1.7
#
# Greenroom frontend image — Next.js 14 (App Router).
#
# Three build targets:
#   dev   — runs `next dev`; compose mounts the host source over /app
#           with an anonymous volume keeping /app/node_modules isolated
#   build — intermediate stage that runs `next build`
#   prod  — minimal runtime image that runs `next start`
#
# Build context is the `frontend/` directory.

# ---- deps ---------------------------------------------------------------
FROM node:20-alpine AS deps
WORKDIR /app

# Install alpine build deps used by some Node native modules (sharp, etc).
RUN apk add --no-cache libc6-compat

COPY package.json package-lock.json* ./
RUN npm ci

# ---- dev ----------------------------------------------------------------
# Host source is mounted over /app at runtime; node_modules stays from
# this stage via an anonymous volume in compose.
FROM node:20-alpine AS dev
WORKDIR /app
ENV NODE_ENV=development
COPY --from=deps /app/node_modules ./node_modules
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

# ---- build --------------------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app
ENV NODE_ENV=production
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ---- prod ---------------------------------------------------------------
FROM node:20-alpine AS prod
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000

COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/next.config.mjs ./next.config.mjs

EXPOSE 3000
CMD ["npm", "start"]
