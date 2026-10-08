# A股缠论分析系统 运行镜像
# 构建上下文为仓库根目录(见 docker-compose.yml: context: ..)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    TZ=Asia/Shanghai \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先装依赖(利用层缓存: 只改代码时不重装)。constraints.txt 固定为本机已验证的版本。
COPY requirements.txt constraints.txt ./
RUN pip install -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com \
    -r requirements.txt -c constraints.txt

# 代码(.env / data / secrets 已被 .dockerignore 排除, 运行时注入/挂载)
COPY . .

# 数据库等持久化数据目录(compose 会把它挂成卷)
RUN mkdir -p /app/data

CMD ["python", "scripts/run_bot.py"]
