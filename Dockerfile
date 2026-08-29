# ════════════════════════════════════════════════════════════
#   𝑨𝒑𝒆𝒙 𝑽𝒊𝒃𝒆𝒔 .ᐟ.ᐟ 🎧  —  Docker Image
#   Owner  : @TheY_CaIl_mE_OG
#   Bot    : @ApexVibesBot
# ════════════════════════════════════════════════════════════

FROM nikolaik/python-nodejs:python3.10-nodejs18

# Install system dependencies
RUN apt-get update -y && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        wget \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
COPY . /app/
WORKDIR /app/

# Install Python dependencies
RUN pip3 install --no-cache-dir --upgrade pip \
    && pip3 install --no-cache-dir --upgrade --requirement requirements.txt

# Start bot
CMD bash start
