{{/*
Shared environment snippets used by deployment.yaml and setup-job.yaml (renders no resource).
*/}}
{{- define "lakehouse-catalog.databaseEnv" -}}
- {name: QUARKUS_DATASOURCE_JDBC_URL, value: {{ .Values.database.url | quote }}}
- name: QUARKUS_DATASOURCE_USERNAME
  valueFrom:
    secretKeyRef: {name: {{ .Values.database.existingSecret }}, key: username}
- name: QUARKUS_DATASOURCE_PASSWORD
  valueFrom:
    secretKeyRef: {name: {{ .Values.database.existingSecret }}, key: password}
{{- end }}

{{- define "lakehouse-catalog.storageEnv" -}}
- {name: AWS_REGION, value: {{ .Values.storage.region | quote }}}
- name: AWS_ACCESS_KEY_ID
  valueFrom:
    secretKeyRef: {name: {{ .Values.storage.existingSecret }}, key: access-key}
- name: AWS_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef: {name: {{ .Values.storage.existingSecret }}, key: secret-key}
{{- end }}
