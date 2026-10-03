# ════════════════════════════════════════════════════════════
#   𝑨𝒑𝒆𝒙 𝑽𝒊𝒃𝒆𝒔 .ᐟ.ᐟ 🎧  —  Docker Image
#   Owner  : @TheY_CaIl_mE_OG
#   Bot    : @ApexVibesBot
# ════════════════════════════════════════════════════════════

FROM nikolaik/python-nodejs:python3.11-nodejs18

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

# The Heroku Python buildpack runs this hook automatically, but Docker does
# not. Without it yt-dlp has no Deno/bgutil proof-of-origin provider and cloud
# deployments hit YouTube's "Sign in to confirm you're not a bot" wall.
RUN chmod +x bin/post_compile && bin/post_compile /app

# Start bot
CMD bash start


# ============ WARP-plus for YouTube IP Block Bypass ============
