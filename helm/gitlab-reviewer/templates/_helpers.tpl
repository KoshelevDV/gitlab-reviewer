{{- define "gitlab-reviewer.fullname" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "gitlab-reviewer.labels" -}}
app.kubernetes.io/name: gitlab-reviewer
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
