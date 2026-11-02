{{- define "payment-api.image" -}}
{{ .Values.image.repository }}{{ if .Values.image.digest }}@{{ .Values.image.digest }}{{ else }}:{{ .Values.image.tag }}{{ end }}
{{- end -}}

{{- define "payment-api.databaseMount" -}}
{{- if .Values.database.tls }}
volumeMounts:
  - name: database-ca
    mountPath: /etc/payment-database
    readOnly: true
{{- end }}
{{- end -}}

{{- define "payment-api.databaseVolume" -}}
{{- if .Values.database.tls }}
- name: database-ca
  secret:
    secretName: {{ .Values.database.existingSecret | quote }}
    items:
      - key: ca.crt
        path: ca.crt
{{- end }}
{{- end -}}

{{- define "payment-api.databaseEnv" -}}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.database.existingSecret | quote }}
      key: DATABASE_URL
{{- end -}}
