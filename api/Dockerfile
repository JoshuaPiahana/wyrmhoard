FROM python:3.12-slim

# libjpeg/zlib: pdfplumber leans on these for payslip parsing.
# git: the financial-data guard uses `git ls-files` to check exactly what
#      would be published. Without it the guard falls back to walking the
#      filesystem, which cannot tell an ignored local household.yml from a
#      committed one and so fails on correct behaviour.
RUN apt-get update \
# shellcheck: so `./hoard lint` runs exactly the checks CI runs. A local
#      command that passes while CI fails is worse than no local command.
 && apt-get install -y --no-install-recommends \
      libjpeg62-turbo zlib1g git shellcheck \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY wyrmhoard ./wyrmhoard
COPY tests ./tests

# Config and data are bind-mounted at runtime so the household can edit them
# from Windows without rebuilding the image.
ENV WYRMHOARD_CONFIG_DIR=/config \
    WYRMHOARD_DATA_DIR=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["uvicorn", "wyrmhoard.api:app", "--host", "0.0.0.0", "--port", "8000"]
