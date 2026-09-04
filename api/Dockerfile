FROM python:3.12-slim

# pdfplumber leans on these for payslip parsing.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libjpeg62-turbo zlib1g \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kete ./kete
COPY tests ./tests

# Config and data are bind-mounted at runtime so the household can edit them
# from Windows without rebuilding the image.
ENV KETE_CONFIG_DIR=/config \
    KETE_DATA_DIR=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["uvicorn", "kete.api:app", "--host", "0.0.0.0", "--port", "8000"]
