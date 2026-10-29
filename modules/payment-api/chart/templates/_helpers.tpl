{{- define "payment-api.image" -}}
{{ .Values.image.repository }}{{ if .Values.image.digest }}@{{ .Values.image.digest }}{{ else }}:{{ .Values.image.tag }}{{ end }}
{{- end -}}

{{- define "payment-api.databaseEnv" -}}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.database.existingSecret | quote }}
      key: DATABASE_URL
{{- end -}}
