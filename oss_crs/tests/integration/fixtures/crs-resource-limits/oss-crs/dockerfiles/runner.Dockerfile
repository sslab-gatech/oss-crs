FROM alpine:latest

# Install libCRS
COPY --from=libcrs . /libCRS
RUN apk add --no-cache bash python3 && /libCRS/install.sh

COPY bin/run_fuzzer.sh /usr/local/bin/run_fuzzer.sh
RUN chmod +x /usr/local/bin/run_fuzzer.sh

ENTRYPOINT ["/usr/local/bin/run_fuzzer.sh"]
