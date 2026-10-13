#!/bin/bash
# 在 openKylin 目标机上原生构建 memhall .deb（源码置于 ~/memhall 后执行）
# 产物：$SRC/dist/memhall_${VERSION}_${ARCH}.deb（内置离线 wheels，安装不依赖网络）
set -euo pipefail
SRC=${SRC:-$HOME/memhall}
cd "$SRC"
SRC=$(pwd -P)
mkdir -p "$SRC/build" "$SRC/dist"
# 只使用项目下的新建临时目录，不删除用户已有的 ~/wheels 或 ~/deb-stage。
WORK=$(mktemp -d "$SRC/build/deb-work.XXXXXX")

# 版本单一来源：pyproject.toml（此前三处硬编码，已经漂移过一次）
VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
ARCH=$(dpkg --print-architecture)
PY_MINOR=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_NEXT=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor + 1}')")

# T21：依赖按 uv.lock 冻结清单安装——同版本源码任何时间构建产物一致（可复现）。
# 清单 = 源码仓 packaging/deb-wheels.txt（`uv export --frozen --no-dev --no-hashes
# --no-emit-project` 生成，含 paramiko 运行依赖）；升级依赖 = 本地重 export 后提交，
# 构建机不联网解析版本。清单缺席直接报错（宁可不构建，不悄悄漂回"当天最新"）。
WHEELS="$SRC/packaging/deb-wheels.txt"
[ -f "$WHEELS" ] || { echo "缺冻结清单 $WHEELS（uv export --frozen 生成）"; exit 1; }
W="$WORK/wheels"
mkdir -p "$W"
pip3 download -q -i https://pypi.tuna.tsinghua.edu.cn/simple -d "$W" -r "$WHEELS"
pip3 wheel -q --no-deps -i https://pypi.tuna.tsinghua.edu.cn/simple -w "$W" .

STAGE="$WORK/memhall"
mkdir -p "$STAGE/DEBIAN" "$STAGE/usr/share/memhall/scripts" "$STAGE/usr/bin" \
  "$STAGE/usr/share/applications" "$STAGE/usr/share/pixmaps" "$STAGE/usr/share/doc/memhall"
cp -r "$W" "$STAGE/usr/share/memhall/wheels"
mkdir -p "$STAGE/usr/share/memhall/cases"
cp cases/suite.yaml "$STAGE/usr/share/memhall/cases/"
for name in full gen chains challenge-v2 development-v2; do
  cp -r "cases/$name" "$STAGE/usr/share/memhall/cases/$name"
done
cp README.md LICENSE "$STAGE/usr/share/memhall/"
cp scripts/judge_selftest.py "$STAGE/usr/share/memhall/scripts/"
cp packaging/memhall.svg "$STAGE/usr/share/pixmaps/memhall.svg"

cat > "$STAGE/usr/share/applications/memhall.desktop" <<'DEOF'
[Desktop Entry]
Type=Application
Name=麟阁 MemHall
Name[en]=MemHall
GenericName=智能体记忆评测基准
GenericName[en]=Agent Memory Benchmark
Comment=教-隔-考三阶段剧本评测智能体长期记忆，输出六维能力雷达图
Exec=/usr/bin/memhall ui
Terminal=false
Categories=Development;Utility;
Icon=memhall
StartupNotify=true
DEOF

cat > "$STAGE/usr/bin/memhall" <<'WEOF'
#!/bin/sh
export PYTHONPATH=/usr/lib/memhall/pylib${PYTHONPATH:+:$PYTHONPATH}
exec python3 -c 'import sys; from memhall.cli import main; sys.exit(main())' "$@"
WEOF
chmod 755 "$STAGE/usr/bin/memhall"

cat > "$STAGE/DEBIAN/control" <<CEOF
Package: memhall
Version: $VERSION
Architecture: $ARCH
Maintainer: MemHall Team <shangjian2023@users.noreply.github.com>
Depends: python3 (>= $PY_MINOR), python3 (<< $PY_NEXT), python3-pip
Section: utils
Priority: optional
Homepage: https://github.com/shangjian2023/MemHall
Description: 麟阁 MemHall —— 面向 openKylin 生态的智能体记忆能力评测基准
 三阶段剧本（教-隔-考）驱动被测智能体，采集对话/记忆快照/动作/文件系统四类证据，
 混合判卷（规则断言 + LLM 单判）输出六维能力雷达。内置 mock 适配器可离线演示，
 hermes/kylinbot 适配器经 SSH 驱动真机评测。
CEOF

cat > "$STAGE/DEBIAN/postinst" <<'PEOF'
#!/bin/sh
set -e
pip3 install --quiet --no-index --no-deps --upgrade --break-system-packages \
  --target /usr/lib/memhall/pylib /usr/share/memhall/wheels/*.whl
PEOF

cat > "$STAGE/DEBIAN/prerm" <<'REOF'
#!/bin/sh
rm -rf /usr/lib/memhall/pylib
REOF
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/prerm"

# T22：deb 元数据达标——copyright + changelog（dpkg 标准位置）
cat > "$STAGE/usr/share/doc/memhall/copyright" <<'KEOF'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: memhall
Source: https://github.com/shangjian2023/MemHall

Files: *
Copyright: 2026 MemHall contributors
License: Apache-2.0
 On Debian systems, the full text is available at
 /usr/share/common-licenses/Apache-2.0.
KEOF

cat > "$STAGE/usr/share/doc/memhall/changelog.Debian" <<LEOF
memhall ($VERSION) unstable; urgency=medium

  * openKylin 目标机原生构建；变更明细见仓库 CHANGELOG.md
    （https://github.com/shangjian2023/MemHall/blob/main/CHANGELOG.md）

 -- MemHall Team <shangjian2023@users.noreply.github.com>  $(date -R)
LEOF
gzip -n -9 "$STAGE/usr/share/doc/memhall/changelog.Debian"
chmod 644 "$STAGE/usr/share/doc/memhall/"*

OUTPUT="$SRC/dist/memhall_${VERSION}_${ARCH}.deb"
fakeroot dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$OUTPUT" 2>/dev/null || \
  dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$OUTPUT"
ls -lh "$OUTPUT"
echo "构建工作目录保留于: $WORK"
