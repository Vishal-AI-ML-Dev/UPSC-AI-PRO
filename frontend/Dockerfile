# syntax=docker/dockerfile:1

# ============================================================================
# Stage 1 - build the SPA with Vite
# ============================================================================
FROM node:20-bookworm-slim AS build
WORKDIR /app

# Vite inlines VITE_* variables at build time. Override at build with:
#   docker build --build-arg VITE_API_BASE=https://your-api/api/v1 .
ARG VITE_API_BASE=http://localhost:8000/api/v1
ENV VITE_API_BASE=${VITE_API_BASE}

# Install dependencies first for better layer caching.
COPY package.json package-lock.json* ./
RUN npm ci || npm install

COPY . .
RUN npm run build

# ============================================================================
# Stage 2 - serve the static build with nginx
# ============================================================================
FROM nginx:1.27-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
