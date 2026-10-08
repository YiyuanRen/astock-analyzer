#!/bin/bash
# 在 ECS 上执行: 拉最新代码 -> 重新构建镜像 -> 重启容器。
# 用法(本机): ssh astock-ecs "bash /opt/astock-analyzer/scripts/ecs_deploy.sh"
set -euo pipefail
cd /opt/astock-analyzer

BRANCH="${BRANCH:-develop}"
echo "== git pull ($BRANCH) =="
git pull --ff-only origin "$BRANCH"
git log --oneline -1

[ -f .env ] || { echo "缺少 /opt/astock-analyzer/.env (需单独 scp 上传)"; exit 1; }
mkdir -p data

echo "== docker compose up -d --build =="
cd docker
docker compose up -d --build

echo "== 清理悬空镜像 =="
docker image prune -f >/dev/null

echo "== 状态 =="
docker compose ps
sleep 8
docker compose logs --tail 25
