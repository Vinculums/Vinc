#!/bin/bash
# vfs 복사 비용을 줄이려고 큰 도구는 검증된 이미지 안에 압축해서 둔다.
# 첫 호출 때 이 잡의 파일시스템에만 풀며, 네트워크로 재설치하지 않는다.
set -euo pipefail
tool=${0##*/}
case "$tool" in
  az)
    if [ ! -x /opt/azure-cli/bin/az ]; then
      sudo /bin/bash -c 'exec 9>/opt/azure-cli.lock; flock 9; if [ ! -x /opt/azure-cli/bin/az ]; then tar -xJf /opt/tool-archives/azure-cli.tar.xz -C /opt; fi'
    fi
    exec /opt/azure-cli/bin/az "$@"
    ;;
  cargo|rustc|rustup)
    if [ ! -d "$HOME/.rustup/toolchains" ]; then
      (
        flock 9
        if [ ! -d "$HOME/.rustup/toolchains" ]; then
          tar -xJf /opt/tool-archives/rustup.tar.xz -C "$HOME"
        fi
      ) 9>"$HOME/.rustup-bootstrap.lock"
    fi
    exec "$HOME/.cargo/bin/$tool" "$@"
    ;;
  *) echo 'Unsupported CI tool' >&2; exit 1 ;;
esac
