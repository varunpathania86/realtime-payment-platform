ARG KEYCLOAK_VERSION
ARG KEYCLOAK_DIGEST
FROM quay.io/keycloak/keycloak:${KEYCLOAK_VERSION}@${KEYCLOAK_DIGEST}
RUN /opt/keycloak/bin/kc.sh build --db=postgres --health-enabled=true
USER 1000
ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
CMD ["start", "--optimized", "--import-realm"]
