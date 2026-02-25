ARG target_base_image
FROM $target_base_image

# Install libCRS
COPY --from=libcrs . /libCRS
RUN /libCRS/install.sh

COPY bin/build.sh /usr/local/bin/build.sh
RUN chmod +x /usr/local/bin/build.sh

CMD ["/usr/local/bin/build.sh"]
