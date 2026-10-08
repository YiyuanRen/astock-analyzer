#!/bin/bash
# ECS 运行时环境初始化 (Ubuntu 22.04, 阿里云镜像源)。幂等, 可重复执行。
# 用法: scp 到服务器后以 root 执行:  bash /tmp/ecs_setup.sh
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "== [1/5] 基础工具 =="
# 若 docker 尚未装成功, 清掉上次中断遗留的源文件, 避免 apt-get update 因坏镜像失败
command -v docker >/dev/null || rm -f /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y ca-certificates curl gnupg unzip git build-essential \
  software-properties-common htop tmux

echo "== [2/5] Python 3.11 (deadsnakes PPA) =="
if ! command -v python3.11 >/dev/null; then
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update -y
  apt-get install -y python3.11 python3.11-venv python3.11-dev
fi
python3.11 --version

echo "== [3/5] pip 国内镜像 =="
cat > /etc/pip.conf <<'EOF'
[global]
index-url = https://mirrors.aliyun.com/pypi/simple/
trusted-host = mirrors.aliyun.com
EOF

echo "== [4/5] Docker CE + compose 插件 (阿里云镜像) =="
install_docker_from() {  # $1 = 镜像基址 (含 /linux/ubuntu)
  local base="$1"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "$base/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] $base $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y \
    && apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}
if ! command -v docker >/dev/null; then
  # 先试阿里云镜像(重试3次, 应对镜像同步中), 失败回落官方源
  ok=0
  for i in 1 2 3; do
    if install_docker_from https://mirrors.aliyun.com/docker-ce/linux/ubuntu; then ok=1; break; fi
    echo "阿里云 docker 镜像第 $i 次失败, 20s 后重试"; sleep 20
  done
  if [ "$ok" != 1 ]; then
    echo "回落到官方源 download.docker.com"
    if install_docker_from https://download.docker.com/linux/ubuntu; then ok=1; fi
  fi
  if [ "$ok" != 1 ]; then
    # 最后兜底: Ubuntu 自带仓库的 docker.io + compose v2 (走阿里云 Ubuntu 内网源, 最稳)
    echo "回落到 Ubuntu 仓库 docker.io"
    rm -f /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker.io docker-compose-v2
  fi
fi

echo "== [5/5] Docker 日志轮转 + 开机自启 =="
mkdir -p /etc/docker
# 国内 ECS 直连 Docker Hub 不通, 需配置镜像加速(第三方公共镜像, 可用性会变, 失效时换别的)
cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" },
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://docker.1ms.run"
  ]
}
EOF
systemctl enable --now docker
systemctl restart docker

echo "== 版本汇总 =="
docker --version
docker compose version
python3.11 --version
git --version
