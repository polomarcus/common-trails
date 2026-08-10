FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json ./
RUN npm install

FROM node:20-alpine AS builder
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
