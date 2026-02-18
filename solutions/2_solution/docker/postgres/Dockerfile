FROM postgres:17.6-bookworm

RUN apt-get update \
    && apt-get install --yes --no-install-recommends postgresql-17-partman \
    && rm -rf /var/lib/apt/lists/*
