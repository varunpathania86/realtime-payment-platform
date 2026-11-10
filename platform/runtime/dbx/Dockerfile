ARG DBX_VERSION
ARG DBX_DIGEST
FROM t8y2/dbx:${DBX_VERSION}@${DBX_DIGEST}
# This is an offline-import payload, not a copy of DBX's private driver state.
# The connection-sync Job invokes DBX's own importer to install it on the PVC.
COPY runtime.zip /opt/dbx/runtime.zip
# Match the Kubernetes pod identity; the PVC and tmp volume provide writable state.
USER 10001:10001
