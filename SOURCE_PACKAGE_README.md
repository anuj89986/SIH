# SIH26061 complete source snapshot

This archive contains the full current local project, including Python backend,
frontend, tests, configuration, documentation, nine persisted LightGBM models,
and bundled weather/demo data. It preserves existing uncommitted work.
Local virtual environments, Git history, compiled Python, logs, and private
environment/key files are excluded. Historical results are included as prior
project artifacts; they were not regenerated for this implementation.

Use Python 3.12+ and Node.js 20+. From the extracted SIH26061 directory:

    cd engine
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    .\.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload

In another terminal:

    cd web
    node scripts/serve.mjs

Open http://127.0.0.1:3000. Swagger is http://127.0.0.1:8000/docs.
The shipped forecasts need no training on HTTP requests. Offline retraining is
engine/train_forecast.py. See review/implementation-report.md and
engine/docs/CONNECTED_MPC.md for all API fields, assumptions, tests and limits.

SOURCE_MANIFEST.json hashes every original packaged project file. Review
documents and this packaging README are additional files in the archive.
