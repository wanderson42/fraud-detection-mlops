{{- define "obs.labels" -}}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: fraud-platform
{{- end -}}
{{- define "obs.owner" -}}
fraud.observability/owner-id: {{ .Values.ownerId | quote }}
{{- end -}}
