FROM node:24-alpine AS build

WORKDIR /workspace

COPY package.json package-lock.json ./
COPY apps/dashboard/package.json apps/dashboard/package.json
RUN npm ci

COPY apps/dashboard apps/dashboard
RUN npm run build --workspace @project-emerge/dashboard \
    && npm prune --omit=dev

FROM node:24-alpine AS runtime

ENV NODE_ENV=production \
    HTTP_PORT=8787 \
    FIRMWARE_DIRECTORY=/var/lib/project-emerge/firmware

WORKDIR /app

RUN mkdir -p /var/lib/project-emerge/firmware \
    && chown -R node:node /var/lib/project-emerge

COPY --from=build --chown=node:node /workspace/node_modules ./node_modules
COPY --from=build --chown=node:node /workspace/apps/dashboard/dist ./dist
COPY --from=build --chown=node:node /workspace/apps/dashboard/server-dist ./server-dist

USER node

EXPOSE 8787

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:8787/').then((response) => { if (!response.ok) process.exit(1); }).catch(() => process.exit(1));"]

CMD ["node", "server-dist/server/index.js"]
