#!/bin/bash
# 在 openKylin 目标机上原生构建 memhall .deb（源码置于 ~/memhall 后执行）
# 产物：~/deb-stage/memhall_${VERSION}_all.deb（内置离线 wheels，安装不依赖网络）
set -e
SRC=${SRC:-$HOME/memhall}
cd $SRC

# 版本单一来源：pyproject.toml（此前三处硬编码，已经漂移过一次）
VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")

W=~/wheels
rm -rf $W && mkdir -p $W
pip3 download -q -i https://pypi.tuna.tsinghua.edu.cn/simple -d $W \
  pydantic pyyaml matplotlib paramiko fastapi uvicorn httpx
pip3 wheel -q --no-deps -i https://pypi.tuna.tsinghua.edu.cn/simple -w $W .

STAGE=~/deb-stage/memhall
rm -rf ~/deb-stage
mkdir -p $STAGE/DEBIAN $STAGE/usr/share/memhall/scripts $STAGE/usr/bin \
  $STAGE/usr/share/applications $STAGE/usr/share/pixmaps
cp -r $W $STAGE/usr/share/memhall/wheels
cp -r cases $STAGE/usr/share/memhall/cases
cp README.md LICENSE $STAGE/usr/share/memhall/
cp scripts/judge_selftest.py $STAGE/usr/share/memhall/scripts/
cp packaging/memhall.svg $STAGE/usr/share/pixmaps/memhall.svg

cat > $STAGE/usr/share/applications/memhall.desktop <<'DEOF'
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

cat > $STAGE/usr/bin/memhall <<'WEOF'
#!/bin/sh
export PYTHONPATH=/usr/lib/memhall/pylib${PYTHONPATH:+:$PYTHONPATH}
exec python3 -c 'import sys; from memhall.cli import main; sys.exit(main())' "$@"
WEOF
chmod 755 $STAGE/usr/bin/memhall

cat > $STAGE/DEBIAN/control <<CEOF
Package: memhall
Version: $VERSION
Architecture: all
Maintainer: MemHall Team <shangjian2023@users.noreply.github.com>
Depends: python3 (>= 3.11)
Section: utils
Priority: optional
Homepage: https://gitee.com/mazhuoran23/MemHall
Description: 麟阁 MemHall —— 面向 openKylin 生态的智能体记忆能力评测基准
 三阶段剧本（教-隔-考）驱动被测智能体，采集对话/记忆快照/动作/文件系统四类证据，
 混合判卷（规则断言 + LLM 单判）输出六维能力雷达。内置 mock 适配器可离线演示，
 hermes/kylinbot 适配器经 SSH 驱动真机评测。
CEOF

cat > $STAGE/DEBIAN/postinst <<'PEOF'
#!/bin/sh
set -e
pip3 install --quiet --no-index --no-deps --upgrade --break-system-packages \
  --target /usr/lib/memhall/pylib /usr/share/memhall/wheels/*.whl
PEOF

cat > $STAGE/DEBIAN/prerm <<'REOF'
#!/bin/sh
rm -rf /usr/lib/memhall/pylib
REOF
chmod 755 $STAGE/DEBIAN/postinst $STAGE/DEBIAN/prerm

# T22：deb 元数据达标——copyright + changelog（dpkg 标准位置）
cat > $STAGE/DEBIAN/copyright <<'KEOF'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: memhall
Source: https://github.com/shangjian2023/MemHall

Files: *
Copyright: 2026 MemHall contributors
License: Apache-2.0
 On Debian systems, the full text is available at
 /usr/share/common-licenses/Apache-2.0.
KEOF

cat > $STAGE/DEBIAN/changelog <<LEOF
memhall (\$VERSION) unstable; urgency=medium

  * openKylin 目标机原生构建；变更明细见仓库 CHANGELOG.md
    （https://github.com/shangjian2023/MemHall/blob/dev/CHANGELOG.md）

 -- MemHall Team <shangjian2023@users.noreply.github.com>  \$(date -R)
LEOF
chmod 644 $STAGE/DEBIAN/copyright $STAGE/DEBIAN/changelog

cd ~/deb-stage
fakeroot dpkg-deb --root-owner-group -Zxz --build memhall memhall_${VERSION}_all.deb 2>/dev/null || dpkg-deb -Zxz --build memhall memhall_${VERSION}_all.deb
ls -lh memhall_${VERSION}_all.deb
