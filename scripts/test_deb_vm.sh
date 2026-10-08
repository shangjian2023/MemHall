#!/bin/bash
# 在干净 openKylin VM 上安装构建产物 deb 并走离线验收路径。
# （择优移植自 leeyu44 PR #3：--repeat 换成主线两次独立 run；
#   .build.json sidecar 为可选——主线构建不产出，存在时才校验哈希。）
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 /path/to/memhall_VERSION_ARCH.deb" >&2
  exit 2
fi
DEB="$(readlink -f "$1")"
[[ -f "$DEB" ]] || { echo "deb not found: $DEB" >&2; exit 2; }
ACTUAL_SHA256="$(sha256sum "$DEB" | awk '{print $1}')"
META="${DEB%.deb}.build.json"
if [[ -f "$META" ]]; then
  EXPECTED_SHA256="$(python3 - "$META" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["deb_sha256"])
PY
)"
  if [[ "$EXPECTED_SHA256" != "$ACTUAL_SHA256" ]]; then
    echo "deb SHA-256 does not match build metadata" >&2
    exit 2
  fi
  echo "build metadata hash: OK"
else
  echo "note: no .build.json sidecar, skipping build-metadata hash check"
fi
PACKAGE_ARCH="$(dpkg-deb -f "$DEB" Architecture)"
HOST_ARCH="$(dpkg --print-architecture)"
if [[ "$PACKAGE_ARCH" != "$HOST_ARCH" ]]; then
  echo "package architecture $PACKAGE_ARCH does not match host $HOST_ARCH" >&2
  exit 2
fi
REPORT="${REPORT:-/tmp/memhall-deb-acceptance.txt}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=()
else
  SUDO=(sudo)
fi

{
  echo "deb=$DEB"
  echo "deb_sha256=$ACTUAL_SHA256"
  echo "started_at=$(date --iso-8601=seconds)"
  "${SUDO[@]}" dpkg -i "$DEB"
  memhall --version
  cd "$WORK"
  # 两次独立 run（离线 mock + quick 规格）→ 稳定性与完整性验收
  memhall run -a mock -c quick -o runs
  memhall run -a mock -c quick -o runs
  mapfile -t RUNS < <(find runs -mindepth 2 -maxdepth 2 -type f \
    -name manifest.json -printf '%h\n' | sort)
  [[ "${#RUNS[@]}" -eq 2 ]] || {
    echo "expected 2 completed runs, found ${#RUNS[@]}" >&2
    exit 1
  }
  memhall verify "${RUNS[0]}"
  memhall verify "${RUNS[1]}"
  memhall stability "${RUNS[0]}" "${RUNS[1]}" -o runs/_stability-deb
  test -s runs/_stability-deb/stability.json
  echo "package_status=$(dpkg-query -W -f='${Status}' memhall)"
  echo "finished_at=$(date --iso-8601=seconds)"
  echo "result=PASS"
} 2>&1 | tee "$REPORT"

echo "acceptance report: $REPORT"
