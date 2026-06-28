ARG BASE_IMAGE=scratch

# 1. Aşama: Statik FFmpeg kopyala (Hızlı ve temiz)
FROM mwader/static-ffmpeg:5.0 AS ffmpeg-source

FROM ${BASE_IMAGE}

# FFmpeg dosyalarını hazır olarak içeri aktar (Saniyeler sürer)
COPY --from=ffmpeg-source /ffmpeg /usr/local/bin/
COPY --from=ffmpeg-source /ffprobe /usr/local/bin/

# 2. Sistem Bağımlılıkları (ffmpeg buradan kaldırıldı)
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    python3-dev \
    libsndfile1 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 3. Bağımlılıkları Önceden Kur (Genel API Bağımlılıkları)
COPY pyproject.toml /app/
RUN pip3 install --no-cache-dir uv && \
    uv pip install --system -r pyproject.toml

# 4. Motor Kurulumu (Hibrit Önbellek)
ARG ENGINE_NAME

# 4.1. Sadece yapılandırma dosyasını kopyala
COPY ./${ENGINE_NAME}/pyproject.toml /app/src/

# 4.2. Sadece bağımlılıkları toml üzerinden kur (Çok hızlı ve basit)
RUN uv pip install --system -r /app/src/pyproject.toml

# 4.3. Motor kodlarını kopyala (Seçili motora göre dinamik)
COPY ./${ENGINE_NAME} /app/src/
COPY . /app/

# 4.4. Kodu sisteme bağla
RUN uv pip install --system --no-deps /app/src/

# 5. Çalıştırma Komutu
ENV START_COMMAND=""
CMD sh -c "${START_COMMAND}"
