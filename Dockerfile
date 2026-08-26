# Playwright's own image, so Chromium and its several hundred shared libraries
# are already present and version-matched to the pip package.
FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    DISPLAY=:99 \
    TZ=Asia/Dubai \
    LA_ROOT=/app \
    LA_DATA=/data \
    LA_CHROME_PROFILE=/data/chrome-profile \
    LA_CDP=http://127.0.0.1:9222

# Xvfb   -- the virtual display; Chrome runs headful on it, never headless
# x11vnc -- exports that display so you can log in by hand
# novnc  -- puts the VNC session in a browser tab
# fluxbox-- a window manager, so the Chrome window is usable over VNC
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        x11vnc \
        novnc \
        websockify \
        fluxbox \
        supervisor \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x deploy/entrypoint.sh

VOLUME ["/data"]
EXPOSE 4300 6080

# The dashboard answers /healthz without auth specifically so this works.
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:4300/healthz',timeout=8).status==200 else 1)"

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
