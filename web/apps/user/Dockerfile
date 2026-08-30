FROM node:22-alpine AS build
WORKDIR /workspace
COPY package.json package-lock.json* ./
COPY apps/user/package.json apps/user/package.json
COPY apps/admin/package.json apps/admin/package.json
COPY packages/shared/package.json packages/shared/package.json
RUN npm install --workspaces --include-workspace-root
COPY tsconfig.base.json ./
COPY packages/shared packages/shared
COPY apps/user apps/user
RUN npm run build:user

FROM nginx:1.27-alpine
COPY apps/user/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /workspace/apps/user/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 CMD wget -qO- http://127.0.0.1/healthz || exit 1
