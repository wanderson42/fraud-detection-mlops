{{- define "fraud.name" -}}
{{- .Release.Name -}}
{{- end -}}
{{- define "fraud.labels" -}}
app.kubernetes.io/part-of: fraud-platform
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
{{- end -}}
