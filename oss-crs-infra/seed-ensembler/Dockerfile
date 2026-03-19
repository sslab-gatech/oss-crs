ARG target_base_image
FROM ${target_base_image}

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-venv rsync \
    && rm -rf /var/lib/apt/lists/*

COPY . /opt/seed-ensembler
RUN pip3 install --break-system-packages /opt/seed-ensembler

CMD ["python3", "-u", "-m", "seed_ensembler"]
