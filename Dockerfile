# PanWatch Dockerfile
# 多阶段构建，减小最终镜像大小

# ===== Stage 1: 前端构建 =====
FROM node:24.14.0-alpine AS frontend-builder

WORKDIR /app/frontend

# 启用并固定 pnpm，避免镜像构建时随 npm 全局安装漂移
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate

# 复制依赖文件
COPY frontend/package.json frontend/pnpm-lock.yaml ./

# 安装依赖
RUN pnpm install --frozen-lockfile --ignore-scripts

# 复制源码并构建
COPY frontend/ ./
RUN pnpm build


# ===== Stage 2: Python 运行环境 =====
FROM python:3.11-slim

# 版本号（构建时传入）
ARG VERSION=dev

WORKDIR /app

# System packages
# - tzdata: zoneinfo data
# - git: requirements.txt pulls tradingagents from a git+https URL
# - fonts-noto-cjk + pango/cairo/gdk-pixbuf/glib/fontconfig: WeasyPrint (PDF export) and
#   matplotlib font rendering. No browser: charts are rendered from PanWatch's own bars.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    git \
    fonts-noto-cjk \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libcairo-gobject2 \
    libgdk-pixbuf-2.0-0 \
    libglib2.0-0 \
    libfontconfig1 \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -fv

# 复制依赖文件
COPY requirements.txt ./

# 复制本仓内本地包(requirements.txt 里 -e ./packages/marketdata 需要它先在)
COPY packages/ ./packages/

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制后端代码
COPY src/ ./src/
COPY server.py ./
COPY LICENSE NOTICE ./
COPY prompts/ ./prompts/

# 写入版本号
RUN echo "${VERSION}" > VERSION

# 从前端构建阶段复制静态文件
COPY --from=frontend-builder /app/frontend/dist ./static/

# 创建数据目录
RUN mkdir -p /app/data

# 环境变量
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data
ENV DOCKER=1

# Default timezone (override with -e TZ=... at docker run)
ENV TZ=America/Vancouver

# 暴露端口（保持 8000 不变，避免影响存量用户升级）
EXPOSE 8000

# 健康检查（使用 Python）
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# 启动命令
CMD ["python", "server.py"]
