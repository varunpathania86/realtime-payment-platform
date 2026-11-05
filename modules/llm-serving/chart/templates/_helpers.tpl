{{/*
Shared snippets for deployment.yaml: the image reference and the restricted container security
context used by the fetch init container and both llama.cpp servers.
*/}}
{{- define "llm-serving.image" -}}
{{- if .Values.image.digest -}}
{{ printf "%s@%s" .Values.image.repository .Values.image.digest }}
{{- else -}}
{{ printf "%s:%s" .Values.image.repository .Values.image.tag }}
{{- end -}}
{{- end }}

{{- define "llm-serving.securityContext" -}}
readOnlyRootFilesystem: true
allowPrivilegeEscalation: false
capabilities: {drop: ["ALL"]}
{{- end }}
