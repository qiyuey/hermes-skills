#!/bin/bash
# 公司 Model 平台 TTS - qwen3-tts-flash
# 用法: tts.sh <input_text_file> <output_audio_file>
#
# 这个脚本由 hermes 的 tts.providers.company-tts.command 触发. 跟同目录下的
# __init__.py / image_gen.py 一起组成完整的 "公司 Model 平台" 插件.
INPUT_FILE="$1"
OUTPUT_FILE="$2"
TEXT=$(cat "$INPUT_FILE")
API_KEY="${COMPANY_MODEL_API_KEY}"
curl -s -X POST "https://model.zhenguanyu.com/v1/audio/speech" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"qwen3-tts-flash\",\"input\":$(echo "$TEXT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),\"voice\":\"Cherry\"}" \
  -o "$OUTPUT_FILE"
