FROM node:22-bookworm-slim AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build


FROM python:3.12-slim

ARG PIPER_VOICE_REV=8aaa3c9839d2b669cb57a94e1ec92ae0928897e8
ARG PIPER_MODEL_SHA256=9df1c43c61149ef9b39e618e2b861fbe41e1fcea9390b2dac62e8761573ea4f1
ARG PIPER_VOICE=de_DE-thorsten-high

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIPER_VOICE_PATH=/opt/piper/de_DE-thorsten-high.onnx

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates curl libespeak-ng1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY --from=frontend-build /app/frontend ./app/frontend
RUN pip install . \
    && pip install piper-tts==1.6.0

# The revision, digest and model-card fetch are all explicit so a changed voice
# artifact cannot silently enter the runtime image.
RUN mkdir -p /opt/piper /usr/share/doc/flashcard \
    && python -c "import importlib.metadata as m; license_value=m.metadata('piper-tts').get('License'); assert license_value == 'GPL-3.0-or-later', license_value; print(license_value)" \
       > /usr/share/doc/flashcard/PIPER-ENGINE-LICENSE \
    && voice_url="https://huggingface.co/rhasspy/piper-voices/resolve/${PIPER_VOICE_REV}/de/de_DE/thorsten/high" \
    && curl --fail --location --silent --show-error "${voice_url}/${PIPER_VOICE}.onnx" \
       --output "${PIPER_VOICE_PATH}" \
    && echo "${PIPER_MODEL_SHA256}  ${PIPER_VOICE_PATH}" | sha256sum --check --status \
    && curl --fail --location --silent --show-error "${voice_url}/${PIPER_VOICE}.onnx.json" \
       --output "${PIPER_VOICE_PATH}.json" \
    && curl --fail --location --silent --show-error "${voice_url}/MODEL_CARD" \
       --output /usr/share/doc/flashcard/THORSTEN-MODEL-CARD \
    && grep --fixed-strings --quiet 'License: CC0' /usr/share/doc/flashcard/THORSTEN-MODEL-CARD \
    && curl --fail --location --silent --show-error \
       "https://huggingface.co/api/models/rhasspy/piper-voices/revision/${PIPER_VOICE_REV}" \
       --output /usr/share/doc/flashcard/PIPER-VOICE-REPOSITORY-METADATA.json \
    && python -c "import json; p='/usr/share/doc/flashcard/PIPER-VOICE-REPOSITORY-METADATA.json'; value=json.load(open(p, encoding='utf-8')); assert value.get('sha') == '${PIPER_VOICE_REV}', value.get('sha'); assert value.get('cardData', {}).get('license') == 'mit', value.get('cardData')" \
    && printf '%s\n' \
       "Piper engine: $(cat /usr/share/doc/flashcard/PIPER-ENGINE-LICENSE) (installed piper-tts==1.6.0 metadata)" \
       "Piper voices repository metadata: MIT (pinned revision ${PIPER_VOICE_REV} API metadata)" \
       'Thorsten-Voice dataset/model card: CC0 (pinned MODEL_CARD above)' \
       "Voice: ${PIPER_VOICE}; source revision: ${PIPER_VOICE_REV}" \
       "Model SHA-256: ${PIPER_MODEL_SHA256}" \
       > /usr/share/doc/flashcard/PIPER-NOTICES \
    && piper --help >/dev/null

# Bounded build smoke: no cache/database/media is baked beyond the selected
# pinned voice, and no LLM SDK is installed in this runtime dependency graph.
RUN printf 'Guten Tag.' | piper --model "${PIPER_VOICE_PATH}" --output_file /tmp/piper-smoke.wav \
    && test -s /tmp/piper-smoke.wav \
    && rm /tmp/piper-smoke.wav \
    && ! pip freeze | grep -E '^(anthropic|openai|google-genai)=='

# Dictionary assets and user state are deliberately independent mounts.  Run
# with /dictionary mounted read-only and /data mounted read-write.
VOLUME ["/dictionary", "/data"]

CMD ["uvicorn", "app.api:create_production_app", "--factory", "--host", "127.0.0.1", "--port", "8000"]
