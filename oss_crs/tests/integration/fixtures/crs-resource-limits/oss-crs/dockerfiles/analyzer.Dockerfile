FROM alpine:latest

# Install libCRS
COPY --from=libcrs . /libCRS
RUN apk add --no-cache bash python3 && /libCRS/install.sh

COPY bin/run_analyzer.sh /usr/local/bin/run_analyzer.sh
RUN chmod +x /usr/local/bin/run_analyzer.sh

ENTRYPOINT ["/usr/local/bin/run_analyzer.sh"]
