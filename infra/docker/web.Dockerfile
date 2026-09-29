# The SPA, served by nginx, which also reverse-proxies /v1 to the API (same origin: cookies + SSE).
FROM node:22-bookworm-slim AS build
ENV PNPM_HOME=/pnpm PATH=/pnpm:$PATH
RUN corepack enable
WORKDIR /repo
COPY pnpm-lock.yaml pnpm-workspace.yaml package.json tsconfig.base.json ./
COPY packages/contracts/package.json packages/contracts/
COPY apps/web/package.json apps/web/
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile --filter @ci/web...
COPY packages/contracts packages/contracts
COPY apps/web apps/web
# "true" only for the local compose stack: compiles in the development sign-in picker.
ARG VITE_DEMO_SIGNIN=false
ENV VITE_DEMO_SIGNIN=$VITE_DEMO_SIGNIN
RUN pnpm --filter @ci/web build

FROM nginx:1.29-alpine AS runtime
COPY infra/docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY infra/docker/snippets/ /etc/nginx/snippets/
COPY --from=build /repo/apps/web/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/ >/dev/null || exit 1
