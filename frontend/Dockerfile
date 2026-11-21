# Node 22 (Node 20 is end-of-life: no more security fixes) and `npm ci`:
# installs EXACTLY package-lock.json, never a newer release than the lock.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# Static export runner — serves the out/ directory produced by next build
FROM python:3.13-slim AS runner
WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY --from=builder /app/out ./out
COPY serve.py ./serve.py

EXPOSE 3787

CMD ["python3", "serve.py", "out", "3787"]
