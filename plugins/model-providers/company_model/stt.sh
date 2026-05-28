#!/bin/bash
# 公司 Model 平台 ASR - qwen3-asr-flash
# 用法: stt.sh <input_audio_file> <output_text_file>
#
# 这个脚本由 hermes 的 stt.providers.company-asr.command 触发. 跟同目录下的
# __init__.py / image_gen.py / tts.sh 一起组成完整的 "公司 Model 平台" 插件.
#
# 接口: POST /v1/audio/transcriptions (multipart/form-data, OpenAI-compatible)
#   - 上传字段 file: 音频文件 (wav/mp3/m4a/ogg/...)
#   - 模型字段 model: qwen3-asr-flash
#   - 返回: {"text": "<transcript>"}
#
# 我们提取 .text 字段写入 output_path. hermes 的 _read_command_stt_output
# 会读这个文件返回给上层. 失败时把整个 JSON 报错写出去, 让用户能看到原因.
set -euo pipefail

INPUT_FILE="$1"
OUTPUT_FILE="$2"
API_KEY="${COMPANY_MODEL_API_KEY}"

if [[ -z "${API_KEY}" ]]; then
    echo "COMPANY_MODEL_API_KEY not set" >&2
    exit 1
fi

if [[ ! -s "${INPUT_FILE}" ]]; then
    echo "Input audio file empty or missing: ${INPUT_FILE}" >&2
    exit 1
fi

RESPONSE=$(curl -s --max-time 120 \
    -X POST "https://model.zhenguanyu.com/v1/audio/transcriptions" \
    -H "Authorization: Bearer ${API_KEY}" \
    -F "model=qwen3-asr-flash" \
    -F "file=@${INPUT_FILE}")

# 提取 .text 字段; 没有就把原始响应写出去给用户排错.
TRANSCRIPT=$(echo "${RESPONSE}" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception as e:
    print(f"<parse-error: {e}>", file=sys.stderr)
    sys.exit(2)
text = data.get("text")
if isinstance(text, str) and text:
    print(text, end="")
else:
    print(json.dumps(data, ensure_ascii=False), file=sys.stderr)
    sys.exit(3)
')

if [[ -z "${TRANSCRIPT}" ]]; then
    echo "Empty transcript from qwen3-asr-flash" >&2
    echo "${RESPONSE}" >&2
    exit 1
fi

printf '%s' "${TRANSCRIPT}" > "${OUTPUT_FILE}"
