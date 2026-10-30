FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV COLLECTOR_SERVICE_HOST=0.0.0.0
ENV COLLECTOR_SERVICE_PORT=5004

# curl dipasang bukan sekadar alat bantu: WAF JDIHN memutus TLS fingerprint milik
# OpenSSL-nya Python, sehingga permintaan halaman dilempar ke curl bawaan sistem.
# ca-certificates dibutuhkan supaya verifikasi sertifikat tetap aktif.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Server www.jdihn.go.id hanya mengirim sertifikat leaf tanpa intermediate Sectigo DV R36.
# curl Windows (Schannel) mengambil intermediate sendiri lewat AIA, tetapi curl Linux
# (OpenSSL) tidak, sehingga gagal dengan "unable to get local issuer certificate".
# Intermediate dipasang ke trust store sistem; verifikasi sertifikat tetap aktif.
COPY certs/sectigo-public-server-authentication-ca-dv-r36.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# PDF yang diunduh ditulis ke sini sebelum diunggah. Pasang volume kalau berkasnya
# perlu bertahan melewati redeploy.
RUN mkdir -p /app/downloads

EXPOSE 5004

CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "5004"]
