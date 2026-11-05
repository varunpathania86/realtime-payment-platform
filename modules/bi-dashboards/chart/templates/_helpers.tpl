{{/*
Shared helpers for deployment.yaml and setup-job.yaml: the image reference and the environment both
containers need to load superset_config.py (metadata database, signing secrets, embed origins).
*/}}
{{- define "bi-dashboards.image" -}}
{{- if .Values.image.digest -}}
{{ printf "%s@%s" .Values.image.repository .Values.image.digest }}
{{- else -}}
{{ printf "%s:%s" .Values.image.repository .Values.image.tag }}
{{- end -}}
{{- end }}

{{- define "bi-dashboards.secretEnv" -}}
- name: {{ .name }}
  valueFrom:
    secretKeyRef: {name: {{ .secret }}, key: {{ .key }}}
{{- end }}

{{- define "bi-dashboards.configEnv" -}}
- {name: SUPERSET_DATABASE_HOST, value: {{ .Values.metadataDatabase.host | quote }}}
- {name: SUPERSET_DATABASE_PORT, value: {{ .Values.metadataDatabase.port | quote }}}
- {name: SUPERSET_DATABASE_NAME, value: {{ .Values.metadataDatabase.name | quote }}}
- {name: SUPERSET_DATABASE_USER, value: {{ .Values.metadataDatabase.user | quote }}}
- {name: SUPERSET_EMBED_ORIGINS, value: {{ join " " .Values.embed.origins | quote }}}
{{- range $name, $key := dict "SUPERSET_SECRET_KEY" "secret-key" "SUPERSET_GUEST_TOKEN_SECRET" "guest-token-secret" "SUPERSET_DATABASE_PASSWORD" "database-password" }}
{{ include "bi-dashboards.secretEnv" (dict "name" $name "secret" $.Values.existingSecret "key" $key) }}
{{- end }}
{{- end }}
