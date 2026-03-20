#!/bin/bash
# 掃描 VRMA 目錄，生成 JSON 清單供前端讀取
VRMA_DIR="$(dirname "$0")/../public/vrm3d/vrm-viewer-main/VRMA"
OUTPUT="$(dirname "$0")/../public/vrm3d/vrm-viewer-main/VRMA/manifest.json"

cd "$VRMA_DIR" || exit 1

# 列出所有 .vrma 檔案，輸出 JSON 陣列
echo -n '[' > manifest.json
first=true
for f in *.vrma; do
  [ -f "$f" ] || continue
  if [ "$first" = true ]; then
    first=false
  else
    echo -n ',' >> manifest.json
  fi
  echo -n "\"$f\"" >> manifest.json
done
echo ']' >> manifest.json

echo "Generated: $OUTPUT"
cat manifest.json
