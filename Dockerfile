# The LiDAR tier needs only the base install:
#
#     docker build -t cozmo .
#     docker run --rm -v /path/to/stray_export:/capture -v "$PWD/runs:/runs" cozmo run --input /capture --out /runs/plan
#
# The photo and video tiers also need the ml extra and the depth weights that scripts/fetch_weights.sh
# puts in weights/:
#
#     docker build --build-arg EXTRAS=dev,ml -t cozmo-ml .
#     docker run --rm -v "$PWD/weights:/app/weights" -v /path/to/photos:/capture -v "$PWD/runs:/runs" \
#         cozmo-ml run --input /capture --out /runs/plan
FROM python:3.11-slim

# opencv-python loads libGL and GLib when it is imported, scikit-learn and OpenCV use OpenMP, and git
# lets the run manifest record which commit produced a plan.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --global --add safe.directory /app

WORKDIR /app
ARG EXTRAS=dev
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[${EXTRAS}]"
COPY . .

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["cozmo"]
CMD ["--help"]
