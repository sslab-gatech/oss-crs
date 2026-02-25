FROM alpine:latest

# Install libCRS
COPY --from=libcrs . /libCRS
RUN apk add --no-cache bash python3 && /libCRS/install.sh

COPY bin/run_monitor.sh /usr/local/bin/run_monitor.sh
RUN chmod +x /usr/local/bin/run_monitor.sh

ENTRYPOINT ["/usr/local/bin/run_monitor.sh"]
