{{/*
Image reference shared by cronjob.yaml: a pinned digest when publication supplies one, otherwise the
locally loaded tag.
*/}}
{{- define "operations-knowledge.image" -}}
{{- if .Values.image.digest -}}
{{ printf "%s@%s" .Values.image.repository .Values.image.digest }}
{{- else -}}
{{ printf "%s:%s" .Values.image.repository .Values.image.tag }}
{{- end -}}
{{- end }}
