# Distro pinned (bookworm) so the WeasyPrint system libs below don't drift under us (§5).
FROM python:3.12-slim-bookworm

# WeasyPrint runtime: Pango, Cairo, GDK-PixBuf, HarfBuzz + fonts.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libpangocairo-1.0-0 libharfbuzz0b \
        libcairo2 libgdk-pixbuf-2.0-0 libffi8 shared-mime-info fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY . .
# Fail the build now, not on day 12, if WeasyPrint can't find its libs.
RUN python -c "import weasyprint; weasyprint.HTML(string='<p>ok</p>').write_pdf()"
