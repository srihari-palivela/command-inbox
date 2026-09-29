# The platform console, served by nginx on its own origin, which reverse-proxies /v1/platform to the API.
# It shares the web app's UI kit at build time, so the web sources are copied in too.
FROM node:22-bookworm-slim AS build
ENV PNPM_HOME=/pnpm PATH=/pnpm:$PATH
RUN corepack enable
WORKDIR /repo
COPY pnpm-lock.yaml pnpm-workspace.yaml package.json tsconfig.base.json ./
COPY packages/contracts/package.json packages/contracts/
COPY apps/web/package.json apps/web/
COPY apps/console/package.json apps/console/
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile --filter @ci/console... --filter @ci/web...
COPY packages/contracts packages/contracts
COPY apps/web apps/web
COPY apps/console apps/console
RUN pnpm --filter @ci/console build

FROM nginx:1.29-alpine AS runtime
COPY infra/docker/console-nginx.conf /etc/nginx/conf.d/default.conf
COPY infra/docker/snippets/ /etc/nginx/snippets/
COPY --from=build /repo/apps/console/dist /usr/share/nginx/html
EXPOSE 8082
HEALTHCHECK --interval=10s --timeout=3s CMD wget -qO- http://127.0.0.1:8082/ >/dev/null || exit 1
