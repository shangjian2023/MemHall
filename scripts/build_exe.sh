#!/usr/bin/env bash
# 麟阁 MemHall Windows 打包：
#   dist/MemHall/                onedir（本机用，启动快）
#   dist/麟阁MemHall-单文件版.exe  onefile（单文件直发微信群/网盘）
set -euo pipefail
cd "$(dirname "$0")/.."

# 2026-10-14 事故修复：禁止 rm -rf build——该目录被 build/judge-review-20261014
# 等共享产物占用，PyInstaller 工作区改用 build/exe 子目录
rm -rf build/exe dist/MemHall dist/MemHall.exe "dist/麟阁MemHall-单文件版.exe"
uv run pyinstaller --noconfirm --workpath build/exe --distpath dist memhall.spec
mkdir -p dist/MemHall/cases
cp cases/suite.yaml dist/MemHall/cases/
for name in full gen chains challenge-v2 development-v2; do
  cp -r "cases/$name" "dist/MemHall/cases/$name"
done
uv run pyinstaller --noconfirm --workpath build/exe --distpath dist memhall-onefile.spec
mv dist/MemHall.exe "dist/麟阁MemHall-单文件版.exe"

# Preserve dependency license texts alongside both distribution forms.
uv run python scripts/dependency_inventory.py --output build/exe-compliance --check
cp -r build/exe-compliance dist/MemHall/THIRD_PARTY_LICENSES
cp LICENSE dist/MemHall/LICENSE
tar -a -cf dist/THIRD_PARTY_LICENSES-windows.zip -C build/exe-compliance .
du -sh dist/MemHall "dist/麟阁MemHall-单文件版.exe"
echo "完成：onedir=dist/MemHall/ · 单文件=dist/麟阁MemHall-单文件版.exe"
